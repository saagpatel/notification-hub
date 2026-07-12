"""Durable hook-producer retry tests with no live network destinations."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest

PRODUCER = Path(__file__).resolve().parents[1] / "ops/hooks/notification-hub-producer.py"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("notification_hub_producer", PRODUCER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload(event_id: str = "producer:fixture:1") -> dict[str, object]:
    return {
        "event_id": event_id,
        "source": "personal-ops",
        "level": "normal",
        "title": "Producer fixture",
        "body": "Retry after hub downtime.",
    }


class AcceptedResponse:
    status = 201

    def __enter__(self) -> AcceptedResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_hub_downtime_persists_event_and_retry_accepts_same_id(tmp_path: Path) -> None:
    module = _module()
    outbox = tmp_path / "producer.sqlite3"
    module.enqueue(_payload(), path=outbox)
    with patch.object(module.urllib.request, "urlopen", side_effect=TimeoutError):
        assert module.deliver_due(path=outbox) == 0
    with sqlite3.connect(outbox) as conn:
        queued = conn.execute(
            "SELECT state, attempt_count FROM producer_events WHERE event_id = ?",
            ("producer:fixture:1",),
        ).fetchone()
        conn.execute("UPDATE producer_events SET next_attempt_at = 0")
    assert queued == ("queued", 1)

    with patch.object(module.urllib.request, "urlopen", return_value=AcceptedResponse()):
        assert module.deliver_due(path=outbox) == 1
    with sqlite3.connect(outbox) as conn:
        accepted = conn.execute(
            "SELECT state, attempt_count, acceptance_receipt FROM producer_events"
        ).fetchone()
    assert accepted == ("accepted", 2, "http:201")


def test_http_timeout_after_possible_acceptance_retries_idempotently(tmp_path: Path) -> None:
    module = _module()
    outbox = tmp_path / "producer.sqlite3"
    payload = _payload("producer:timeout-after-acceptance")
    module.enqueue(payload, path=outbox)
    with patch.object(module.urllib.request, "urlopen", side_effect=TimeoutError):
        module.deliver_due(path=outbox)
    module.enqueue(json.loads(json.dumps(payload)), path=outbox)
    with sqlite3.connect(outbox) as conn:
        conn.execute("UPDATE producer_events SET next_attempt_at = 0")
    with patch.object(module.urllib.request, "urlopen", return_value=AcceptedResponse()):
        assert module.deliver_due(path=outbox) == 1
    with sqlite3.connect(outbox) as conn:
        assert conn.execute("SELECT COUNT(*) FROM producer_events").fetchone() == (1,)


def test_conflicting_payload_for_same_producer_id_is_rejected(tmp_path: Path) -> None:
    module = _module()
    outbox = tmp_path / "producer.sqlite3"
    module.enqueue(_payload(), path=outbox)
    conflicting = {**_payload(), "body": "different"}
    with pytest.raises(ValueError, match="conflicting producer payload"):
        module.enqueue(conflicting, path=outbox)


def test_test_mode_blocks_non_loopback_hub_host(tmp_path: Path) -> None:
    module = _module()
    module.HUB_URL = "https://example.invalid/events"
    outbox = tmp_path / "producer.sqlite3"
    module.enqueue(_payload(), path=outbox)
    with pytest.raises(ValueError, match="blocks non-loopback"):
        module.deliver_due(path=outbox)


def test_permanent_conflict_is_rejected_without_indefinite_retry(tmp_path: Path) -> None:
    module = _module()
    outbox = tmp_path / "producer.sqlite3"
    module.enqueue(_payload("producer:conflict:1"), path=outbox)
    conflict = module.urllib.error.HTTPError(module.HUB_URL, 409, "conflict", {}, None)
    with patch.object(module.urllib.request, "urlopen", side_effect=conflict):
        assert module.deliver_due(path=outbox) == 0
    with sqlite3.connect(outbox) as conn:
        assert conn.execute("SELECT state, attempt_count FROM producer_events").fetchone() == (
            "rejected",
            1,
        )


def test_exhausted_transient_retries_dead_letter_durably(tmp_path: Path) -> None:
    module = _module()
    outbox = tmp_path / "producer.sqlite3"
    module.enqueue(_payload("producer:dead:1"), path=outbox)
    with sqlite3.connect(outbox) as conn:
        conn.execute("UPDATE producer_events SET attempt_count = max_attempts - 1")
    with patch.object(module.urllib.request, "urlopen", side_effect=TimeoutError):
        assert module.deliver_due(path=outbox) == 0
    with sqlite3.connect(outbox) as conn:
        state = conn.execute(
            "SELECT state, attempt_count, dead_lettered_at FROM producer_events"
        ).fetchone()
    assert state[0:2] == ("dead_lettered", module.DEFAULT_MAX_ATTEMPTS)
    assert state[2] is not None
    assert outbox.stat().st_mode & 0o777 == 0o600

    module.disposition("producer:dead:1", "operator_reviewed", "fixture:ticket:1", path=outbox)
    with sqlite3.connect(outbox) as conn:
        disposition = conn.execute(
            "SELECT state, terminal_disposition, disposition_ref FROM producer_events"
        ).fetchone()
    assert disposition == ("dead_lettered", "operator_reviewed", "fixture:ticket:1")


def test_additive_producer_schema_migration_preserves_existing_row(tmp_path: Path) -> None:
    module = _module()
    outbox = tmp_path / "legacy-producer.sqlite3"
    payload = _payload("producer:legacy:1")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    with sqlite3.connect(outbox) as conn:
        conn.execute(
            """
            CREATE TABLE producer_events (
                event_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL,
                payload_digest TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
                attempt_count INTEGER NOT NULL DEFAULT 0, next_attempt_at REAL NOT NULL DEFAULT 0,
                accepted_at REAL, acceptance_receipt TEXT, last_error_category TEXT,
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO producer_events "
            "(event_id, payload_json, payload_digest, created_at, updated_at) "
            "VALUES (?, ?, ?, 1, 1)",
            (payload["event_id"], encoded, module.payload_digest(payload)),
        )

    with module.connect(outbox) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(producer_events)")}
        count = conn.execute("SELECT COUNT(*) FROM producer_events").fetchone()[0]

    assert count == 1
    assert {
        "max_attempts",
        "dead_lettered_at",
        "terminal_disposition",
        "disposition_ref",
        "dispositioned_at",
    } <= columns
