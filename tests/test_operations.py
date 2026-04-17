"""Tests for smoke and retention operator actions."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

import notification_hub.operations as ops_mod
from notification_hub.models import StoredEvent
from notification_hub.operations import run_retention, run_smoke_check


def test_smoke_check_reports_success_when_event_hits_log() -> None:
    response = MagicMock()
    response.status_code = 201
    response.json.return_value = {"event_id": "abc123"}

    with (
        patch("notification_hub.operations.httpx.post", return_value=response),
        patch(
            "notification_hub.operations.read_jsonl",
            return_value=[StoredEvent(source="codex", level="info", title="x", body="y", event_id="abc123")],
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


def test_retention_noop_when_log_missing() -> None:
    report = run_retention(max_events=10, keep_archives=2)

    assert report["status"] == "ok"
    assert report["rotated"] is False
    assert report["events_before"] == 0


def test_retention_archives_older_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events_dir = tmp_path / "notification-hub"
    events_dir.mkdir()
    events_log = events_dir / "events.jsonl"
    lines = [
        json.dumps({"event_id": f"id-{i}", "source": "codex", "level": "info", "title": "t", "body": "b"})
        + "\n"
        for i in range(5)
    ]
    events_log.write_text("".join(lines), encoding="utf-8")

    monkeypatch.setattr(ops_mod, "EVENTS_DIR", events_dir)
    monkeypatch.setattr(ops_mod, "EVENTS_LOG", events_log)

    report = run_retention(max_events=2, keep_archives=5)

    assert report["rotated"] is True
    assert report["events_before"] == 5
    assert report["events_after"] == 2
    assert report["archived_events"] == 3
    archive_path = report["archive_path"]
    assert isinstance(archive_path, str)
    assert Path(archive_path).exists()
    assert len(events_log.read_text(encoding="utf-8").splitlines()) == 2
