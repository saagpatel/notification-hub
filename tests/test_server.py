"""Tests for the FastAPI server endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import contextmanager
import logging
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

import notification_hub.server as server_mod
from notification_hub.config import PolicyConfig, RetentionPolicy
from notification_hub.pipeline import reset_suppression_engine
from notification_hub.server import app


@contextmanager
def _mock_channels():
    """Mock all delivery channels so server tests don't fire real notifications."""
    with (
        patch("notification_hub.pipeline.send_push", return_value=True),
        patch("notification_hub.pipeline.send_slack", return_value=True),
        patch("notification_hub.pipeline.send_slack_digest", return_value=True),
    ):
        yield


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def fresh_suppression() -> None:
    reset_suppression_engine()


async def test_health_endpoint(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "uptime_seconds" in data
    assert "events_processed" in data


async def test_health_details_endpoint(client: AsyncClient) -> None:
    with (
        patch(
            "notification_hub.server.collect_runtime_readiness",
            return_value={
                "delivery": {
                    "push_notifier_available": True,
                    "slack_webhook_configured": False,
                },
                "paths": {
                    "bridge_file_exists": True,
                    "events_dir_exists": True,
                    "events_log_exists": False,
                    "launch_agent_exists": True,
                },
                "config": {
                    "path": "/tmp/config.toml",
                    "exists": False,
                    "load_error": None,
                    "routing_rule_count": 0,
                    "warning_count": 0,
                },
                "retention": {
                    "enabled": True,
                    "interval_minutes": 60,
                    "max_events": 2000,
                    "keep_archives": 10,
                },
            },
        ),
        patch("notification_hub.server.get_suppression_engine") as mock_engine,
        patch(
            "notification_hub.server.get_retention_runtime_status",
            return_value={
                "enabled": True,
                "interval_minutes": 60,
                "max_events": 2000,
                "keep_archives": 10,
                "last_checked_at": "2026-04-17T12:00:00Z",
                "last_status": "ok",
                "last_rotated": False,
                "last_archive_path": None,
            },
        ),
    ):
        mock_engine.return_value.snapshot.return_value = {
            "dedup_entries": 0,
            "queued_for_morning": 0,
            "overflow_buffered": 0,
            "pushes_last_hour": 0,
            "slacks_last_hour": 0,
        }
        resp = await client.get("/health/details")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["delivery"] == {
        "push_notifier_available": True,
        "slack_webhook_configured": False,
    }
    assert data["paths"] == {
        "bridge_file_exists": True,
        "events_dir_exists": True,
        "events_log_exists": False,
        "launch_agent_exists": True,
    }
    assert data["config"] == {
        "path": "/tmp/config.toml",
        "exists": False,
        "load_error": None,
        "routing_rule_count": 0,
        "warning_count": 0,
    }
    assert data["retention"] == {
        "enabled": True,
        "interval_minutes": 60,
        "max_events": 2000,
        "keep_archives": 10,
        "last_checked_at": "2026-04-17T12:00:00Z",
        "last_status": "ok",
        "last_rotated": False,
        "last_archive_path": None,
    }
    assert data["suppression"] == {
        "dedup_entries": 0,
        "queued_for_morning": 0,
        "overflow_buffered": 0,
        "pushes_last_hour": 0,
        "slacks_last_hour": 0,
    }


async def test_create_event_valid(client: AsyncClient) -> None:
    payload = {
        "source": "cc",
        "level": "info",
        "title": "Test event",
        "body": "This is a test notification",
        "project": "notification-hub",
    }
    with _mock_channels():
        resp = await client.post("/events", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["accepted"] is True
    assert data["level"] == "info"
    assert "event_id" in data


async def test_create_event_classified_level_in_response(client: AsyncClient) -> None:
    payload = {
        "source": "cc",
        "level": "info",
        "title": "Security alert",
        "body": "Security finding in auth module",
    }
    with _mock_channels():
        resp = await client.post("/events", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["level"] == "urgent"


async def test_create_event_minimal(client: AsyncClient) -> None:
    payload = {
        "source": "codex",
        "level": "urgent",
        "title": "Alert",
        "body": "Something needs attention",
    }
    with _mock_channels():
        resp = await client.post("/events", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["level"] == "urgent"


async def test_create_event_invalid_source(client: AsyncClient) -> None:
    payload = {
        "source": "unknown_system",
        "level": "info",
        "title": "Bad source",
        "body": "Should fail validation",
    }
    resp = await client.post("/events", json=payload)
    assert resp.status_code == 422


async def test_create_event_validation_logs_invalid_source_value(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = {
        "source": "codex-hook",
        "level": "normal",
        "title": "Bad source",
        "body": "Invalid source should be summarized",
    }
    with caplog.at_level(logging.WARNING, logger="notification_hub.server"):
        resp = await client.post("/events", json=payload)

    assert resp.status_code == 422
    combined = "\n".join(record.getMessage() for record in caplog.records)
    assert "source" in combined
    assert "codex-hook" in combined
    assert "Invalid source should be summarized" not in combined


async def test_create_event_validation_logs_field_without_body(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = {
        "source": "codex",
        "level": "normal",
        "title": "Bad project",
        "body": "Do not log this body",
        "project": "p" * 101,
    }
    with caplog.at_level(logging.WARNING, logger="notification_hub.server"):
        resp = await client.post("/events", json=payload)

    assert resp.status_code == 422
    messages = [record.getMessage() for record in caplog.records]
    assert any("Rejected event payload" in message for message in messages)
    combined = "\n".join(messages)
    assert "project" in combined
    assert "string_too_long" in combined
    assert "Do not log this body" not in combined


async def test_create_event_invalid_level(client: AsyncClient) -> None:
    payload = {
        "source": "cc",
        "level": "critical",
        "title": "Bad level",
        "body": "Should fail validation",
    }
    resp = await client.post("/events", json=payload)
    assert resp.status_code == 422


async def test_create_event_empty_title(client: AsyncClient) -> None:
    payload = {
        "source": "cc",
        "level": "info",
        "title": "",
        "body": "Empty title should fail",
    }
    resp = await client.post("/events", json=payload)
    assert resp.status_code == 422


async def test_create_event_empty_body(client: AsyncClient) -> None:
    payload = {
        "source": "cc",
        "level": "info",
        "title": "Valid title",
        "body": "",
    }
    resp = await client.post("/events", json=payload)
    assert resp.status_code == 422


async def test_create_event_all_sources(client: AsyncClient) -> None:
    for source in ("cc", "codex", "claude_ai", "bridge_watcher", "personal-ops"):
        payload = {
            "source": source,
            "level": "info",
            "title": f"Test from {source}",
            "body": "Source validation check",
        }
        with _mock_channels():
            resp = await client.post("/events", json=payload)
        assert resp.status_code == 201, f"Failed for source: {source}"


def test_run_retention_once_updates_runtime_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        server_mod,
        "get_policy_config",
        lambda: PolicyConfig(
            retention=RetentionPolicy(
                enabled=True,
                interval_minutes=30,
                max_events=111,
                keep_archives=5,
            )
        ),
    )
    def _run_retention(*, max_events: int, keep_archives: int) -> dict[str, object]:
        return {
            "status": "ok",
            "rotated": True,
            "archive_path": "/tmp/archive.jsonl",
            "events_before": 120,
            "events_after": 111,
            "archived_events": 9,
            "deleted_archives": [],
        }

    def _strftime(_format: str, _time_tuple: object) -> str:
        return "2026-04-17T12:00:00Z"

    monkeypatch.setattr(server_mod, "run_retention", _run_retention)
    monkeypatch.setattr(server_mod.time, "strftime", _strftime)

    server_mod.reset_retention_runtime_state()
    server_mod.run_retention_check_once()

    assert server_mod.get_retention_runtime_status() == {
        "enabled": True,
        "interval_minutes": 30,
        "max_events": 111,
        "keep_archives": 5,
        "last_checked_at": "2026-04-17T12:00:00Z",
        "last_status": "ok",
        "last_rotated": True,
        "last_archive_path": "/tmp/archive.jsonl",
    }
