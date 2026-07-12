"""No-network chain proof from Bridge fixture through readback and observation."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from notification_hub.bridge_cursor import poll_bridge_protected_activity
from notification_hub.delivery_readback import (
    confirm_delivery_with_readback,
    record_operator_observation,
)
from notification_hub.durable_inbox import (
    channel_state_counts,
    claim_next_due_event,
    get_channel_receipts,
    mark_delivered,
    record_channel_state,
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
