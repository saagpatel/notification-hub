"""SQLite durable inbox for accepted notification events."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, TypedDict, cast

from notification_hub.config import DURABLE_INBOX_DB
from notification_hub.models import Level, StoredEvent

DurableEventStatus = Literal[
    "queued",
    "processing",
    "retry_scheduled",
    "processed",
    "suppressed",
    "dead_lettered",
]
DurableOutcome = Literal["processed", "suppressed"]

DEFAULT_DB_PATH = DURABLE_INBOX_DB
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_LEASE_SECONDS = 60
DEFAULT_RETRY_BACKOFF_SECONDS = (5, 30, 120, 300, 600)
RETRY_BACKOFF_CAP_SECONDS = 600
PROCESSED_RETENTION_DAYS = 30
PROCESSED_RETENTION_ROWS = 10_000
DEAD_LETTER_RETENTION_DAYS = 90
DEAD_LETTER_DEGRADED_AFTER_SECONDS = 24 * 60 * 60
BACKLOG_DEGRADED_AFTER_SECONDS = 300


class IdempotencyConflictError(ValueError):
    """Raised when an event id is reused with a different canonical payload."""


def event_payload_digest(event: StoredEvent) -> str:
    """Return a stable digest that excludes server receipt metadata."""
    if event.payload_digest is not None:
        return event.payload_digest
    payload = event.model_dump(
        mode="json",
        exclude={
            "event_id",
            "timestamp",
            "payload_digest",
            "received_at",
            "classified_level",
        },
    )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class DurableInboxHealth(TypedDict):
    status: str
    db_path: str
    db_exists: bool
    queued_count: int
    processing_count: int
    retry_scheduled_count: int
    retrying_count: int
    processed_count: int
    suppressed_count: int
    dead_letter_count: int
    unresolved_dead_letter_count: int
    recent_dead_letter_count: int
    delivery_state_counts: dict[str, int]
    attempted_count: int
    accepted_count: int
    delivered_count: int
    observed_count: int
    dispositioned_count: int
    stale_processing_count: int
    oldest_pending_at: str | None
    oldest_pending_age_seconds: float | None
    last_accepted_at: str | None
    last_processed_at: str | None
    last_dead_lettered_at: str | None
    next_action: str
    error: str | None


@dataclass(frozen=True)
class DurableEventRecord:
    event_id: str
    event: StoredEvent
    status: DurableEventStatus
    attempt_count: int
    max_attempts: int
    next_attempt_at: str | None
    lease_until: str | None
    created_at: str
    updated_at: str
    processed_at: str | None
    dead_lettered_at: str | None
    last_error: str | None
    last_error_type: str | None


def utc_now() -> datetime:
    return datetime.now(UTC)


def isoformat(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat()


def _db_path(path: Path | None = None) -> Path:
    return path or DEFAULT_DB_PATH


def _connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = _db_path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")
    return conn


def init_schema(path: Path | None = None) -> None:
    """Create the durable inbox schema if it does not exist."""
    with _connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS durable_events (
                event_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 5,
                next_attempt_at TEXT,
                lease_until TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                processed_at TEXT,
                dead_lettered_at TEXT,
                last_error TEXT,
                last_error_type TEXT,
                source TEXT NOT NULL,
                project TEXT,
                level TEXT NOT NULL,
                classified_level TEXT,
                title TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_durable_events_due
                ON durable_events(status, next_attempt_at, created_at);
            CREATE INDEX IF NOT EXISTS idx_durable_events_lease
                ON durable_events(status, lease_until);
            CREATE INDEX IF NOT EXISTS idx_durable_events_retention
                ON durable_events(status, processed_at, dead_lettered_at);
            CREATE TABLE IF NOT EXISTS schema_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS channel_deliveries (
                event_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                state TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                attempted_at TEXT,
                accepted_at TEXT,
                delivered_at TEXT,
                observed_at TEXT,
                dispositioned_at TEXT,
                destination_ref TEXT,
                acceptance_receipt TEXT,
                delivery_receipt TEXT,
                observation_receipt TEXT,
                terminal_disposition TEXT,
                backoff_until TEXT,
                last_error_category TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(event_id, channel),
                FOREIGN KEY(event_id) REFERENCES durable_events(event_id) ON DELETE RESTRICT
            );
            CREATE TABLE IF NOT EXISTS consumer_cursors (
                consumer TEXT PRIMARY KEY,
                cursor_value INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(durable_events)").fetchall()
        }
        if "payload_digest" not in columns:
            conn.execute("ALTER TABLE durable_events ADD COLUMN payload_digest TEXT")
        if "dead_letter_disposition" not in columns:
            conn.execute("ALTER TABLE durable_events ADD COLUMN dead_letter_disposition TEXT")
        if "dead_letter_disposition_ref" not in columns:
            conn.execute("ALTER TABLE durable_events ADD COLUMN dead_letter_disposition_ref TEXT")
        if "dead_letter_dispositioned_at" not in columns:
            conn.execute("ALTER TABLE durable_events ADD COLUMN dead_letter_dispositioned_at TEXT")
        channel_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(channel_deliveries)").fetchall()
        }
        for name in (
            "acceptance_receipt",
            "delivery_receipt",
            "observation_receipt",
            "terminal_disposition",
            "backoff_until",
            "last_error_category",
        ):
            if name not in channel_columns:
                conn.execute(f"ALTER TABLE channel_deliveries ADD COLUMN {name} TEXT")  # noqa: S608
        conn.execute(
            "INSERT OR REPLACE INTO schema_metadata(key, value) VALUES('schema_version', '5')"
        )


def accepted_channels(event_id: str, *, path: Path | None = None) -> frozenset[str]:
    init_schema(path)
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT channel FROM channel_deliveries WHERE event_id = ? "
            "AND state IN ('accepted', 'delivered', 'observed', 'dispositioned')",
            (event_id,),
        ).fetchall()
    return frozenset(str(row["channel"]) for row in rows)


def record_channel_state(
    event_id: str,
    channel: str,
    state: str,
    *,
    path: Path | None = None,
    destination_ref: str | None = None,
    error_category: str | None = None,
    backoff_until: str | None = None,
) -> None:
    """Persist one monotonic channel state without claiming unsupported readback."""
    allowed = {
        "attempted",
        "buffered",
        "accepted",
        "delivered",
        "observed",
        "failed",
        "dispositioned",
    }
    if state not in allowed:
        raise ValueError(f"unsupported channel state: {state}")
    now = isoformat()
    timestamp_column = {
        "attempted": "attempted_at",
        "accepted": "accepted_at",
        "delivered": "delivered_at",
        "observed": "observed_at",
        "dispositioned": "dispositioned_at",
    }.get(state)
    init_schema(path)
    with _connect(path) as conn:
        existing = conn.execute(
            "SELECT state FROM channel_deliveries WHERE event_id = ? AND channel = ?",
            (event_id, channel),
        ).fetchone()
        current_state = str(existing["state"]) if existing is not None else None
        terminal_rank = {
            "attempted": 1,
            "buffered": 1,
            "failed": 1,
            "accepted": 2,
            "delivered": 3,
            "observed": 4,
            "dispositioned": 5,
        }
        if (
            current_state is not None
            and terminal_rank[current_state] > terminal_rank[state]
            and not (current_state in {"buffered", "failed"} and state == "attempted")
        ):
            return
        conn.execute(
            "INSERT OR IGNORE INTO channel_deliveries "
            "(event_id, channel, state, updated_at) VALUES (?, ?, ?, ?)",
            (event_id, channel, state, now),
        )
        assignments = ["state = ?", "updated_at = ?"]
        values: list[object] = [state, now]
        if state == "attempted":
            assignments.extend(
                [
                    "attempt_count = attempt_count + 1",
                    "attempted_at = ?",
                    "backoff_until = NULL",
                ]
            )
            values.append(now)
        elif timestamp_column is not None:
            assignments.append(f"{timestamp_column} = COALESCE({timestamp_column}, ?)")
            values.append(now)
        if destination_ref is not None:
            assignments.append("destination_ref = ?")
            values.append(destination_ref)
            receipt_column = {
                "accepted": "acceptance_receipt",
                "delivered": "delivery_receipt",
                "observed": "observation_receipt",
                "dispositioned": "terminal_disposition",
            }.get(state)
            if receipt_column is not None:
                assignments.append(f"{receipt_column} = ?")
                values.append(destination_ref)
        if error_category is not None:
            assignments.append("last_error_category = ?")
            values.append(error_category)
        if backoff_until is not None:
            assignments.append("backoff_until = ?")
            values.append(backoff_until)
        values.extend([event_id, channel])
        conn.execute(
            f"UPDATE channel_deliveries SET {', '.join(assignments)} "  # noqa: S608
            "WHERE event_id = ? AND channel = ?",
            values,
        )


def channel_state_counts(*, path: Path | None = None) -> dict[str, int]:
    init_schema(path)
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT state, COUNT(*) AS count FROM channel_deliveries GROUP BY state"
        ).fetchall()
    return {str(row["state"]): int(row["count"]) for row in rows}


def channels_in_state(event_id: str, state: str, *, path: Path | None = None) -> frozenset[str]:
    init_schema(path)
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT channel FROM channel_deliveries WHERE event_id = ? AND state = ?",
            (event_id, state),
        ).fetchall()
    return frozenset(str(row["channel"]) for row in rows)


def get_channel_state(event_id: str, channel: str, *, path: Path | None = None) -> str | None:
    init_schema(path)
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT state FROM channel_deliveries WHERE event_id = ? AND channel = ?",
            (event_id, channel),
        ).fetchone()
    return str(row["state"]) if row is not None else None


def get_channel_receipts(
    event_id: str, channel: str, *, path: Path | None = None
) -> dict[str, str | None]:
    init_schema(path)
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT acceptance_receipt, delivery_receipt, observation_receipt, "
            "terminal_disposition, last_error_category AS error_category, backoff_until "
            "FROM channel_deliveries "
            "WHERE event_id = ? AND channel = ?",
            (event_id, channel),
        ).fetchone()
    if row is None:
        raise KeyError((event_id, channel))
    return {key: cast(str | None, row[key]) for key in row.keys()}


def disposition_dead_letter(
    event_id: str,
    disposition: str,
    disposition_ref: str,
    *,
    path: Path | None = None,
) -> None:
    """Resolve an actionable dead letter without deleting its historical record."""
    if not disposition.strip() or not disposition_ref.strip():
        raise ValueError("dead-letter disposition and reference must be non-empty")
    init_schema(path)
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT status FROM durable_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if row is None:
            raise KeyError(event_id)
        if str(row["status"]) != "dead_lettered":
            raise ValueError("only dead-lettered events can be dispositioned")
        conn.execute(
            "UPDATE durable_events SET dead_letter_disposition = ?, "
            "dead_letter_disposition_ref = ?, dead_letter_dispositioned_at = ?, "
            "updated_at = ? WHERE event_id = ?",
            (disposition, disposition_ref, isoformat(), isoformat(), event_id),
        )


def get_consumer_cursor(consumer: str, *, path: Path | None = None) -> int | None:
    init_schema(path)
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT cursor_value FROM consumer_cursors WHERE consumer = ?", (consumer,)
        ).fetchone()
    return int(row["cursor_value"]) if row is not None else None


def advance_consumer_cursor(consumer: str, cursor_value: int, *, path: Path | None = None) -> None:
    """Advance monotonically; retries after a crash remain safe by event id."""
    init_schema(path)
    with _connect(path) as conn:
        current = conn.execute(
            "SELECT cursor_value FROM consumer_cursors WHERE consumer = ?", (consumer,)
        ).fetchone()
        if current is not None and int(current["cursor_value"]) > cursor_value:
            raise ValueError("consumer cursor cannot move backwards")
        conn.execute(
            "INSERT INTO consumer_cursors(consumer, cursor_value, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(consumer) DO UPDATE SET cursor_value=excluded.cursor_value, "
            "updated_at=excluded.updated_at",
            (consumer, cursor_value, isoformat()),
        )


def enqueue_event(
    event: StoredEvent,
    *,
    path: Path | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> StoredEvent:
    """Persist an accepted event before the caller acknowledges receipt."""
    init_schema(path)
    now = isoformat()
    payload_json = event.model_dump_json()
    payload_digest = event_payload_digest(event)
    with _connect(path) as conn:
        existing = conn.execute(
            "SELECT payload_json, payload_digest FROM durable_events WHERE event_id = ?",
            (event.event_id,),
        ).fetchone()
        if existing is not None:
            existing_event = StoredEvent.model_validate_json(str(existing["payload_json"]))
            existing_digest = str(
                existing["payload_digest"] or event_payload_digest(existing_event)
            )
            if existing_digest != payload_digest:
                raise IdempotencyConflictError(
                    f"event_id {event.event_id!r} already exists with a different payload digest"
                )
            return existing_event

        conn.execute(
            """
            INSERT OR IGNORE INTO durable_events (
                event_id,
                payload_json,
                status,
                attempt_count,
                max_attempts,
                next_attempt_at,
                created_at,
                updated_at,
                source,
                project,
                level,
                classified_level,
                title,
                payload_digest
            )
            VALUES (?, ?, 'queued', 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                payload_json,
                max_attempts,
                now,
                now,
                now,
                event.source,
                event.project,
                event.level,
                event.classified_level,
                event.title,
                payload_digest,
            ),
        )
        row = conn.execute(
            "SELECT payload_json FROM durable_events WHERE event_id = ?",
            (event.event_id,),
        ).fetchone()

    if row is None:
        raise RuntimeError("durable inbox insert did not return the accepted event")
    return StoredEvent.model_validate_json(str(row["payload_json"]))


