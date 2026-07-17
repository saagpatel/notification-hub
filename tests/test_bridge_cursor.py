"""Tests for crash-safe BridgeDB protected activity consumption."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import notification_hub.bridge_cursor as bridge_cursor
from notification_hub.bridge_cursor import CONSUMER_NAME, poll_bridge_protected_activity
from notification_hub.durable_inbox import collect_health, get_consumer_cursor, get_event


def _bridge(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE activity_log (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                project_name TEXT NOT NULL,
                summary TEXT NOT NULL,
                canonical_key TEXT,
                tags TEXT NOT NULL
            );
            INSERT INTO activity_log VALUES
              (10, 'cc', '2026-07-12', 'alpha', 'ordinary row', 'org/alpha', '[]'),
              (20, 'codex', '2026-07-12', 'beta', 'shipped row', 'org/beta', '["SHIPPED"]');
            """
        )


def test_bridge_poll_closes_read_only_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    class Cursor:
        def __init__(self, row=None, rows=None):
            self.row = row
            self.rows = rows or []

        def fetchone(self):
            return self.row

        def fetchall(self):
            return self.rows

    class TrackingConnection:
        closed = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql: str, *_args):
            if "MAX(id)" in sql:
                return Cursor({"value": 0})
            return Cursor(rows=[])

        def close(self) -> None:
            self.closed = True

    connection = TrackingConnection()
    monkeypatch.setattr(bridge_cursor, "get_consumer_cursor", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(bridge_cursor, "_connect_read_only", lambda _path: connection)

    poll_bridge_protected_activity(Path("unused"))

    assert connection.closed is True


def test_first_run_bootstraps_without_replaying_history(tmp_path: Path) -> None:
    bridge = tmp_path / "bridge.db"
    inbox = tmp_path / "inbox.db"
    _bridge(bridge)

    result = poll_bridge_protected_activity(bridge, inbox_path=inbox)

    assert result.bootstrapped is True
    assert result.consumed == 0
    assert get_consumer_cursor(CONSUMER_NAME, path=inbox) == 20


def test_backfill_consumes_protected_rows_with_deterministic_ids(tmp_path: Path) -> None:
    bridge = tmp_path / "bridge.db"
    inbox = tmp_path / "inbox.db"
    _bridge(bridge)

    result = poll_bridge_protected_activity(bridge, inbox_path=inbox, backfill_on_first_run=True)

    assert result.consumed == 1
    record = get_event("bridge-db:activity:20", path=inbox)
    assert record is not None
    assert record.event.source_revision == "20"
    assert collect_health(path=inbox)["queued_count"] == 1


def test_retry_after_cursor_write_loss_is_idempotent(tmp_path: Path) -> None:
    bridge = tmp_path / "bridge.db"
    inbox = tmp_path / "inbox.db"
    _bridge(bridge)
    poll_bridge_protected_activity(bridge, inbox_path=inbox, backfill_on_first_run=True)
    with sqlite3.connect(inbox) as conn:
        conn.execute("DELETE FROM consumer_cursors WHERE consumer = ?", (CONSUMER_NAME,))

    replay = poll_bridge_protected_activity(bridge, inbox_path=inbox, backfill_on_first_run=True)

    assert replay.consumed == 1
    assert collect_health(path=inbox)["queued_count"] == 1


def test_bridge_unavailable_does_not_advance_cursor_and_recovers(tmp_path: Path) -> None:
    bridge = tmp_path / "bridge.db"
    inbox = tmp_path / "inbox.db"

    with pytest.raises(sqlite3.OperationalError):
        poll_bridge_protected_activity(bridge, inbox_path=inbox)
    assert get_consumer_cursor(CONSUMER_NAME, path=inbox) is None

    _bridge(bridge)
    result = poll_bridge_protected_activity(bridge, inbox_path=inbox, backfill_on_first_run=True)
    assert result.consumed == 1
    assert get_consumer_cursor(CONSUMER_NAME, path=inbox) == 20


def test_rows_older_than_cursor_are_not_reordered_into_delivery(tmp_path: Path) -> None:
    bridge = tmp_path / "bridge.db"
    inbox = tmp_path / "inbox.db"
    _bridge(bridge)
    poll_bridge_protected_activity(bridge, inbox_path=inbox)
    with sqlite3.connect(bridge) as conn:
        conn.execute(
            "INSERT INTO activity_log VALUES (?, 'codex', '2026-07-12', ?, ?, ?, ?)",
            (15, "late-old", "late old shipped", "org/late-old", '["SHIPPED"]'),
        )
        conn.execute(
            "INSERT INTO activity_log VALUES (?, 'codex', '2026-07-12', ?, ?, ?, ?)",
            (30, "new", "new shipped", "org/new", '["SHIPPED"]'),
        )

    result = poll_bridge_protected_activity(bridge, inbox_path=inbox)

    assert result.consumed == 1
    assert get_event("bridge-db:activity:15", path=inbox) is None
    assert get_event("bridge-db:activity:30", path=inbox) is not None
    assert get_consumer_cursor(CONSUMER_NAME, path=inbox) == 30
    assert result.gap_ranges == ((21, 29),)


def test_source_rewrite_below_cursor_is_rejected(tmp_path: Path) -> None:
    bridge = tmp_path / "bridge.db"
    inbox = tmp_path / "inbox.db"
    _bridge(bridge)
    poll_bridge_protected_activity(bridge, inbox_path=inbox)
    with sqlite3.connect(bridge) as conn:
        conn.execute("DELETE FROM activity_log WHERE id = 20")

    with pytest.raises(ValueError, match="cursor regression"):
        poll_bridge_protected_activity(bridge, inbox_path=inbox)
    assert get_consumer_cursor(CONSUMER_NAME, path=inbox) == 20
