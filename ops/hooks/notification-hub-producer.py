#!/usr/bin/env python3
"""Durable local producer outbox for notification-hub hook templates."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HUB_URL = os.environ.get("NOTIFICATION_HUB_URL", "http://127.0.0.1:9199/events")
OUTBOX_PATH = Path(
    os.environ.get(
        "NOTIFICATION_HUB_PRODUCER_OUTBOX",
        str(Path.home() / ".local/share/notification-hub/producer-outbox.sqlite3"),
    )
)
MAX_DRAIN = 20
DEFAULT_MAX_ATTEMPTS = 20
LEGACY_PRODUCER_BY_SOURCE = {"cc": "cc", "codex": "codex"}


class PayloadDefect(ValueError):
    """A stored event is malformed and cannot become valid through retry."""


def load_producer_token(producer_id: str) -> str:
    """Load the producer's owner-private token without placing it in payloads."""
    configured_path = os.environ.get("NOTIFICATION_HUB_PRODUCER_TOKEN_FILE")
    token_path = (
        Path(configured_path).expanduser()
        if configured_path
        else Path.home()
        / ".config"
        / "notification-hub"
        / "producer-tokens"
        / f"{producer_id}.token"
    )
    metadata = token_path.lstat()
    if (
        token_path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o077
    ):
        raise ValueError("producer token file must be owner-private and non-symlinked")
    token = token_path.read_text(encoding="utf-8").strip()
    if (
        not token
        or len(token) > 512
        or any(character.isspace() for character in token)
    ):
        raise ValueError("producer token is invalid")
    return token


def _hub_url_allowed() -> bool:
    if os.environ.get("NOTIFICATION_HUB_TEST_MODE", "").lower() not in {"1", "true", "yes"}:
        return True
    host = urllib.parse.urlparse(HUB_URL).hostname
    return host in {"127.0.0.1", "localhost", "::1"}


