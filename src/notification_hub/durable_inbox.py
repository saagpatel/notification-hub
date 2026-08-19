"""SQLite durable inbox for accepted notification events."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from contextlib import contextmanager
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
    "reconciliation_required",
    "reconciled_succeeded",
    "reconciled_absent",
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
DELIVERY_HISTORY_RETENTION_DAYS = 180
DEAD_LETTER_DEGRADED_AFTER_SECONDS = 24 * 60 * 60
BACKLOG_DEGRADED_AFTER_SECONDS = 300


class IdempotencyConflictError(ValueError):
    """Raised when an event id is reused with a different canonical payload."""


class MissingEventAuthorityError(ValueError):
    """Raised when a new durable event lacks an exact producer/destination binding."""


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
    reconciliation_required_count: int
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


@contextmanager
def _managed_connection(path: Path | None = None):
    """Run one transaction and always release its SQLite file descriptors."""
    conn = _connect(path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_schema(path: Path | None = None) -> None:
    """Create the durable inbox schema if it does not exist."""
    with _managed_connection(path) as conn:
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
            CREATE TABLE IF NOT EXISTS channel_reconciliation_receipts (
                reconciliation_id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                original_unknown_evidence_digest TEXT NOT NULL,
                original_provider_reference TEXT NOT NULL,
                terminal_outcome TEXT NOT NULL,
                provider_reference TEXT NOT NULL,
                readback_json TEXT NOT NULL,
                readback_digest TEXT NOT NULL,
                receipt_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(event_id, channel),
                FOREIGN KEY(event_id) REFERENCES durable_events(event_id) ON DELETE RESTRICT
            );
            CREATE TRIGGER IF NOT EXISTS channel_reconciliation_receipts_no_update
            BEFORE UPDATE ON channel_reconciliation_receipts
            BEGIN
                SELECT RAISE(ABORT, 'channel reconciliation receipts are append-only');
            END;
            CREATE TRIGGER IF NOT EXISTS channel_reconciliation_receipts_no_delete
            BEFORE DELETE ON channel_reconciliation_receipts
            BEGIN
                SELECT RAISE(ABORT, 'channel reconciliation receipts are append-only');
            END;
            CREATE TRIGGER IF NOT EXISTS outcome_unknown_channel_no_update
            BEFORE UPDATE ON channel_deliveries
            WHEN OLD.state = 'outcome_unknown' AND OLD.destination_ref IS NOT NULL
            BEGIN
                SELECT RAISE(ABORT, 'outcome_unknown evidence is immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS outcome_unknown_channel_no_delete
            BEFORE DELETE ON channel_deliveries
            WHEN OLD.state = 'outcome_unknown'
            BEGIN
                SELECT RAISE(ABORT, 'outcome_unknown evidence is immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS reconciled_event_status_no_update
            BEFORE UPDATE OF status ON durable_events
            WHEN OLD.status IN ('reconciled_succeeded', 'reconciled_absent')
                AND NEW.status != OLD.status
            BEGIN
                SELECT RAISE(ABORT, 'reconciled event status is terminal');
            END;
            CREATE TRIGGER IF NOT EXISTS reconciled_event_no_delete
            BEFORE DELETE ON durable_events
            WHEN OLD.status IN ('reconciled_succeeded', 'reconciled_absent')
            BEGIN
                SELECT RAISE(ABORT, 'reconciled event evidence is append-only');
            END;
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
            "INSERT OR REPLACE INTO schema_metadata(key, value) VALUES('schema_version', '7')"
        )


def accepted_channels(event_id: str, *, path: Path | None = None) -> frozenset[str]:
    init_schema(path)
    with _managed_connection(path) as conn:
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
        "outcome_unknown",
        "dispositioned",
    }
    if state not in allowed:
        raise ValueError(f"unsupported channel state: {state}")
    if state == "outcome_unknown" and not destination_ref:
        raise ValueError("outcome_unknown requires a provider reference")
    now = isoformat()
    timestamp_column = {
        "attempted": "attempted_at",
        "accepted": "accepted_at",
        "delivered": "delivered_at",
        "observed": "observed_at",
        "dispositioned": "dispositioned_at",
    }.get(state)
    init_schema(path)
    with _managed_connection(path) as conn:
        existing = conn.execute(
            "SELECT state, destination_ref FROM channel_deliveries "
            "WHERE event_id = ? AND channel = ?",
            (event_id, channel),
        ).fetchone()
        current_state = str(existing["state"]) if existing is not None else None
        if current_state == "outcome_unknown":
            assert existing is not None
            current_ref = cast(str | None, existing["destination_ref"])
            if state != "outcome_unknown":
                raise ValueError("outcome_unknown requires explicit reconciliation")
            if destination_ref != current_ref:
                raise ValueError("outcome_unknown receipt is immutable")
            return
        terminal_rank = {
            "attempted": 1,
            "buffered": 1,
            "failed": 1,
            "outcome_unknown": 2,
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
    with _managed_connection(path) as conn:
        rows = conn.execute(
            "SELECT state, COUNT(*) AS count FROM channel_deliveries GROUP BY state"
        ).fetchall()
    return {str(row["state"]): int(row["count"]) for row in rows}


def channels_in_state(event_id: str, state: str, *, path: Path | None = None) -> frozenset[str]:
    init_schema(path)
    with _managed_connection(path) as conn:
        rows = conn.execute(
            "SELECT channel FROM channel_deliveries WHERE event_id = ? AND state = ?",
            (event_id, state),
        ).fetchall()
    return frozenset(str(row["channel"]) for row in rows)


def get_channel_state(event_id: str, channel: str, *, path: Path | None = None) -> str | None:
    init_schema(path)
    with _managed_connection(path) as conn:
        row = conn.execute(
            "SELECT state FROM channel_deliveries WHERE event_id = ? AND channel = ?",
            (event_id, channel),
        ).fetchone()
    return str(row["state"]) if row is not None else None


def get_channel_receipts(
    event_id: str, channel: str, *, path: Path | None = None
) -> dict[str, str | None]:
    init_schema(path)
    with _managed_connection(path) as conn:
        row = conn.execute(
            "SELECT destination_ref, acceptance_receipt, delivery_receipt, observation_receipt, "
            "terminal_disposition, last_error_category AS error_category, backoff_until "
            "FROM channel_deliveries "
            "WHERE event_id = ? AND channel = ?",
            (event_id, channel),
        ).fetchone()
    if row is None:
        raise KeyError((event_id, channel))
    return {key: cast(str | None, row[key]) for key in row.keys()}


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("receipt evidence must be canonical JSON") from exc


def _sha256_json(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _parse_json_object(encoded: str, *, label: str) -> dict[str, object]:
    value = cast(object, json.loads(encoded))
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not an object")
    raw = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in raw):
        raise ValueError(f"{label} has a non-string key")
    return {cast(str, key): item for key, item in raw.items()}


def _unknown_delivery_evidence(row: sqlite3.Row, event_id: str, channel: str) -> dict[str, object]:
    if str(row["state"]) != "outcome_unknown":
        raise ValueError("channel does not have outcome_unknown evidence")
    original_provider_reference = cast(str | None, row["destination_ref"])
    if not original_provider_reference:
        raise ValueError("outcome_unknown evidence has no provider reference")
    payload_digest = cast(str | None, row["payload_digest"])
    if not payload_digest:
        event = StoredEvent.model_validate_json(str(row["payload_json"]))
        payload_digest = event_payload_digest(event)
    return {
        "schema": "UnknownDeliveryEvidenceV1",
        "target": {"event_id": event_id, "channel": channel},
        "event_payload_digest": payload_digest,
        "state": "outcome_unknown",
        "provider_reference": original_provider_reference,
        "error_category": cast(str | None, row["last_error_category"]),
        "attempt_count": int(row["attempt_count"]),
    }


def _select_unknown_delivery(
    conn: sqlite3.Connection,
    event_id: str,
    channel: str,
) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT
            channel_deliveries.state,
            channel_deliveries.destination_ref,
            channel_deliveries.last_error_category,
            channel_deliveries.attempt_count,
            durable_events.status AS event_status,
            durable_events.payload_digest,
            durable_events.payload_json
        FROM channel_deliveries
        JOIN durable_events ON durable_events.event_id = channel_deliveries.event_id
        WHERE channel_deliveries.event_id = ? AND channel_deliveries.channel = ?
        """,
        (event_id, channel),
    ).fetchone()
    if row is None:
        raise KeyError((event_id, channel))
    return row


