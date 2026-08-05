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


def _bridge_with_long_summary(path: Path, length: int) -> None:
    """A protected row whose summary exceeds Event.body, followed by an ordinary one.

    The trailing row is the point: the live failure was not that one notification was
    lost, it was that every protected row queued behind the over-long one stopped
    moving. A test that only checks the long row itself would pass against a consumer
    that still wedges.
    """
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
            """
        )
        conn.execute(
            "INSERT INTO activity_log VALUES (30, 'cc', '2026-08-05', 'alpha', ?, 'org/alpha',"
            " '[\"LEDGER\"]')",
            ("L" * length,),
        )
        conn.execute(
            "INSERT INTO activity_log VALUES (40, 'cc', '2026-08-05', 'beta', 'behind the poison"
            " pill', 'org/beta', '[\"SHIPPED\"]')"
        )


def test_over_long_summary_does_not_wedge_the_cursor(tmp_path: Path) -> None:
    # Observed live 2026-08-05: 261 consecutive poll failures on one row, bridge
    # activity delivery stopped for the duration. The cursor never advanced because the
    # ValidationError aborted the batch before advance_consumer_cursor ran.
    bridge = tmp_path / "bridge.db"
    inbox = tmp_path / "inbox.db"
    _bridge_with_long_summary(bridge, bridge_cursor.BODY_MAX_LENGTH + 5_000)

    result = poll_bridge_protected_activity(bridge, inbox_path=inbox, backfill_on_first_run=True)

    assert result.consumed == 2
    assert result.cursor_after == 40
    assert get_event("bridge-db:activity:40", path=inbox) is not None


def test_the_truncated_body_fits_and_says_it_was_truncated(tmp_path: Path) -> None:
    bridge = tmp_path / "bridge.db"
    inbox = tmp_path / "inbox.db"
    _bridge_with_long_summary(bridge, bridge_cursor.BODY_MAX_LENGTH + 5_000)

    poll_bridge_protected_activity(bridge, inbox_path=inbox, backfill_on_first_run=True)

    record = get_event("bridge-db:activity:30", path=inbox)
    assert record is not None
    assert len(record.event.body) <= bridge_cursor.BODY_MAX_LENGTH
    assert record.event.body.endswith(bridge_cursor.BODY_TRUNCATION_NOTICE)
    assert record.event.body.startswith("LLLL")


def test_a_summary_at_the_limit_is_left_alone(tmp_path: Path) -> None:
    bridge = tmp_path / "bridge.db"
    inbox = tmp_path / "inbox.db"
    _bridge_with_long_summary(bridge, bridge_cursor.BODY_MAX_LENGTH)

    poll_bridge_protected_activity(bridge, inbox_path=inbox, backfill_on_first_run=True)

    record = get_event("bridge-db:activity:30", path=inbox)
    assert record is not None
    assert record.event.body == "L" * bridge_cursor.BODY_MAX_LENGTH


def test_the_limit_is_read_from_the_event_contract_not_restated(tmp_path: Path) -> None:
    # If Event.body's max_length moves and this constant does not follow, the wedge
    # comes back silently.
    from notification_hub.models import Event

    declared = [
        c.max_length for c in Event.model_fields["body"].metadata if hasattr(c, "max_length")
    ]
    assert declared == [bridge_cursor.BODY_MAX_LENGTH]