def payload_digest(payload: dict[str, object]) -> str:
    canonical = {key: value for key, value in payload.items() if key != "timestamp"}
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _migrate_legacy_queued_producers(conn: sqlite3.Connection) -> None:
    """Bind pre-auth hook rows to their fixed historical producer identity."""
    rows = conn.execute(
        "SELECT event_id, payload_json FROM producer_events WHERE state = 'queued'"
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"]))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or payload.get("producer") is not None:
            continue
        source = payload.get("source")
        if not isinstance(source, str):
            continue
        producer_id = LEGACY_PRODUCER_BY_SOURCE.get(source)
        if producer_id is None:
            continue
        payload["producer"] = producer_id
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        conn.execute(
            "UPDATE producer_events SET payload_json = ?, payload_digest = ? "
            "WHERE event_id = ? AND state = 'queued'",
            (encoded, payload_digest(payload), str(row["event_id"])),
        )


def connect(path: Path = OUTBOX_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS producer_events (
            event_id TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            payload_digest TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'queued',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 20,
            next_attempt_at REAL NOT NULL DEFAULT 0,
            accepted_at REAL,
            acceptance_receipt TEXT,
            last_error_category TEXT,
            dead_lettered_at REAL,
            terminal_disposition TEXT,
            disposition_ref TEXT,
            dispositioned_at REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(producer_events)")}
    if "max_attempts" not in columns:
        conn.execute(
            "ALTER TABLE producer_events ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 20"
        )
    if "dead_lettered_at" not in columns:
        conn.execute("ALTER TABLE producer_events ADD COLUMN dead_lettered_at REAL")
    for name, sql_type in (
        ("terminal_disposition", "TEXT"),
        ("disposition_ref", "TEXT"),
        ("dispositioned_at", "REAL"),
    ):
        if name not in columns:
            conn.execute(f"ALTER TABLE producer_events ADD COLUMN {name} {sql_type}")
    _migrate_legacy_queued_producers(conn)
    os.chmod(path, 0o600)
    return conn


def enqueue(payload: dict[str, object], *, path: Path = OUTBOX_PATH) -> None:
    event_id = payload.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("producer payload requires event_id")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = payload_digest(payload)
    now = time.time()
    with connect(path) as conn:
        existing = conn.execute(
            "SELECT payload_digest FROM producer_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if existing is not None:
            if str(existing["payload_digest"]) != digest:
                raise ValueError("event_id already exists with conflicting producer payload")
            return
        conn.execute(
            "INSERT INTO producer_events "
            "(event_id, payload_json, payload_digest, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (event_id, encoded, digest, now, now),
        )


def _backoff_seconds(attempt_count: int) -> int:
    return min(5 * (2 ** max(0, attempt_count - 1)), 600)


def disposition(event_id: str, decision: str, reference: str, *, path: Path = OUTBOX_PATH) -> None:
    if not decision.strip() or not reference.strip():
        raise ValueError("producer disposition and reference are required")
    with connect(path) as conn:
        row = conn.execute(
            "SELECT state FROM producer_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if row is None:
            raise KeyError(event_id)
        if str(row["state"]) not in {"dead_lettered", "rejected"}:
            raise ValueError("only rejected or dead-lettered producer events can be dispositioned")
        conn.execute(
            "UPDATE producer_events SET terminal_disposition = ?, disposition_ref = ?, "
            "dispositioned_at = ?, updated_at = ? WHERE event_id = ?",
            (decision.strip(), reference.strip(), time.time(), time.time(), event_id),
        )


def deliver_due(*, path: Path = OUTBOX_PATH, limit: int = MAX_DRAIN) -> int:
    """Attempt due rows once; accepted history remains preserved in the outbox."""
    if not _hub_url_allowed():
        raise ValueError("test mode blocks non-loopback notification-hub hosts")
    delivered = 0
    now = time.time()
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT event_id, payload_json, attempt_count, max_attempts FROM producer_events "
            "WHERE state = 'queued' AND next_attempt_at <= ? "
            "ORDER BY next_attempt_at ASC, created_at ASC LIMIT ?",
            (now, max(1, min(limit, MAX_DRAIN))),
        ).fetchall()
        for row in rows:
            event_id = str(row["event_id"])
            attempt_count = int(row["attempt_count"]) + 1
            max_attempts = int(row["max_attempts"])
            try:
                payload = json.loads(str(row["payload_json"]))
                producer_id = (
                    payload.get("producer") if isinstance(payload, dict) else None
                )
                if not isinstance(producer_id, str) or not producer_id:
                    raise PayloadDefect("producer payload requires producer identity")
                token = load_producer_token(producer_id)
                request = urllib.request.Request(
                    HUB_URL,
                    data=str(row["payload_json"]).encode(),
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                        "X-Notification-Hub-Producer": producer_id,
                    },
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=2) as response:
                    status = int(getattr(response, "status", 0))
                if status < 200 or status >= 300:
                    raise urllib.error.HTTPError(HUB_URL, status, "non-success", {}, None)
            except Exception as exc:
                # Stored-payload defects cannot heal through retry. Token-file
                # failures remain transient because an operator can repair them.
                permanent = isinstance(exc, (PayloadDefect, json.JSONDecodeError)) or (
                    isinstance(exc, urllib.error.HTTPError)
                    and 400 <= exc.code < 500
                    and exc.code not in {408, 429}
                )
                exhausted = attempt_count >= max_attempts
                state = "rejected" if permanent else "dead_lettered" if exhausted else "queued"
                conn.execute(
                    "UPDATE producer_events SET state = ?, attempt_count = ?, "
                    "next_attempt_at = ?, last_error_category = ?, dead_lettered_at = ?, "
                    "updated_at = ? WHERE event_id = ?",
                    (
                        state,
                        attempt_count,
                        now + _backoff_seconds(attempt_count),
                        type(exc).__name__,
                        now if state in {"rejected", "dead_lettered"} else None,
                        now,
                        event_id,
                    ),
                )
                continue
            conn.execute(
                "UPDATE producer_events SET state = 'accepted', attempt_count = ?, "
                "accepted_at = ?, acceptance_receipt = ?, last_error_category = NULL, "
                "updated_at = ? WHERE event_id = ?",
                (attempt_count, now, f"http:{status}", now, event_id),
            )
            delivered += 1
    return delivered


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("producer payload must be a JSON object")
        enqueue(payload)
        deliver_due()
    except (OSError, sqlite3.Error, ValueError, json.JSONDecodeError) as exc:
        print(f"notification-hub producer error: {type(exc).__name__}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