def unknown_delivery_evidence_digest(
    event_id: str,
    channel: str,
    *,
    path: Path | None = None,
) -> str:
    """Digest the immutable unknown-outcome evidence for exact reconciliation binding."""
    init_schema(path)
    with _managed_connection(path) as conn:
        row = _select_unknown_delivery(conn, event_id, channel)
        return _sha256_json(_unknown_delivery_evidence(row, event_id, channel))


def read_unknown_delivery_context(
    event_id: str,
    channel: str,
    *,
    path: Path,
) -> dict[str, object]:
    """Read reconciliation context without creating or migrating SQLite state."""
    canonical_path = path.expanduser().resolve(strict=True)
    connection = sqlite3.connect(f"{canonical_path.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = _select_unknown_delivery(connection, event_id, channel)
        evidence = _unknown_delivery_evidence(row, event_id, channel)
        return {
            "database_path": str(canonical_path),
            "event_status": str(row["event_status"]),
            "event_payload_digest": evidence["event_payload_digest"],
            "unknown_evidence_digest": _sha256_json(evidence),
            "original_provider_reference": evidence["provider_reference"],
        }
    finally:
        connection.close()


def read_channel_reconciliation_state(
    event_id: str,
    channel: str,
    *,
    path: Path,
) -> dict[str, object]:
    """Read terminal reconciliation state without creating or migrating SQLite state."""
    canonical_path = path.expanduser().resolve(strict=True)
    connection = sqlite3.connect(f"{canonical_path.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT
                durable_events.status AS event_status,
                channel_reconciliation_receipts.receipt_json
            FROM durable_events
            LEFT JOIN channel_reconciliation_receipts
              ON channel_reconciliation_receipts.event_id = durable_events.event_id
             AND channel_reconciliation_receipts.channel = ?
            WHERE durable_events.event_id = ?
            """,
            (channel, event_id),
        ).fetchone()
        if row is None:
            raise KeyError(event_id)
        encoded_receipt = cast(str | None, row["receipt_json"])
        return {
            "database_path": str(canonical_path),
            "event_status": str(row["event_status"]),
            "receipt": (
                _parse_json_object(
                    encoded_receipt,
                    label="stored reconciliation receipt",
                )
                if encoded_receipt is not None
                else None
            ),
        }
    finally:
        connection.close()


def get_channel_reconciliation_receipt(
    event_id: str,
    channel: str,
    *,
    path: Path | None = None,
) -> dict[str, object] | None:
    """Read the append-only reconciliation receipt for one target, if present."""
    init_schema(path)
    with _managed_connection(path) as conn:
        row = conn.execute(
            "SELECT receipt_json FROM channel_reconciliation_receipts "
            "WHERE event_id = ? AND channel = ?",
            (event_id, channel),
        ).fetchone()
    if row is None:
        return None
    return _parse_json_object(
        str(row["receipt_json"]),
        label="stored reconciliation receipt",
    )


def record_channel_reconciliation(
    event_id: str,
    channel: str,
    *,
    reconciliation_id: str,
    expected_unknown_evidence_digest: str,
    expected_original_provider_reference: str,
    terminal_outcome: Literal["reconciled_succeeded", "reconciled_absent"],
    provider_reference: str,
    readback_result: Mapping[str, object],
    artifact_digest: str,
    provider_idempotency_key: str,
    path: Path | None = None,
) -> dict[str, object]:
    """Append exact provider readback and terminally resolve one unknown outcome."""
    for name, value in (
        ("event_id", event_id),
        ("channel", channel),
        ("reconciliation_id", reconciliation_id),
        ("expected_unknown_evidence_digest", expected_unknown_evidence_digest),
        ("expected_original_provider_reference", expected_original_provider_reference),
        ("provider_reference", provider_reference),
        ("artifact_digest", artifact_digest),
        ("provider_idempotency_key", provider_idempotency_key),
    ):
        if not value or value != value.strip():
            raise ValueError(f"{name} must be a non-empty exact value")
    if terminal_outcome not in {"reconciled_succeeded", "reconciled_absent"}:
        raise ValueError("unsupported reconciliation terminal outcome")
    canonical_readback = _parse_json_object(
        _canonical_json(dict(readback_result)),
        label="readback_result",
    )
    if not canonical_readback:
        raise ValueError("readback_result must be a non-empty object")
    expected_provider_outcome = (
        "accepted" if terminal_outcome == "reconciled_succeeded" else "absent"
    )
    required_readback = {
        "event_id": event_id,
        "channel": channel,
        "provider_outcome": expected_provider_outcome,
        "provider_reference": provider_reference,
    }
    for key, expected in required_readback.items():
        if canonical_readback.get(key) != expected:
            raise ValueError(f"readback_result {key} does not match reconciliation target")
    readback_digest = _sha256_json(canonical_readback)

    init_schema(path)
    with _managed_connection(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        unknown_row = _select_unknown_delivery(conn, event_id, channel)
        unknown_evidence = _unknown_delivery_evidence(unknown_row, event_id, channel)
        actual_evidence_digest = _sha256_json(unknown_evidence)
        if expected_unknown_evidence_digest != actual_evidence_digest:
            raise ValueError("unknown evidence digest does not match current evidence")
        actual_original_reference = str(unknown_evidence["provider_reference"])
        if expected_original_provider_reference != actual_original_reference:
            raise ValueError("original provider reference does not match current evidence")

        existing = conn.execute(
            """
            SELECT *
            FROM channel_reconciliation_receipts
            WHERE reconciliation_id = ? OR (event_id = ? AND channel = ?)
            """,
            (reconciliation_id, event_id, channel),
        ).fetchone()
        if existing is not None:
            exact_replay = (
                str(existing["reconciliation_id"]) == reconciliation_id
                and str(existing["event_id"]) == event_id
                and str(existing["channel"]) == channel
                and str(existing["original_unknown_evidence_digest"]) == actual_evidence_digest
                and str(existing["original_provider_reference"]) == actual_original_reference
                and str(existing["terminal_outcome"]) == terminal_outcome
                and str(existing["provider_reference"]) == provider_reference
                and str(existing["readback_json"]) == _canonical_json(canonical_readback)
                and str(existing["readback_digest"]) == readback_digest
            )
            stored_receipt = _parse_json_object(
                str(existing["receipt_json"]),
                label="stored reconciliation receipt",
            )
            exact_replay = (
                exact_replay
                and stored_receipt.get("artifact_digest") == artifact_digest
                and stored_receipt.get("provider_idempotency_key") == provider_idempotency_key
            )
            if not exact_replay:
                raise ValueError("event, channel, or reconciliation id already has reconciliation")
            return stored_receipt

        if str(unknown_row["event_status"]) != "reconciliation_required":
            raise ValueError("event is not awaiting reconciliation")
        recorded_at = isoformat()
        receipt: dict[str, object] = {
            "schema": "ChannelReconciliationReceiptV1",
            "action_id": reconciliation_id,
            "target": {"event_id": event_id, "channel": channel},
            "original_unknown_evidence_digest": actual_evidence_digest,
            "original_provider_reference": actual_original_reference,
            "terminal_outcome": terminal_outcome,
            "provider_reference": provider_reference,
            "readback_result": canonical_readback,
            "readback_digest": readback_digest,
            "artifact_digest": artifact_digest,
            "provider_idempotency_key": provider_idempotency_key,
            "recorded_at": recorded_at,
        }
        conn.execute(
            """
            INSERT INTO channel_reconciliation_receipts (
                reconciliation_id,
                event_id,
                channel,
                original_unknown_evidence_digest,
                original_provider_reference,
                terminal_outcome,
                provider_reference,
                readback_json,
                readback_digest,
                receipt_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reconciliation_id,
                event_id,
                channel,
                actual_evidence_digest,
                actual_original_reference,
                terminal_outcome,
                provider_reference,
                _canonical_json(canonical_readback),
                readback_digest,
                _canonical_json(receipt),
                recorded_at,
            ),
        )
        cursor = conn.execute(
            """
            UPDATE durable_events
            SET status = ?,
                lease_until = NULL,
                next_attempt_at = NULL,
                updated_at = ?
            WHERE event_id = ? AND status = 'reconciliation_required'
            """,
            (terminal_outcome, recorded_at, event_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("reconciliation transition no longer matches current event state")
        return receipt


def recent_channel_acceptance_times(
    *, path: Path | None = None, at: datetime | None = None
) -> dict[str, tuple[datetime, ...]]:
    """Return accepted push/Slack timestamps from the preceding hour."""
    end = at or datetime.now(UTC)
    start = end - timedelta(hours=1)
    init_schema(path)
    with _managed_connection(path) as conn:
        rows = conn.execute(
            "SELECT channel, accepted_at FROM channel_deliveries "
            "WHERE channel IN ('push', 'slack') AND accepted_at > ? AND accepted_at <= ? "
            "ORDER BY accepted_at",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    result: dict[str, list[datetime]] = {"push": [], "slack": []}
    for row in rows:
        channel = str(row["channel"])
        result[channel].append(datetime.fromisoformat(str(row["accepted_at"])))
    return {channel: tuple(timestamps) for channel, timestamps in result.items()}


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
    with _managed_connection(path) as conn:
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
    with _managed_connection(path) as conn:
        row = conn.execute(
            "SELECT cursor_value FROM consumer_cursors WHERE consumer = ?", (consumer,)
        ).fetchone()
    return int(row["cursor_value"]) if row is not None else None


def advance_consumer_cursor(consumer: str, cursor_value: int, *, path: Path | None = None) -> None:
    """Advance monotonically; retries after a crash remain safe by event id."""
    init_schema(path)
    with _managed_connection(path) as conn:
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
    if not event.producer or "log" not in event.required_destinations:
        raise MissingEventAuthorityError(
            "new durable events require an explicit producer and destination authority"
        )
    init_schema(path)
    now = isoformat()
    payload_json = event.model_dump_json()
    payload_digest = event_payload_digest(event)
    with _managed_connection(path) as conn:
        # Serialize the idempotency decision with the insert. A deferred
        # transaction permits conflicting callers to observe no row before
        # INSERT OR IGNORE silently chooses one payload.
        conn.execute("BEGIN IMMEDIATE")
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
            "SELECT payload_json, payload_digest FROM durable_events WHERE event_id = ?",
            (event.event_id,),
        ).fetchone()

    if row is None:
        raise RuntimeError("durable inbox insert did not return the accepted event")
    accepted_event = StoredEvent.model_validate_json(str(row["payload_json"]))
    accepted_digest = str(row["payload_digest"] or event_payload_digest(accepted_event))
    if accepted_digest != payload_digest:
        raise IdempotencyConflictError(
            f"event_id {event.event_id!r} already exists with a different payload digest"
        )
    return accepted_event


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
    with _managed_connection(path) as conn:
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
        event = StoredEvent.model_validate_json(str(row["payload_json"]))
        if not event.producer or "log" not in event.required_destinations:
            conn.execute(
                """
                UPDATE durable_events
                SET status = 'dead_lettered',
                    lease_until = NULL,
                    next_attempt_at = NULL,
                    dead_lettered_at = ?,
                    last_error = 'legacy durable row lacks authenticated authority; reconciliation required',
                    last_error_type = 'MissingEventAuthority',
                    updated_at = ?
                WHERE event_id = ?
                """,
                (now, now, event_id),
            )
            conn.commit()
            return None
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
            event=event,
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
    expected_attempt_count: int,
    outcome: DurableOutcome,
    classified_level: Level | None,
    path: Path | None = None,
) -> None:
    now = isoformat()
    with _managed_connection(path) as conn:
        cursor = conn.execute(
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
              AND status = 'processing'
              AND attempt_count = ?
            """,
            (outcome, now, classified_level, now, event_id, expected_attempt_count),
        )
        if cursor.rowcount != 1:
            raise ValueError("delivery completion no longer matches the claimed attempt")


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
    with _managed_connection(path) as conn:
        if record.attempt_count >= record.max_attempts:
            cursor = conn.execute(
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
                  AND status = 'processing'
                  AND attempt_count = ?
                """,
                (
                    now,
                    error_text,
                    error_type,
                    now,
                    record.event_id,
                    record.attempt_count,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("processing failure no longer matches the claimed attempt")
            return "dead_lettered"

        delay = retry_delay_seconds(record.attempt_count)
        next_attempt_at = isoformat(now_dt + timedelta(seconds=delay))
        cursor = conn.execute(
            """
            UPDATE durable_events
            SET status = 'retry_scheduled',
                lease_until = NULL,
                next_attempt_at = ?,
                last_error = ?,
                last_error_type = ?,
                updated_at = ?
            WHERE event_id = ?
              AND status = 'processing'
              AND attempt_count = ?
            """,
            (
                next_attempt_at,
                error_text,
                error_type,
                now,
                record.event_id,
                record.attempt_count,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("processing failure no longer matches the claimed attempt")
        return "retry_scheduled"


def record_processing_outcome_unknown(
    record: DurableEventRecord,
    error: BaseException,
    *,
    path: Path | None = None,
) -> DurableEventStatus:
    """Stop automatic retries until provider outcome is explicitly reconciled."""
    now = isoformat()
    error_text = str(error)[:1000] or error.__class__.__name__
    with _managed_connection(path) as conn:
        cursor = conn.execute(
            """
            UPDATE durable_events
            SET status = 'reconciliation_required',
                lease_until = NULL,
                next_attempt_at = NULL,
                last_error = ?,
                last_error_type = ?,
                updated_at = ?
            WHERE event_id = ?
              AND status = 'processing'
              AND attempt_count = ?
            """,
            (
                error_text,
                error.__class__.__name__,
                now,
                record.event_id,
                record.attempt_count,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("outcome_unknown transition no longer matches the claimed attempt")
    return "reconciliation_required"


def record_processing_deferred(
    record: DurableEventRecord,
    retry_at: datetime,
    *,
    path: Path | None = None,
) -> DurableEventStatus:
    """Durably defer without consuming the delivery failure budget."""
    now = isoformat()
    with _managed_connection(path) as conn:
        cursor = conn.execute(
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
              AND status = 'processing'
              AND attempt_count = ?
            """,
            (isoformat(retry_at), now, record.event_id, record.attempt_count),
        )
        if cursor.rowcount != 1:
            raise ValueError("processing deferral no longer matches the claimed attempt")
    return "retry_scheduled"


def reclaim_stale_processing(
    *,
    path: Path | None = None,
    now: datetime | None = None,
) -> int:
    """Move expired processing leases back to retry so restarts do not drop events."""
    init_schema(path)
    now_iso = isoformat(now)
    with _managed_connection(path) as conn:
        conn.execute(
            """
            UPDATE durable_events
            SET status = 'reconciliation_required',
                lease_until = NULL,
                next_attempt_at = NULL,
                last_error = 'provider reconciliation required after interrupted delivery',
                last_error_type = 'DeliveryOutcomeUnknown',
                updated_at = ?
            WHERE status = 'processing'
              AND lease_until IS NOT NULL
              AND lease_until < ?
              AND EXISTS (
                  SELECT 1
                  FROM channel_deliveries
                  WHERE channel_deliveries.event_id = durable_events.event_id
                    AND channel_deliveries.channel IN ('push', 'slack')
                    AND channel_deliveries.state IN ('attempted', 'outcome_unknown')
              )
            """,
            (now_iso, now_iso),
        )
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
    delivery_history_retention_days: int = DELIVERY_HISTORY_RETENTION_DAYS,
) -> int:
    """Apply time/row-bounded retention across the completed-event classes.

    Three bounded classes, plus an immutable core that is never pruned:

    * Log-only / never-delivered ``processed``/``suppressed`` rows: pruned past
      ``processed_retention_days`` outside the newest ``processed_retention_rows``,
      only while no ``channel_deliveries`` reference them.
    * ``dead_lettered`` rows with a disposition and no delivery evidence: pruned
      past ``dead_letter_retention_days``.
    * Delivered-class ``processed``/``suppressed`` history (rows that DO carry
      ``channel_deliveries``): aged out past ``delivery_history_retention_days``
      (operator policy, default 180d) so the dominant row class stops growing
      without bound. The ``channel_deliveries`` FK is ``ON DELETE RESTRICT``, so
      the delivery rows are removed before the parent.

    The immutable core survives INDEFINITELY by construction: any event carrying an
    ``outcome_unknown`` delivery (trigger-protected against deletion) or a
    ``channel_reconciliation_receipts`` row (``ON DELETE RESTRICT`` and immutable)
    is excluded from the delivered-class age-out. Reconciled events retain their
    ``outcome_unknown`` evidence, so both exclusions cover them; the receipt
    exclusion additionally guarantees the age-out can never hit a RESTRICT abort.
    """
    init_schema(path)
    now_dt = now or utc_now()
    processed_cutoff = isoformat(now_dt - timedelta(days=processed_retention_days))
    dead_letter_cutoff = isoformat(now_dt - timedelta(days=dead_letter_retention_days))
    with _managed_connection(path) as conn:
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
        delivery_cutoff = isoformat(now_dt - timedelta(days=delivery_history_retention_days))
        # Delivered-class age-out. Eligible only when every piece of evidence is
        # safely disposable: no outcome_unknown delivery (trigger-immutable) and no
        # reconciliation receipt (RESTRICT + immutable). Delete the child delivery
        # rows first, then the parent, so the RESTRICT FK is satisfied.
        aged_delivered_ids = [
            str(row["event_id"])
            for row in conn.execute(
                """
                SELECT event_id FROM durable_events
                WHERE status IN ('processed', 'suppressed')
                  AND processed_at IS NOT NULL
                  AND processed_at < ?
                  AND EXISTS (
                    SELECT 1 FROM channel_deliveries
                    WHERE channel_deliveries.event_id = durable_events.event_id
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM channel_deliveries
                    WHERE channel_deliveries.event_id = durable_events.event_id
                      AND channel_deliveries.state = 'outcome_unknown'
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM channel_reconciliation_receipts
                    WHERE channel_reconciliation_receipts.event_id
                          = durable_events.event_id
                  )
                """,
                (delivery_cutoff,),
            ).fetchall()
        ]
        deleted_delivered = 0
        for event_id in aged_delivered_ids:
            conn.execute("DELETE FROM channel_deliveries WHERE event_id = ?", (event_id,))
            deleted_delivered += conn.execute(
                "DELETE FROM durable_events WHERE event_id = ?", (event_id,)
            ).rowcount
    return deleted_processed + deleted_dead + deleted_delivered


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
            "reconciliation_required_count": 0,
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
        with _managed_connection(path) as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS count FROM durable_events GROUP BY status"
            ).fetchall()
            counts = {str(row["status"]): int(row["count"]) for row in rows}
            aggregate = conn.execute(
                """
                SELECT
                    MIN(CASE WHEN status IN ('queued', 'processing', 'retry_scheduled')
                        THEN created_at END) AS oldest_pending_at,
                    MIN(CASE
                        WHEN status = 'queued' THEN created_at
                        WHEN status = 'retry_scheduled'
                            AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                            THEN COALESCE(next_attempt_at, created_at)
                        END) AS oldest_actionable_at,
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
                (now, recent_dead_cutoff, now),
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
            "reconciliation_required_count": 0,
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
    reconciliation_required = counts.get("reconciliation_required", 0)
    processed = counts.get("processed", 0)
    suppressed = counts.get("suppressed", 0)
    dead = counts.get("dead_lettered", 0)
    unresolved_dead = int(aggregate["unresolved_dead_letter_count"] or 0)
    recent_dead = int(aggregate["recent_dead_letter_count"] or 0)
    oldest_pending_at = cast(str | None, aggregate["oldest_pending_at"])
    oldest_actionable_at = cast(str | None, aggregate["oldest_actionable_at"])
    stale_processing = int(aggregate["stale_processing_count"] or 0)
    oldest_pending_dt = _parse_iso(oldest_pending_at)
    oldest_actionable_dt = _parse_iso(oldest_actionable_at)
    oldest_pending_age_seconds: float | None = None
    if oldest_pending_dt is not None:
        oldest_pending_age_seconds = max(0.0, (now_dt - oldest_pending_dt).total_seconds())

    stale_backlog = (
        oldest_actionable_dt is not None
        and (now_dt - oldest_actionable_dt).total_seconds() > BACKLOG_DEGRADED_AFTER_SECONDS
    )
    if reconciliation_required > 0:
        status = "degraded"
        next_action = "Reconcile every outcome-unknown delivery before any provider retry."
    elif unresolved_dead > 0:
        status = "degraded"
        next_action = "Review and disposition every unresolved dead-lettered event."
    elif stale_processing > 0:
        status = "degraded"
        next_action = "Reclaim stale processing leases, then verify the worker drains the inbox."
    elif stale_backlog:
        status = "degraded"
        next_action = "Inspect the durable inbox worker; due events are not draining."
    elif retry_scheduled > 0:
        status = "ok"
        next_action = "Deferred events are waiting for their scheduled retry times."
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
        "reconciliation_required_count": reconciliation_required,
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
