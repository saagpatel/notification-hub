"""Tests for durable inbox persistence and retry/dead-letter lifecycle."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from notification_hub.durable_inbox import (
    claim_next_due_event,
    collect_health,
    enqueue_event,
    get_event,
    init_schema,
    mark_delivered,
    reclaim_stale_processing,
    record_processing_failure,
    retry_delay_seconds,
)
from notification_hub.models import StoredEvent


def _event(event_id: str = "evt1") -> StoredEvent:
    return StoredEvent(
        event_id=event_id,
        source="codex",
        level="info",
        title="Durable inbox test",
        body="Persist me before ack.",
        project="notification-hub",
        classified_level="info",
    )


def test_init_schema_creates_sqlite_database(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"

    init_schema(db_path)

    assert db_path.exists()
    assert collect_health(path=db_path)["status"] == "ok"


def test_enqueue_event_is_idempotent_by_event_id(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _event("stable-id")

    first = enqueue_event(event, path=db_path)
    second = enqueue_event(event, path=db_path)

    assert first.event_id == "stable-id"
    assert second.event_id == "stable-id"
    assert collect_health(path=db_path)["queued_count"] == 1


def test_claim_and_mark_processed_transition(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event(), path=db_path)

    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    assert claimed.status == "processing"
    assert claimed.attempt_count == 1

    mark_delivered(
        claimed.event_id,
        outcome="processed",
        classified_level=claimed.event.classified_level,
        path=db_path,
    )

    stored = get_event(claimed.event_id, path=db_path)
    assert stored is not None
    assert stored.status == "processed"
    assert collect_health(path=db_path)["processed_count"] == 1


def test_reclaim_stale_processing_lease(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event(), path=db_path)
    claimed = claim_next_due_event(path=db_path, lease_seconds=-1)
    assert claimed is not None

    reclaimed = reclaim_stale_processing(path=db_path)

    assert reclaimed == 1
    stored = get_event(claimed.event_id, path=db_path)
    assert stored is not None
    assert stored.status == "retry_scheduled"


def test_retry_backoff_schedules_transient_failure(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event(), path=db_path, max_attempts=5)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None

    status = record_processing_failure(claimed, RuntimeError("temporary"), path=db_path)

    stored = get_event(claimed.event_id, path=db_path)
    assert status == "retry_scheduled"
    assert stored is not None
    assert stored.status == "retry_scheduled"
    assert stored.last_error_type == "RuntimeError"
    assert stored.next_attempt_at is not None
    assert retry_delay_seconds(1) == 5


def test_max_attempt_failure_moves_to_dead_letter(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event(), path=db_path, max_attempts=1)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None

    status = record_processing_failure(claimed, RuntimeError("permanent"), path=db_path)

    stored = get_event(claimed.event_id, path=db_path)
    assert status == "dead_lettered"
    assert stored is not None
    assert stored.status == "dead_lettered"
    assert stored.dead_lettered_at is not None
    health = collect_health(path=db_path)
    assert health["status"] == "degraded"
    assert health["dead_letter_count"] == 1
    assert health["recent_dead_letter_count"] == 1


def test_old_dead_letters_remain_visible_without_degrading_health(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event(), path=db_path, max_attempts=1)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    record_processing_failure(claimed, RuntimeError("old failure"), path=db_path)
    old_dead_lettered_at = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE durable_events
            SET dead_lettered_at = ?, updated_at = ?
            WHERE event_id = ?
            """,
            (old_dead_lettered_at, old_dead_lettered_at, claimed.event_id),
        )

    health = collect_health(path=db_path)

    assert health["status"] == "ok"
    assert health["dead_letter_count"] == 1
    assert health["recent_dead_letter_count"] == 0
    assert "historical dead letters" in health["next_action"]


def test_suppressed_event_is_persisted_as_terminal_state(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event(), path=db_path)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None

    mark_delivered(
        claimed.event_id,
        outcome="suppressed",
        classified_level=claimed.event.classified_level,
        path=db_path,
    )

    health = collect_health(path=db_path)
    stored = get_event(claimed.event_id, path=db_path)
    assert stored is not None
    assert stored.status == "suppressed"
    assert health["suppressed_count"] == 1


def test_health_degrades_for_stale_backlog(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("old"), path=db_path)
    old_created_at = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE durable_events SET created_at = ? WHERE event_id = ?",
            (old_created_at, "old"),
        )

    health = collect_health(path=db_path)

    assert health["status"] == "degraded"
    assert health["oldest_pending_age_seconds"] is not None
