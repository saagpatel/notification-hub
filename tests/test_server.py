"""Tests for the FastAPI server endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

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
            },
        ),
        patch("notification_hub.server.get_suppression_engine") as mock_engine,
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
    for source in ("cc", "codex", "claude_ai", "bridge_watcher"):
        payload = {
            "source": source,
            "level": "info",
            "title": f"Test from {source}",
            "body": "Source validation check",
        }
        with _mock_channels():
            resp = await client.post("/events", json=payload)
        assert resp.status_code == 201, f"Failed for source: {source}"
