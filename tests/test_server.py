"""Tests for the FastAPI server endpoints."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from notification_hub.server import app


@pytest.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health_endpoint(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "uptime_seconds" in data
    assert "events_processed" in data


async def test_create_event_valid(client: AsyncClient) -> None:
    payload = {
        "source": "cc",
        "level": "info",
        "title": "Test event",
        "body": "This is a test notification",
        "project": "notification-hub",
    }
    resp = await client.post("/events", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] is True
    assert data["level"] == "info"
    assert "event_id" in data


async def test_create_event_minimal(client: AsyncClient) -> None:
    payload = {
        "source": "codex",
        "level": "urgent",
        "title": "Alert",
        "body": "Something needs attention",
    }
    resp = await client.post("/events", json=payload)
    assert resp.status_code == 200
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
        resp = await client.post("/events", json=payload)
        assert resp.status_code == 200, f"Failed for source: {source}"