def _record_from_row(row: sqlite3.Row) -> DurableEventRecord:
    return DurableEventRecord(
        event_id=str(row["event_id"]),
        event=StoredEvent.model_validate_json(str(row["payload_json"])),
        status=cast(DurableEventStatus, str(row["status"])),
        attempt_count=int(row["attempt_count"]),
        max_attempts=int(row["max_attempts"]),
        next_attempt_at=cast(str | None, row["next_attempt_at"]),
        lease_until=cast(str | None, row["lease_until"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        processed_at=cast(str | None, row["processed_at"]),
        dead_lettered_at=cast(str | None, row["dead_lettered_at"]),
        last_error=cast(str | None, row["last_error"]),
        last_error_type=cast(str | None, row["last_error_type"]),
    )


def get_event(event_id: str, *, path: Path | None = None) -> DurableEventRecord | None:
    init_schema(path)
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM durable_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
    return _record_from_row(row) if row is not None else None


def claim_next_due_event(
    *,
    path: Path | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> DurableEventRecord | None:
    """Atomically claim one due queued/retry event for delivery."""
    init_schema(path)
    now_dt = utc_now()
    now = isoformat(now_dt)
    lease_until = isoformat(now_dt + timedelta(seconds=lease_seconds))
    conn = _connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT * FROM durable_events
            WHERE status IN ('queued', 'retry_scheduled')
              AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (now,),
        ).fetchone()
        if row is None:
            conn.commit()
            return None

        event_id = str(row["event_id"])
        attempt_count = int(row["attempt_count"]) + 1
        conn.execute(
            """
            UPDATE durable_events
            SET status = 'processing',
                attempt_count = ?,
                lease_until = ?,
                updated_at = ?
            WHERE event_id = ?
            """,
            (attempt_count, lease_until, now, event_id),
        )
        conn.commit()
        updated = dict(row)
        updated["status"] = "processing"
        updated["attempt_count"] = attempt_count
        updated["lease_until"] = lease_until
        updated["updated_at"] = now
        return DurableEventRecord(
            event_id=str(updated["event_id"]),
            event=StoredEvent.model_validate_json(str(updated["payload_json"])),
            status="processing",
            attempt_count=attempt_count,
            max_attempts=int(updated["max_attempts"]),
            next_attempt_at=cast(str | None, updated["next_attempt_at"]),
            lease_until=lease_until,
            created_at=str(updated["created_at"]),
            updated_at=now,
            processed_at=cast(str | None, updated["processed_at"]),
            dead_lettered_at=cast(str | None, updated["dead_lettered_at"]),
            last_error=cast(str | None, updated["last_error"]),
            last_error_type=cast(str | None, updated["last_error_type"]),
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mark_delivered(
    event_id: str,
    *,
    outcome: DurableOutcome,
    classified_level: Level | None,
    path: Path | None = None,
) -> None:
    now = isoformat()
    with _connect(path) as conn:
        conn.execute(
            """
            UPDATE durable_events
            SET status = ?,
                processed_at = ?,
                lease_until = NULL,
                next_attempt_at = NULL,
                last_error = NULL,
                last_error_type = NULL,
                classified_level = ?,
                updated_at = ?
            WHERE event_id = ?
            """,
            (outcome, now, classified_level, now, event_id),
        )


def retry_delay_seconds(attempt_count: int) -> int:
    if attempt_count <= 0:
        return DEFAULT_RETRY_BACKOFF_SECONDS[0]
    index = min(attempt_count - 1, len(DEFAULT_RETRY_BACKOFF_SECONDS) - 1)
    return min(DEFAULT_RETRY_BACKOFF_SECONDS[index], RETRY_BACKOFF_CAP_SECONDS)


def record_processing_failure(
    record: DurableEventRecord,
    error: BaseException,
    *,
    path: Path | None = None,
) -> DurableEventStatus:
    now_dt = utc_now()
    now = isoformat(now_dt)
    error_text = str(error)[:1000] or error.__class__.__name__
    error_type = error.__class__.__name__
    with _connect(path) as conn:
        if record.attempt_count >= record.max_attempts:
            conn.execute(
                """
                UPDATE durable_events
                SET status = 'dead_lettered',
                    lease_until = NULL,
                    next_attempt_at = NULL,
                    dead_lettered_at = ?,
                    last_error = ?,
                    last_error_type = ?,
                    updated_at = ?
                WHERE event_id = ?
                """,
                (now, error_text, error_type, now, record.event_id),
            )
            return "dead_lettered"

        delay = retry_delay_seconds(record.attempt_count)
        next_attempt_at = isoformat(now_dt + timedelta(seconds=delay))
        conn.execute(
            """
            UPDATE durable_events
            SET status = 'retry_scheduled',
                lease_until = NULL,
                next_attempt_at = ?,
                last_error = ?,
                last_error_type = ?,
                updated_at = ?
            WHERE event_id = ?
            """,
            (next_attempt_at, error_text, error_type, now, record.event_id),
        )
        return "retry_scheduled"


def record_processing_deferred(
    record: DurableEventRecord,
    retry_at: datetime,
    *,
    path: Path | None = None,
) -> DurableEventStatus:
    """Durably defer without consuming the delivery failure budget."""
    now = isoformat()
    with _connect(path) as conn:
        conn.execute(
            """
            UPDATE durable_events
            SET status = 'retry_scheduled',
                attempt_count = CASE WHEN attempt_count > 0 THEN attempt_count - 1 ELSE 0 END,
                lease_until = NULL,
                next_attempt_at = ?,
                last_error = NULL,
                last_error_type = NULL,
                updated_at = ?
            WHERE event_id = ?
            """,
            (isoformat(retry_at), now, record.event_id),
        )
    return "retry_scheduled"


def reclaim_stale_processing(
    *,
    path: Path | None = None,
    now: datetime | None = None,
) -> int:
    """Move expired processing leases back to retry so restarts do not drop events."""
    init_schema(path)
    now_iso = isoformat(now)
    with _connect(path) as conn:
        cursor = conn.execute(
            """
            UPDATE durable_events
            SET status = 'retry_scheduled',
                lease_until = NULL,
                next_attempt_at = ?,
                updated_at = ?
            WHERE status = 'processing'
              AND lease_until IS NOT NULL
              AND lease_until < ?
            """,
            (now_iso, now_iso, now_iso),
        )
        return cursor.rowcount


def prune_retained_events(
    *,
    path: Path | None = None,
    now: datetime | None = None,
    processed_retention_days: int = PROCESSED_RETENTION_DAYS,
    processed_retention_rows: int = PROCESSED_RETENTION_ROWS,
    dead_letter_retention_days: int = DEAD_LETTER_RETENTION_DAYS,
) -> int:
    """Apply bounded retention for completed rows while keeping recent audit evidence."""
    init_schema(path)
    now_dt = now or utc_now()
    processed_cutoff = isoformat(now_dt - timedelta(days=processed_retention_days))
    dead_letter_cutoff = isoformat(now_dt - timedelta(days=dead_letter_retention_days))
    with _connect(path) as conn:
        deleted_processed = conn.execute(
            """
            DELETE FROM durable_events
            WHERE status IN ('processed', 'suppressed')
              AND processed_at IS NOT NULL
              AND processed_at < ?
              AND event_id NOT IN (
                SELECT event_id FROM durable_events
                WHERE status IN ('processed', 'suppressed')
                ORDER BY processed_at DESC
                LIMIT ?
              )
              AND NOT EXISTS (
                SELECT 1 FROM channel_deliveries
                WHERE channel_deliveries.event_id = durable_events.event_id
              )
            """,
            (processed_cutoff, processed_retention_rows),
        ).rowcount
        deleted_dead = conn.execute(
            """
            DELETE FROM durable_events
            WHERE status = 'dead_lettered'
              AND dead_lettered_at IS NOT NULL
              AND dead_lettered_at < ?
              AND dead_letter_disposition IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM channel_deliveries
                WHERE channel_deliveries.event_id = durable_events.event_id
              )
            """,
            (dead_letter_cutoff,),
        ).rowcount
    return deleted_processed + deleted_dead


def _parse_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def collect_health(*, path: Path | None = None, create: bool = False) -> DurableInboxHealth:
    """Return safe aggregate facts for health, status, and review surfaces."""
    db_path = _db_path(path)
    if not db_path.exists() and not create:
        return {
            "status": "ok",
            "db_path": str(db_path),
            "db_exists": False,
            "queued_count": 0,
            "processing_count": 0,
            "retry_scheduled_count": 0,
            "retrying_count": 0,
            "processed_count": 0,
            "suppressed_count": 0,
            "dead_letter_count": 0,
            "unresolved_dead_letter_count": 0,
            "recent_dead_letter_count": 0,
            "delivery_state_counts": {},
            "attempted_count": 0,
            "accepted_count": 0,
            "delivered_count": 0,
            "observed_count": 0,
            "dispositioned_count": 0,
            "stale_processing_count": 0,
            "oldest_pending_at": None,
            "oldest_pending_age_seconds": None,
            "last_accepted_at": None,
            "last_processed_at": None,
            "last_dead_lettered_at": None,
            "next_action": "Durable inbox has no accepted events yet.",
            "error": None,
        }
    try:
        init_schema(path)
        now_dt = utc_now()
        now = isoformat(now_dt)
        recent_dead_cutoff = isoformat(
            now_dt - timedelta(seconds=DEAD_LETTER_DEGRADED_AFTER_SECONDS)
        )
        with _connect(path) as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS count FROM durable_events GROUP BY status"
            ).fetchall()
            counts = {str(row["status"]): int(row["count"]) for row in rows}
            aggregate = conn.execute(
                """
                SELECT
                    MIN(CASE WHEN status IN ('queued', 'processing', 'retry_scheduled')
                        THEN created_at END) AS oldest_pending_at,
                    MAX(created_at) AS last_accepted_at,
                    MAX(processed_at) AS last_processed_at,
                    MAX(dead_lettered_at) AS last_dead_lettered_at,
                    SUM(CASE WHEN status = 'dead_lettered'
                        AND dead_lettered_at IS NOT NULL
                        AND dead_lettered_at >= ?
                        THEN 1 ELSE 0 END) AS recent_dead_letter_count,
                    SUM(CASE WHEN status = 'dead_lettered'
                        AND dead_letter_disposition IS NULL
                        THEN 1 ELSE 0 END) AS unresolved_dead_letter_count,
                    SUM(CASE WHEN status = 'processing'
                        AND lease_until IS NOT NULL
                        AND lease_until < ?
                        THEN 1 ELSE 0 END) AS stale_processing_count
                FROM durable_events
                """,
                (recent_dead_cutoff, now),
            ).fetchone()
            delivery_rows = conn.execute(
                "SELECT state, COUNT(*) AS count FROM channel_deliveries GROUP BY state"
            ).fetchall()
            attempted_row = conn.execute(
                "SELECT COALESCE(SUM(attempt_count), 0) AS count FROM channel_deliveries"
            ).fetchone()
            delivery_counts = {str(row["state"]): int(row["count"]) for row in delivery_rows}
            delivery_counts["attempted"] = int(attempted_row["count"] or 0)
            for state in ("accepted", "delivered", "observed", "dispositioned"):
                delivery_counts.setdefault(state, 0)
    except sqlite3.Error as exc:
        return {
            "status": "degraded",
            "db_path": str(db_path),
            "db_exists": db_path.exists(),
            "queued_count": 0,
            "processing_count": 0,
            "retry_scheduled_count": 0,
            "retrying_count": 0,
            "processed_count": 0,
            "suppressed_count": 0,
            "dead_letter_count": 0,
            "unresolved_dead_letter_count": 0,
            "recent_dead_letter_count": 0,
            "delivery_state_counts": {},
            "attempted_count": 0,
            "accepted_count": 0,
            "delivered_count": 0,
            "observed_count": 0,
            "dispositioned_count": 0,
            "stale_processing_count": 0,
            "oldest_pending_at": None,
            "oldest_pending_age_seconds": None,
            "last_accepted_at": None,
            "last_processed_at": None,
            "last_dead_lettered_at": None,
            "next_action": "Inspect the durable inbox SQLite database.",
            "error": str(exc),
        }

    queued = counts.get("queued", 0)
    processing = counts.get("processing", 0)
    retry_scheduled = counts.get("retry_scheduled", 0)
    processed = counts.get("processed", 0)
    suppressed = counts.get("suppressed", 0)
    dead = counts.get("dead_lettered", 0)
    unresolved_dead = int(aggregate["unresolved_dead_letter_count"] or 0)
    recent_dead = int(aggregate["recent_dead_letter_count"] or 0)
    oldest_pending_at = cast(str | None, aggregate["oldest_pending_at"])
    stale_processing = int(aggregate["stale_processing_count"] or 0)
    oldest_pending_dt = _parse_iso(oldest_pending_at)
    oldest_pending_age_seconds: float | None = None
    if oldest_pending_dt is not None:
        oldest_pending_age_seconds = max(0.0, (utc_now() - oldest_pending_dt).total_seconds())

    stale_backlog = (
        oldest_pending_age_seconds is not None
        and oldest_pending_age_seconds > BACKLOG_DEGRADED_AFTER_SECONDS
    )
    if unresolved_dead > 0:
        status = "degraded"
        next_action = "Review and disposition every unresolved dead-lettered event."
    elif stale_processing > 0:
        status = "degraded"
        next_action = "Reclaim stale processing leases, then verify the worker drains the inbox."
    elif stale_backlog:
        status = "degraded"
        next_action = "Inspect the durable inbox worker; queued events are not draining."
    else:
        status = "ok"
        next_action = "Durable inbox is clear."

    return {
        "status": status,
        "db_path": str(db_path),
        "db_exists": db_path.exists(),
        "queued_count": queued,
        "processing_count": processing,
        "retry_scheduled_count": retry_scheduled,
        "retrying_count": retry_scheduled,
        "processed_count": processed,
        "suppressed_count": suppressed,
        "dead_letter_count": dead,
        "unresolved_dead_letter_count": unresolved_dead,
        "recent_dead_letter_count": recent_dead,
        "delivery_state_counts": delivery_counts,
        "attempted_count": delivery_counts["attempted"],
        "accepted_count": delivery_counts["accepted"],
        "delivered_count": delivery_counts["delivered"],
        "observed_count": delivery_counts["observed"],
        "dispositioned_count": delivery_counts["dispositioned"],
        "stale_processing_count": stale_processing,
        "oldest_pending_at": oldest_pending_at,
        "oldest_pending_age_seconds": oldest_pending_age_seconds,
        "last_accepted_at": cast(str | None, aggregate["last_accepted_at"]),
        "last_processed_at": cast(str | None, aggregate["last_processed_at"]),
        "last_dead_lettered_at": cast(str | None, aggregate["last_dead_lettered_at"]),
        "next_action": next_action,
        "error": None,
    }
