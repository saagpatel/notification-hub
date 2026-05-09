"""Tests for the FastAPI server endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import contextmanager
import logging
from unittest.mock import AsyncMock, patch

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


async def test_review_page_endpoint(client: AsyncClient) -> None:
    resp = await client.get("/review")

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "notification-hub review" in resp.text


async def test_review_data_endpoint_is_read_only(client: AsyncClient) -> None:
    with (
        patch(
            "notification_hub.server.run_inbox",
            return_value={
                "status": "ok",
                "hours": 2,
                "events_seen": 1,
                "needs_attention": [],
                "waiting_or_blocked": [],
                "ready": [],
                "completed": [],
                "rollups": [],
                "noise_candidates": [],
                "error": None,
            },
        ) as mock_inbox,
        patch(
            "notification_hub.server.run_personal_ops_action_export",
            return_value={
                "status": "ok",
                "schema_version": "notification-hub.personal_ops_action_export.v1",
                "generated_at": "2026-05-09T00:00:00+00:00",
                "hours": 2,
                "actions": [],
                "review_package": {
                    "requested": False,
                    "status": "not_requested",
                    "path": None,
                    "error": None,
                },
                "inbox": {
                    "status": "ok",
                    "hours": 2,
                    "events_seen": 1,
                    "needs_attention": [],
                    "waiting_or_blocked": [],
                    "ready": [],
                    "completed": [],
                    "rollups": [],
                    "noise_candidates": [],
                    "error": None,
                },
                "error": None,
            },
        ) as mock_actions,
        patch(
            "notification_hub.server._review_runtime_status",
            new_callable=AsyncMock,
            return_value={
                "status": "ok",
                "health_url": "http://127.0.0.1:9199/health/details",
                "daemon_reachable": True,
                "watcher_active": True,
                "events_processed": 1,
                "uptime_seconds": 1.0,
                "policy_config_found": True,
                "policy_warning_count": 0,
                "retention_enabled": True,
                "retention_last_status": "ok",
                "runtime_wiring_current": True,
                "push_notifier_available": True,
                "slack_configured": True,
                "slack_delivery_failures": 0,
                "next_action": "No action needed.",
            },
        ) as mock_status,
    ):
        resp = await client.get("/review/data?hours=2&limit=3")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["trust"]["applied"] is False
    assert data["trust"]["validated"] is False
    mock_inbox.assert_called_once_with(hours=2, limit=3)
    mock_actions.assert_called_once_with(hours=2, limit=3)
    mock_status.assert_awaited_once_with()


async def test_review_save_package_endpoint_stages_without_applying(client: AsyncClient) -> None:
    server_mod.reset_review_package_state()
    with patch(
        "notification_hub.server.run_personal_ops_action_export",
        return_value={
            "status": "ok",
            "schema_version": "notification-hub.personal_ops_action_export.v1",
            "generated_at": "2026-05-09T00:00:00+00:00",
            "hours": 2,
            "actions": [{"action_id": "a"}],
            "review_package": {
                "requested": True,
                "status": "ok",
                "path": "/tmp/actions.json",
                "error": None,
            },
            "inbox": {},
            "error": None,
        },
    ) as mock_export:
        resp = await client.post("/review/save-package?hours=2&limit=3")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["applied"] is False
    assert data["review_package"]["path"] == "/tmp/actions.json"
    assert server_mod.get_latest_review_package_path() == "/tmp/actions.json"
    mock_export.assert_called_once_with(hours=2, limit=3, save_review_package=True)


async def test_review_validate_package_endpoint_validates_latest_package(client: AsyncClient) -> None:
    server_mod.reset_review_package_state()
    with patch(
        "notification_hub.server.get_latest_review_package_path",
        return_value="/tmp/actions.json",
    ), patch(
        "notification_hub.server.validate_action_package",
        return_value={
            "status": "ok",
            "path": "/tmp/actions.json",
            "schema_version": "notification-hub.personal_ops_action_export.v1",
            "action_count": 1,
            "valid_action_count": 1,
            "warning_count": 0,
            "error_count": 0,
            "warnings": [],
            "errors": [],
        },
    ) as mock_validate:
        resp = await client.post("/review/validate-package")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["applied"] is False
    assert data["validation"]["valid_action_count"] == 1
    assert data["review_package"]["validation_status"] == "ok"
    mock_validate.assert_called_once()


async def test_review_packages_endpoint_lists_saved_packages(client: AsyncClient) -> None:
    with patch(
        "notification_hub.server.list_action_review_packages",
        return_value=[
            {
                "path": "/tmp/actions.json",
                "name": "actions.json",
                "modified_at": "2026-05-09T00:00:00+00:00",
                "size_bytes": 100,
                "validation_status": "ok",
                "action_count": 1,
                "valid_action_count": 1,
                "error_count": 0,
            }
        ],
    ) as mock_packages:
        resp = await client.get("/review/packages?limit=3")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["applied"] is False
    assert data["packages"][0]["validation_status"] == "ok"
    mock_packages.assert_called_once_with(limit=3)


async def test_review_package_detail_endpoint_inspects_saved_package(client: AsyncClient) -> None:
    with patch(
        "notification_hub.server.load_action_review_package_detail",
        return_value={
            "status": "ok",
            "path": "/tmp/personal-ops-actions-20260509-100000.json",
            "name": "personal-ops-actions-20260509-100000.json",
            "schema_version": "notification-hub.personal_ops_action_export.v1",
            "generated_at": "2026-05-09T10:00:00+00:00",
            "hours": 2,
            "actions": [
                {
                    "title": "Approval Requested",
                    "priority": "high",
                    "state": "waiting",
                    "suggested_next_action": "Review the waiting item.",
                    "evidence_event_id": "abc123",
                    "evidence_timestamp": "2026-05-09T00:00:00+00:00",
                }
            ],
            "validation": {
                "status": "ok",
                "path": "/tmp/personal-ops-actions-20260509-100000.json",
                "schema_version": "notification-hub.personal_ops_action_export.v1",
                "action_count": 1,
                "valid_action_count": 1,
                "warning_count": 0,
                "error_count": 0,
                "warnings": [],
                "errors": [],
            },
            "applied": False,
            "error": None,
        },
    ) as mock_detail:
        resp = await client.get("/review/package/personal-ops-actions-20260509-100000.json")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["applied"] is False
    assert data["actions"][0]["evidence_event_id"] == "abc123"
    mock_detail.assert_called_once_with(name="personal-ops-actions-20260509-100000.json")


async def test_review_delete_package_endpoint_removes_saved_package(client: AsyncClient) -> None:
    server_mod.reset_review_package_state()
    with patch(
        "notification_hub.server.delete_action_review_package",
        return_value={
            "status": "ok",
            "path": "/tmp/actions.json",
            "name": "personal-ops-actions-20260509-100000.json",
            "deleted": True,
            "applied": False,
            "error": None,
        },
    ) as mock_delete:
        resp = await client.delete("/review/package/personal-ops-actions-20260509-100000.json")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["deleted"] is True
    assert data["applied"] is False
    mock_delete.assert_called_once_with(name="personal-ops-actions-20260509-100000.json")


async def test_review_queue_package_endpoint_enqueues_without_applying(client: AsyncClient) -> None:
    with patch(
        "notification_hub.server.load_action_review_package_detail",
        return_value={
            "status": "ok",
            "path": "/tmp/personal-ops-actions-20260509-100000.json",
            "name": "personal-ops-actions-20260509-100000.json",
            "schema_version": "notification-hub.personal_ops_action_export.v1",
            "generated_at": "2026-05-09T10:00:00+00:00",
            "hours": 2,
            "actions": [],
            "validation": {
                "status": "ok",
                "path": "/tmp/personal-ops-actions-20260509-100000.json",
                "schema_version": "notification-hub.personal_ops_action_export.v1",
                "action_count": 1,
                "valid_action_count": 1,
                "warning_count": 0,
                "error_count": 0,
                "warnings": [],
                "errors": [],
            },
            "applied": False,
            "error": None,
        },
    ), patch(
        "notification_hub.server.run_personal_ops_import_stub",
        return_value={
            "status": "ok",
            "path": "/tmp/personal-ops-actions-20260509-100000.json",
            "dry_run": True,
            "applied": False,
            "enqueued": True,
            "queued_count": 1,
            "skipped_count": 0,
            "queue_path": "/tmp/queue.jsonl",
            "validation": {
                "status": "ok",
                "path": "/tmp/personal-ops-actions-20260509-100000.json",
                "schema_version": "notification-hub.personal_ops_action_export.v1",
                "action_count": 1,
                "valid_action_count": 1,
                "warning_count": 0,
                "error_count": 0,
                "warnings": [],
                "errors": [],
            },
            "next_action": "Review the queued personal-ops handoff items.",
            "error": None,
        },
    ) as mock_import:
        resp = await client.post("/review/package/personal-ops-actions-20260509-100000.json/queue")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["applied"] is False
    assert data["enqueued"] is True
    assert data["queued_count"] == 1
    mock_import.assert_called_once()


async def test_review_import_queue_endpoint_lists_queue_items(client: AsyncClient) -> None:
    with patch(
        "notification_hub.server.list_personal_ops_import_queue",
        return_value=[
            {
                "queue_id": "queue123",
                "status": "queued",
                "enqueued_at": "2026-05-09T10:00:00+00:00",
                "source_package_name": "personal-ops-actions-20260509-100000.json",
                "action_id": "action123",
                "title": "Approval Requested",
                "priority": "high",
                "state": "waiting",
                "evidence_event_id": "abc123",
                "applied": False,
            }
        ],
    ) as mock_queue:
        resp = await client.get("/review/import-queue?limit=3")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["applied"] is False
    assert data["items"][0]["status"] == "queued"
    mock_queue.assert_called_once_with(limit=3)


async def test_review_validate_package_uses_newest_saved_package(client: AsyncClient) -> None:
    server_mod.reset_review_package_state()
    with patch(
        "notification_hub.server.list_action_review_packages",
        return_value=[
            {
                "path": "/tmp/newest-actions.json",
                "name": "newest-actions.json",
                "modified_at": "2026-05-09T00:00:00+00:00",
                "size_bytes": 100,
                "validation_status": "ok",
                "action_count": 1,
                "valid_action_count": 1,
                "error_count": 0,
            }
        ],
    ), patch(
        "notification_hub.server.validate_action_package",
        return_value={
            "status": "ok",
            "path": "/tmp/newest-actions.json",
            "schema_version": "notification-hub.personal_ops_action_export.v1",
            "action_count": 1,
            "valid_action_count": 1,
            "warning_count": 0,
            "error_count": 0,
            "warnings": [],
            "errors": [],
        },
    ):
        resp = await client.post("/review/validate-package")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["review_package"]["path"] == "/tmp/newest-actions.json"
    assert data["review_package"]["validation_status"] == "ok"
    assert server_mod.get_latest_review_package_path() == "/tmp/newest-actions.json"


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
