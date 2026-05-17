"""Tests for smoke checks and coordination snapshots."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

from notification_hub.models import StoredEvent
from notification_hub.operations import (
    run_coordination_snapshot,
    run_smoke_check,
)


def test_smoke_check_reports_success_when_event_hits_log() -> None:
    response = MagicMock()
    response.status_code = 201
    response.json.return_value = {"event_id": "abc123"}

    with (
        patch("notification_hub.operations.httpx.post", return_value=response),
        patch(
            "notification_hub.operations.read_jsonl",
            return_value=[
                StoredEvent(source="codex", level="info", title="x", body="y", event_id="abc123")
            ],
        ),
    ):
        report = run_smoke_check()

    assert report["status"] == "ok"
    assert report["event_id"] == "abc123"
    assert report["log_verified"] is True


def test_smoke_check_reports_http_failure() -> None:
    with patch(
        "notification_hub.operations.httpx.post",
        side_effect=httpx.ConnectError("boom"),
    ):
        report = run_smoke_check()

    assert report["status"] == "degraded"
    assert report["response_status"] is None
    assert report["event_id"] is None
    assert report["error"] == "operation failed; inspect local logs for details"


def test_coordination_snapshot_can_save_to_bridge_db(tmp_path: Path) -> None:
    db_path = tmp_path / "bridge.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE system_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                system TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            );
            CREATE VIRTUAL TABLE content_index USING fts5(
                source_type UNINDEXED,
                source_id UNINDEXED,
                text
            );
            """
        )

    inbox_report: dict[str, object] = {
        "status": "ok",
        "hours": 2,
        "events_seen": 0,
        "needs_attention": [],
        "waiting_or_blocked": [],
        "ready": [],
        "completed": [],
        "rollups": [],
        "noise_candidates": [],
        "error": None,
    }
    status_report = {
        "status": "ok",
        "health_url": "http://127.0.0.1:9199/health/details",
        "daemon_reachable": True,
        "watcher_active": True,
        "events_processed": 10,
        "uptime_seconds": 123.4,
        "policy_config_found": True,
        "policy_warning_count": 0,
        "retention_enabled": True,
        "retention_last_status": "ok",
        "runtime_wiring_current": True,
        "push_notifier_available": True,
        "slack_configured": True,
        "slack_delivery_failures": 0,
        "next_action": "No action needed.",
    }

    with (
        patch("notification_hub.operations.run_inbox", return_value=inbox_report),
        patch("notification_hub.operations.run_status", return_value=status_report),
    ):
        report = run_coordination_snapshot(
            hours=2,
            limit=3,
            save_bridge_db=True,
            bridge_db_path=db_path,
        )

    assert report["status"] == "ok"
    assert report["bridge_save"]["status"] == "ok"
    assert report["bridge_save"]["snapshot_id"] == 1
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT system, snapshot_date, data FROM system_snapshots").fetchone()
    assert row is not None
    assert row[0] == "codex"
    assert row[1] == report["bridge_snapshot_date"]
    assert json.loads(row[2])["runtime"]["status"] == "ok"
