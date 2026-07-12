"""Tests for durable inbox persistence and retry/dead-letter lifecycle."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from notification_hub.durable_inbox import (
    IdempotencyConflictError,
    accepted_channels,
    channel_state_counts,
    claim_next_due_event,
    collect_health,
    disposition_dead_letter,
    enqueue_event,
    get_channel_receipts,
    get_event,
    init_schema,
    mark_delivered,
    prune_retained_events,
    reclaim_stale_processing,
    record_channel_state,
    record_processing_deferred,
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


def test_retry_with_new_server_default_timestamp_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    first = _event("stable-id")
    enqueue_event(first, path=db_path)
    retry = _event("stable-id").model_copy(
        update={"timestamp": first.timestamp + timedelta(seconds=5)}
    )

    accepted = enqueue_event(retry, path=db_path)

    assert accepted.timestamp == first.timestamp
    assert collect_health(path=db_path)["queued_count"] == 1


def test_enqueue_event_rejects_conflicting_payload_for_same_event_id(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("stable-id"), path=db_path)
    conflicting = _event("stable-id").model_copy(update={"body": "Different payload"})

    with pytest.raises(IdempotencyConflictError, match="different payload digest"):
        enqueue_event(conflicting, path=db_path)

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


def test_channel_state_tracks_attempt_and_acceptance_without_claiming_delivery(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("channel-state"), path=db_path)

    record_channel_state("channel-state", "slack", "attempted", path=db_path)
    record_channel_state("channel-state", "slack", "accepted", path=db_path)

    assert accepted_channels("channel-state", path=db_path) == frozenset({"slack"})
    assert channel_state_counts(path=db_path) == {"accepted": 1}
    assert collect_health(path=db_path)["delivery_state_counts"] == {
        "accepted": 1,
        "attempted": 1,
    }


def test_channel_failure_is_not_treated_as_accepted(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("channel-failure"), path=db_path)

    record_channel_state("channel-failure", "push", "attempted", path=db_path)
    record_channel_state(
        "channel-failure", "push", "failed", path=db_path, error_category="timeout"
    )

    assert accepted_channels("channel-failure", path=db_path) == frozenset()
    assert channel_state_counts(path=db_path) == {"failed": 1}


def test_channel_receipts_remain_distinct_across_lifecycle(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("receipt-lifecycle"), path=db_path)
    record_channel_state(
        "receipt-lifecycle", "slack", "accepted", path=db_path, destination_ref="ack:1"
    )
    record_channel_state(
        "receipt-lifecycle", "slack", "delivered", path=db_path, destination_ref="readback:1"
    )
    record_channel_state(
        "receipt-lifecycle", "slack", "observed", path=db_path, destination_ref="operator:1"
    )

    receipts = get_channel_receipts("receipt-lifecycle", "slack", path=db_path)
    assert receipts["acceptance_receipt"] == "ack:1"
    assert receipts["delivery_receipt"] == "readback:1"
    assert receipts["observation_receipt"] == "operator:1"


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


def test_durable_deferral_survives_restart_without_consuming_attempt_budget(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("deferred-event"), path=db_path)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    retry_at = datetime.now(UTC) + timedelta(hours=2)

    record_processing_deferred(claimed, retry_at, path=db_path)

    stored = get_event("deferred-event", path=db_path)
    assert stored is not None
    assert stored.status == "retry_scheduled"
    assert stored.attempt_count == 0
    assert stored.next_attempt_at == retry_at.isoformat()
    assert claim_next_due_event(path=db_path) is None


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


def test_old_unresolved_dead_letters_continue_degrading_health(tmp_path: Path) -> None:
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

    assert health["status"] == "degraded"
    assert health["dead_letter_count"] == 1
    assert health["recent_dead_letter_count"] == 0
    assert "disposition every unresolved" in health["next_action"]


def test_dead_letter_disposition_clears_actionable_health_without_deleting_history(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("dead-history"), path=db_path, max_attempts=1)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    record_processing_failure(claimed, RuntimeError("permanent"), path=db_path)

    disposition_dead_letter("dead-history", "operator_reviewed", "ticket:test-1", path=db_path)

    health = collect_health(path=db_path)
    assert health["status"] == "ok"
    assert health["dead_letter_count"] == 1
    assert health["unresolved_dead_letter_count"] == 0
    assert get_event("dead-history", path=db_path) is not None


def test_schema_migration_is_additive_and_preserves_existing_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    event = _event("legacy-row")
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE durable_events (
                event_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 5,
                next_attempt_at TEXT, lease_until TEXT, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, processed_at TEXT, dead_lettered_at TEXT,
                last_error TEXT, last_error_type TEXT, source TEXT NOT NULL, project TEXT,
                level TEXT NOT NULL, classified_level TEXT, title TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO durable_events "
            "(event_id, payload_json, status, created_at, updated_at, source, level, title) "
            "VALUES (?, ?, 'queued', ?, ?, ?, ?, ?)",
            (
                event.event_id,
                event.model_dump_json(),
                now,
                now,
                event.source,
                event.level,
                event.title,
            ),
        )

    init_schema(db_path)

    assert get_event("legacy-row", path=db_path) is not None
    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(durable_events)")}
        channel_columns = {row[1] for row in conn.execute("PRAGMA table_info(channel_deliveries)")}
        version = conn.execute(
            "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
        ).fetchone()
    assert {
        "payload_digest",
        "dead_letter_disposition",
        "dead_letter_disposition_ref",
        "dead_letter_dispositioned_at",
    } <= columns
    assert {
        "acceptance_receipt",
        "delivery_receipt",
        "observation_receipt",
        "terminal_disposition",
        "backoff_until",
    } <= channel_columns
    assert version == ("5",)


def test_retention_preserves_delivery_history_and_unresolved_dead_letters(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    old = datetime.now(UTC) - timedelta(days=120)
    enqueue_event(_event("processed-with-receipt"), path=db_path)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    record_channel_state(claimed.event_id, "slack", "accepted", path=db_path)
    mark_delivered(
        claimed.event_id,
        outcome="processed",
        classified_level=claimed.event.classified_level,
        path=db_path,
    )
    enqueue_event(_event("unresolved-dead"), path=db_path, max_attempts=1)
    dead = claim_next_due_event(path=db_path)
    assert dead is not None
    record_processing_failure(dead, RuntimeError("poison"), path=db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE durable_events SET processed_at = ?, dead_lettered_at = "
            "CASE WHEN status = 'dead_lettered' THEN ? ELSE dead_lettered_at END",
            (old.isoformat(), old.isoformat()),
        )

    prune_retained_events(
        path=db_path,
        now=datetime.now(UTC),
        processed_retention_days=1,
        processed_retention_rows=0,
        dead_letter_retention_days=1,
    )

    assert get_event("processed-with-receipt", path=db_path) is not None
    assert get_event("unresolved-dead", path=db_path) is not None


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
