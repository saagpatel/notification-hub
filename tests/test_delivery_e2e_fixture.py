"""No-network chain proof from Bridge fixture through readback and observation."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from notification_hub.bridge_cursor import poll_bridge_protected_activity
from notification_hub.delivery_readback import (
    confirm_delivery_with_readback,
    record_operator_observation,
)
from notification_hub.durable_inbox import (
    channel_state_counts,
    claim_next_due_event,
    collect_health,
    enqueue_event,
    get_channel_receipts,
    get_channel_state,
    get_event,
    mark_delivered,
    record_channel_state,
    record_processing_deferred,
)
from notification_hub.models import StoredEvent
from notification_hub.pipeline import (
    DeliveryDeferred,
    get_suppression_engine,
    process_stored_event_with_result,
)


class IsolatedDestination:
    def __init__(self) -> None:
        self._accepted: set[str] = set()

    def accept(self, event_id: str) -> str:
        self._accepted.add(event_id)
        return f"fixture-accept:{event_id}"

    def readback(self, event_id: str, channel: str) -> str | None:
        if event_id not in self._accepted:
            return None
        return f"fixture-readback:{channel}:{event_id}"


def test_bridge_to_isolated_destination_readback_and_observation(tmp_path: Path) -> None:
    bridge = tmp_path / "bridge.db"
    inbox = tmp_path / "inbox.db"
    with sqlite3.connect(bridge) as conn:
        conn.executescript(
            """
            CREATE TABLE activity_log (
                id INTEGER PRIMARY KEY, source TEXT NOT NULL, timestamp TEXT NOT NULL,
                project_name TEXT NOT NULL, summary TEXT NOT NULL,
                canonical_key TEXT, tags TEXT NOT NULL
            );
            INSERT INTO activity_log VALUES (
                701, 'personal_ops', '2026-07-12T12:00:00Z', 'fixture-project',
                'fixture shipped event', 'fixture/project', '["SHIPPED"]'
            );
            """
        )

    polled = poll_bridge_protected_activity(bridge, inbox_path=inbox, backfill_on_first_run=True)
    assert polled.consumed == 1
    claimed = claim_next_due_event(path=inbox)
    assert claimed is not None
    assert claimed.event.event_id == "bridge-db:activity:701"
    assert claimed.event.source == "personal-ops"

    destination = IsolatedDestination()
    record_channel_state(claimed.event_id, "slack", "attempted", path=inbox)
    acceptance = destination.accept(claimed.event_id)
    record_channel_state(
        claimed.event_id,
        "slack",
        "accepted",
        path=inbox,
        destination_ref=acceptance,
    )
    mark_delivered(
        claimed.event_id,
        outcome="processed",
        classified_level=claimed.event.classified_level,
        path=inbox,
    )
    readback = confirm_delivery_with_readback(claimed.event_id, "slack", destination, path=inbox)
    record_operator_observation(claimed.event_id, "slack", "fixture-operator:observed", path=inbox)

    receipts = get_channel_receipts(claimed.event_id, "slack", path=inbox)
    assert receipts["acceptance_receipt"] == acceptance
    assert receipts["delivery_receipt"] == readback
    assert receipts["observation_receipt"] == "fixture-operator:observed"
    assert channel_state_counts(path=inbox) == {"observed": 1}


def test_restart_backlog_rate_limit_defers_without_spending_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the durable inbox/pipeline boundary used by the live worker."""
    inbox = tmp_path / "inbox.db"
    event = StoredEvent(
        event_id="fixture:restart-backlog:1",
        source="codex",
        level="normal",
        classified_level="normal",
        title="Session complete",
        body="Backlog fixture",
    )
    enqueue_event(event, path=inbox)
    claimed = claim_next_due_event(path=inbox)
    assert claimed is not None

    now = datetime.now(UTC)
    engine = get_suppression_engine()
    engine.restore_rate_history(
        push_times=(),
        slack_times=tuple(now for _ in range(20)),
    )
    monkeypatch.setattr("notification_hub.pipeline._slack_delivery_enabled", lambda: True)

    def record_state(channel: str, state: str, evidence: str | None) -> None:
        record_channel_state(
            claimed.event_id,
            channel,
            state,
            path=inbox,
            error_category=evidence if state in {"failed", "buffered"} else None,
        )

    with pytest.raises(DeliveryDeferred) as raised:
        process_stored_event_with_result(
            claimed.event,
            raise_on_delivery_failure=True,
            durable_mode=True,
            channel_state_recorder=record_state,
        )

    record_processing_deferred(claimed, raised.value.retry_at, path=inbox)
    record_channel_state(
        claimed.event_id,
        "slack",
        "buffered",
        path=inbox,
        backoff_until=raised.value.retry_at.isoformat(),
    )

    stored = get_event(claimed.event_id, path=inbox)
    receipts = get_channel_receipts(claimed.event_id, "slack", path=inbox)
    assert stored is not None
    assert stored.status == "retry_scheduled"
    assert stored.attempt_count == 0
    assert stored.last_error is None
    assert get_channel_state(claimed.event_id, "slack", path=inbox) == "buffered"
    assert receipts["error_category"] == "slack_rate_limited"
    assert receipts["backoff_until"] == raised.value.retry_at.isoformat()


