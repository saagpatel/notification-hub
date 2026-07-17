"""Tests for the FastAPI server endpoints."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

import notification_hub.server as server_mod
from notification_hub.channels import ChannelDeliveryResult
from notification_hub.config import PolicyConfig, RetentionPolicy
from notification_hub.durable_inbox import (
    claim_next_due_event,
    get_channel_receipts,
    get_channel_state,
    get_event,
)
from notification_hub.pipeline import (
    DeliveryError,
    get_suppression_engine,
    reset_suppression_engine,
)
from notification_hub.server import app


@contextmanager
def _mock_channels():
    """Mock all delivery channels so server tests don't fire real notifications."""
    with (
        patch("notification_hub.pipeline.has_slack_webhook_configured", return_value=True),
        patch("notification_hub.pipeline.send_push", return_value=True),
        patch(
            "notification_hub.pipeline.send_push_with_result",
            return_value=ChannelDeliveryResult(True, receipt="terminal-notifier:exit:0"),
        ),
        patch("notification_hub.pipeline.send_slack", return_value=True),
        patch(
            "notification_hub.pipeline.send_slack_with_result",
            return_value=ChannelDeliveryResult(True, receipt="slack:webhook:http:2xx"),
        ),
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


async def test_health_endpoint_propagates_delivery_degradation(client: AsyncClient) -> None:
    with patch(
        "notification_hub.server.collect_durable_inbox_health",
        return_value={"status": "degraded", "unresolved_dead_letter_count": 1},
    ):
        resp = await client.get("/health")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "degraded"
    assert data["durable_inbox"]["unresolved_dead_letter_count"] == 1


async def test_health_endpoint_propagates_bridge_cursor_failure(
    client: AsyncClient,
) -> None:
    with (
        patch("notification_hub.server.bridge_cursor_enabled", return_value=True),
        patch.object(server_mod, "_bridge_cursor_task") as cursor_task,
        patch.dict(
            server_mod._bridge_cursor_status,
            {
                "consecutive_failures": 2,
                "last_success_at": "2026-07-17T00:00:00Z",
                "last_error_at": "2026-07-17T00:00:04Z",
                "last_error_type": "OperationalError",
            },
            clear=True,
        ),
    ):
        cursor_task.done.return_value = False
        resp = await client.get("/health")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "degraded"
    assert data["bridge_cursor_health"]["status"] == "degraded"
    assert data["bridge_cursor_health"]["consecutive_failures"] == 2


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
    assert data["durable_inbox"]["status"] == "ok"


async def test_health_details_propagates_delivery_degradation(client: AsyncClient) -> None:
    with (
        patch(
            "notification_hub.server.collect_runtime_readiness",
            return_value={
                "retention": {},
                "producer_outbox": {"status": "ok"},
            },
        ),
        patch(
            "notification_hub.server.collect_durable_inbox_health",
            return_value={"status": "degraded", "unresolved_dead_letter_count": 1},
        ),
        patch("notification_hub.server.get_suppression_engine") as mock_engine,
        patch(
            "notification_hub.server.get_retention_runtime_status",
            return_value={
                "last_checked_at": None,
                "last_status": "ok",
                "last_rotated": False,
                "last_archive_path": None,
            },
        ),
    ):
        mock_engine.return_value.snapshot.return_value = {}
        resp = await client.get("/health/details")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "degraded"
    assert data["durable_inbox"]["status"] == "degraded"


async def test_health_distinguishes_bridge_cursor_from_markdown_watcher(
    client: AsyncClient,
) -> None:
    with (
        patch("notification_hub.server.bridge_cursor_enabled", return_value=True),
        patch.object(server_mod, "_bridge_cursor_task") as cursor_task,
    ):
        cursor_task.done.return_value = False
        response = await client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["watcher_active"] is False
    assert data["bridge_cursor_enabled"] is True
    assert data["bridge_cursor_active"] is True


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


async def test_create_event_persists_before_ack_with_stable_event_id(client: AsyncClient) -> None:
    payload = {
        "source": "codex",
        "level": "info",
        "title": "Durable ack",
        "body": "Ack only after SQLite commit.",
        "project": "notification-hub",
    }

    resp = await client.post("/events", json=payload)

    assert resp.status_code == 201
    event_id = resp.json()["event_id"]
    record = get_event(event_id)
    assert record is not None
    assert record.event.event_id == event_id
    assert record.status == "queued"
    assert record.event.title == "Durable ack"


async def test_create_event_identical_retry_returns_original_receipt(client: AsyncClient) -> None:
    payload = {
        "event_id": "producer:stable:0001",
        "source": "codex",
        "level": "info",
        "title": "Stable retry",
        "body": "Same payload, same receipt.",
        "timestamp": "2026-07-12T12:00:00Z",
    }

    first = await client.post("/events", json=payload)
    second = await client.post("/events", json=payload)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["event_id"] == "producer:stable:0001"
    assert second.json() == first.json()


async def test_create_event_persists_explicit_producer(client: AsyncClient) -> None:
    payload = {
        "event_id": "personal-ops:fixture:0001",
        "source": "personal-ops",
        "producer": "personal-ops",
        "source_revision": "fixture-revision-1",
        "event_type": "fixture.delivery",
        "level": "info",
        "title": "Producer envelope",
        "body": "Producer identity must survive intake.",
    }

    response = await client.post("/events", json=payload)

    assert response.status_code == 201
    record = get_event("personal-ops:fixture:0001")
    assert record is not None
    assert record.event.producer == "personal-ops"
    assert record.event.source_revision == "fixture-revision-1"
    assert record.event.event_type == "fixture.delivery"


async def test_durable_worker_persists_transport_acceptance_receipts(
    client: AsyncClient,
) -> None:
    payload = {
        "event_id": "personal-ops:fixture:acceptance",
        "source": "personal-ops",
        "producer": "personal-ops",
        "source_revision": "fixture-acceptance-1",
        "event_type": "fixture.acceptance",
        "level": "urgent",
        "title": "Approval required",
        "body": "Persist transport acceptance evidence.",
    }
    response = await client.post("/events", json=payload)
    assert response.status_code == 201
    claimed = claim_next_due_event()
    assert claimed is not None

    with (
        _mock_channels(),
        patch.object(get_suppression_engine(), "is_quiet_hours", return_value=False),
    ):
        server_mod._process_durable_record(claimed)

    push = get_channel_receipts(claimed.event_id, "push")
    slack = get_channel_receipts(claimed.event_id, "slack")
    assert push["acceptance_receipt"] == "terminal-notifier:exit:0"
    assert slack["acceptance_receipt"] == "slack:webhook:http:2xx"


async def test_durable_worker_honors_log_only_destination_contract(
    client: AsyncClient,
) -> None:
    payload = {
        "event_id": "personal-ops:daemon.stopping:fixture",
        "source": "personal-ops",
        "producer": "personal-ops",
        "source_revision": "fixture-stop-1",
        "event_type": "daemon.stopping",
        "level": "normal",
        "title": "Daemon Stopping",
        "body": "personal-ops received SIGTERM",
        "project": "personal-ops",
        "required_destinations": ["log"],
    }
    response = await client.post("/events", json=payload)
    assert response.status_code == 201
    claimed = claim_next_due_event()
    assert claimed is not None

    with (
        patch("notification_hub.pipeline.has_slack_webhook_configured", return_value=True),
        patch("notification_hub.pipeline.send_push_with_result") as mock_push,
        patch("notification_hub.pipeline.send_slack_with_result") as mock_slack,
    ):
        server_mod._process_durable_record(claimed)

    assert get_channel_state(claimed.event_id, "push") is None
    assert get_channel_state(claimed.event_id, "slack") is None
    mock_push.assert_not_called()
    mock_slack.assert_not_called()
    record = get_event(claimed.event_id)
    assert record is not None
    assert record.status == "processed"


async def test_durable_worker_persists_secret_safe_transport_failure_category(
    client: AsyncClient,
) -> None:
    payload = {
        "event_id": "personal-ops:fixture:slack-400",
        "source": "personal-ops",
        "producer": "personal-ops",
        "source_revision": "fixture-slack-400-1",
        "event_type": "fixture.failure",
        "level": "normal",
        "title": "Fixture only",
        "body": "Persist a bounded failure category.",
    }
    response = await client.post("/events", json=payload)
    assert response.status_code == 201
    claimed = claim_next_due_event()
    assert claimed is not None

    with (
        patch("notification_hub.pipeline.has_slack_webhook_configured", return_value=True),
        patch(
            "notification_hub.pipeline.send_slack_with_result",
            return_value=ChannelDeliveryResult(False, error_category="slack_http_4xx"),
        ),
        pytest.raises(DeliveryError),
    ):
        server_mod._process_durable_record(claimed)

    receipts = get_channel_receipts(claimed.event_id, "slack")
    assert receipts["acceptance_receipt"] is None
    assert receipts["error_category"] == "slack_http_4xx"


async def test_create_event_conflicting_retry_returns_409(client: AsyncClient) -> None:
    payload = {
        "event_id": "producer:stable:0002",
        "source": "codex",
        "level": "info",
        "title": "Stable retry",
        "body": "Original payload.",
        "timestamp": "2026-07-12T12:00:00Z",
    }
    first = await client.post("/events", json=payload)
    conflicting = await client.post("/events", json={**payload, "body": "Conflicting payload."})

    assert first.status_code == 201
    assert conflicting.status_code == 409
    assert "different payload digest" in conflicting.json()["detail"]


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
    for source in ("cc", "codex", "claude_ai", "bridge_watcher", "personal-ops", "notion-os"):
        payload = {
            "source": source,
            "level": "info",
            "title": f"Test from {source}",
            "body": "Source validation check",
        }
        with _mock_channels():
            resp = await client.post("/events", json=payload)
        assert resp.status_code == 201, f"Failed for source: {source}"


async def test_create_event_normalizes_warn_level(client: AsyncClient) -> None:
    payload = {
        "source": "notion-os",
        "level": "warn",
        "title": "Warning alias",
        "body": "Producer sent warn instead of normal",
    }
    with _mock_channels():
        resp = await client.post("/events", json=payload)

    assert resp.status_code == 201
    assert resp.json()["level"] == "normal"


async def test_create_event_accepts_bridge_underscore_source(client: AsyncClient) -> None:
    # A producer that reuses its bridge-db caller id (`personal_ops`) instead of the
    # hub's hyphenated wire form must be accepted, not 422'd (F3).
    payload = {
        "source": "personal_ops",
        "level": "info",
        "title": "Underscore source",
        "body": "Producer reused its bridge-db caller id form",
    }
    with _mock_channels():
        resp = await client.post("/events", json=payload)

    assert resp.status_code == 201


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
