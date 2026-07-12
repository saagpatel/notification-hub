"""Durable cursor consumer for BridgeDB protected activity rows."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from notification_hub.durable_inbox import (
    advance_consumer_cursor,
    enqueue_event,
    get_consumer_cursor,
)
from notification_hub.models import BRIDGE_SOURCE_ALIASES, Event
from notification_hub.pipeline import build_stored_event

CONSUMER_NAME = "bridge-db-protected-activity-v1"


@dataclass(frozen=True)
class BridgePollResult:
    consumed: int
    cursor_before: int | None
    cursor_after: int
    bootstrapped: bool
    gap_ranges: tuple[tuple[int, int], ...] = ()


def _connect_read_only(path: Path) -> sqlite3.Connection:
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _logical_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def poll_bridge_protected_activity(
    bridge_db_path: Path,
    *,
    inbox_path: Path | None = None,
    backfill_on_first_run: bool = False,
    limit: int = 200,
) -> BridgePollResult:
    """Persist protected Bridge rows, advancing only after idempotent acceptance."""
    cursor_before = get_consumer_cursor(CONSUMER_NAME, path=inbox_path)
    with _connect_read_only(bridge_db_path) as bridge:
        max_row = bridge.execute(
            "SELECT COALESCE(MAX(id), 0) AS value FROM activity_log"
        ).fetchone()
        max_id = int(max_row["value"]) if max_row is not None else 0
        if cursor_before is not None and max_id < cursor_before:
            raise ValueError(
                f"BridgeDB cursor regression: stored={cursor_before}, source_max={max_id}"
            )
        if cursor_before is None and not backfill_on_first_run:
            advance_consumer_cursor(CONSUMER_NAME, max_id, path=inbox_path)
            return BridgePollResult(0, None, max_id, True)

        cursor = cursor_before or 0
        rows = bridge.execute(
            """
            SELECT id, source, timestamp, project_name, summary, canonical_key, tags
            FROM activity_log
            WHERE id > ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (cursor, max(1, min(limit, 1000))),
        ).fetchall()

    consumed = 0
    cursor_after = cursor
    gap_ranges: list[tuple[int, int]] = []
    for row in rows:
        row_id = int(row["id"])
        if row_id > cursor_after + 1:
            gap_ranges.append((cursor_after + 1, row_id - 1))
        tags = {str(value).upper() for value in json.loads(str(row["tags"]))}
        if not tags.intersection({"SHIPPED", "LEDGER"}):
            advance_consumer_cursor(CONSUMER_NAME, row_id, path=inbox_path)
            cursor_after = row_id
            continue
        source = BRIDGE_SOURCE_ALIASES.get(str(row["source"]), "bridge_watcher")
        event = Event(
            event_id=f"bridge-db:activity:{row_id}",
            source=source,
            level="normal" if "SHIPPED" in tags else "info",
            title=f"Bridge: {row['project_name']}",
            body=str(row["summary"]),
            project=str(row["canonical_key"] or row["project_name"]),
            timestamp=_logical_timestamp(str(row["timestamp"])),
            event_type="bridge.shipped" if "SHIPPED" in tags else "bridge.ledger",
            source_revision=str(row_id),
            sequence=row_id,
            privacy_class="internal",
        )
        enqueue_event(build_stored_event(event), path=inbox_path)
        advance_consumer_cursor(CONSUMER_NAME, row_id, path=inbox_path)
        cursor_after = row_id
        consumed += 1
    return BridgePollResult(consumed, cursor_before, cursor_after, False, tuple(gap_ranges))