def test_restart_backlog_pressure_cannot_create_dead_letter_storm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate the incident-sized backlog against restored channel limits."""
    inbox = tmp_path / "inbox.db"
    backlog_size = 250
    for index in range(backlog_size):
        enqueue_event(
            StoredEvent(
                event_id=f"fixture:backlog:{index}",
                source="codex",
                level="urgent",
                classified_level="urgent",
                title=f"Approval needed {index}",
                body="Backlog pressure fixture",
            ),
            path=inbox,
        )

    now = datetime.now(UTC)
    engine = get_suppression_engine()
    engine.restore_rate_history(
        push_times=tuple(now for _ in range(5)),
        slack_times=tuple(now for _ in range(20)),
    )
    monkeypatch.setattr(engine, "is_quiet_hours", lambda: False)
    monkeypatch.setattr("notification_hub.pipeline._slack_delivery_enabled", lambda: True)

    for _ in range(backlog_size):
        claimed = claim_next_due_event(path=inbox)
        assert claimed is not None

        def record_state(channel: str, state: str, evidence: str | None) -> None:
            record_channel_state(
                claimed.event_id,
                channel,
                state,
                path=inbox,
                error_category=evidence if state in {"failed", "buffered"} else None,
            )

        with pytest.raises(DeliveryDeferred) as raised:
            process_stored_event_with_result(
                claimed.event,
                raise_on_delivery_failure=True,
                skip_duplicate_suppression=True,
                durable_mode=True,
                channel_state_recorder=record_state,
            )
        record_processing_deferred(claimed, raised.value.retry_at, path=inbox)
        for channel in ("push", "slack"):
            record_channel_state(
                claimed.event_id,
                channel,
                "buffered",
                path=inbox,
                backoff_until=raised.value.retry_at.isoformat(),
            )

    with sqlite3.connect(inbox) as conn:
        conn.execute(
            "UPDATE durable_events SET created_at = ? WHERE status = 'retry_scheduled'",
            ((datetime.now(UTC) - timedelta(minutes=10)).isoformat(),),
        )
        event_attempts = conn.execute(
            "SELECT COALESCE(SUM(attempt_count), 0) FROM durable_events"
        ).fetchone()[0]
        channel_attempts = conn.execute(
            "SELECT COALESCE(SUM(attempt_count), 0) FROM channel_deliveries"
        ).fetchone()[0]

    health = collect_health(path=inbox)
    assert health["status"] == "ok"
    assert health["retry_scheduled_count"] == backlog_size
    assert health["dead_letter_count"] == 0
    assert health["next_action"] == (
        "Deferred events are waiting for their scheduled retry times."
    )
    assert channel_state_counts(path=inbox) == {"buffered": backlog_size * 2}
    assert event_attempts == 0
    assert channel_attempts == 0
