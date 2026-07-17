from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path
from types import ModuleType

import pytest

import notification_hub.producer_health as producer_health
from notification_hub.producer_health import collect_producer_health

PRODUCER = Path(__file__).resolve().parents[1] / "ops/hooks/notification-hub-producer.py"


def _producer_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("producer_health_fixture", PRODUCER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_health_read_closes_read_only_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "producer.sqlite3"
    path.touch()

    class Cursor:
        def fetchone(self) -> tuple[int, int, int, int, int, int, None, int]:
            return (0, 0, 0, 0, 0, 0, None, 0)

    class TrackingConnection:
        closed = False

        def execute(self, _sql: str) -> Cursor:
            return Cursor()

        def close(self) -> None:
            self.closed = True

    def connect_fixture(_database: str, *, uri: bool = False) -> TrackingConnection:
        assert uri is True
        return connection

    connection = TrackingConnection()
    monkeypatch.setattr(sqlite3, "connect", connect_fixture)

    health = producer_health.collect_producer_health(path)

    assert health["status"] == "ok"
    assert connection.closed is True


def test_queued_producer_event_degrades_health_without_deleting_it(tmp_path: Path) -> None:
    path = tmp_path / "producer.sqlite3"
    producer = _producer_module()
    producer.enqueue(
        {
            "event_id": "producer:health:1",
            "source": "codex",
            "level": "normal",
            "title": "Queued",
            "body": "Hub unavailable",
        },
        path=path,
    )

    health = collect_producer_health(path)

    assert health["status"] == "degraded"
    assert health["queued_count"] == 1
    assert health["accepted_count"] == 0
    assert health["dead_letter_count"] == 0
    assert health["rejected_count"] == 0
