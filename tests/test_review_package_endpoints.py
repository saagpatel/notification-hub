"""Tests for FastAPI review package and queue endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

import notification_hub.server as server_mod
from notification_hub.pipeline import reset_suppression_engine
from notification_hub.server import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def fresh_suppression() -> None:
    reset_suppression_engine()


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


async def test_review_validate_package_endpoint_validates_latest_package(
    client: AsyncClient,
) -> None:
    server_mod.reset_review_package_state()
    with (
        patch(
            "notification_hub.server.get_latest_review_package_path",
            return_value="/tmp/personal-ops-actions-20260509-100000.json",
        ),
        patch(
            "notification_hub.server.validate_action_package",
            return_value={
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
        ) as mock_validate,
    ):
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
            "queue_items": [
                {
                    "queue_id": "queue123",
                    "status": "promoted",
                    "enqueued_at": "2026-05-09T10:05:00+00:00",
                    "updated_at": "2026-05-09T10:10:00+00:00",
                    "source_package_name": "personal-ops-actions-20260509-100000.json",
                    "source_package_path": "/tmp/personal-ops-actions-20260509-100000.json",
                    "action_id": "abc",
                    "title": "Approval Requested",
                    "summary": "summary",
                    "priority": "high",
                    "state": "waiting",
                    "evidence_event_id": "abc123",
                    "applied": True,
                    "snoozed_until": None,
                    "outcome_reason": None,
                    "promoted_at": "2026-05-09T10:10:00+00:00",
                    "promotion_target": "personal-ops task suggestion",
                    "promotion_target_id": "task123",
                    "promotion_outcome": "accepted",
                    "promotion_outcome_at": "2026-05-09T10:15:00+00:00",
                    "promotion_outcome_note": None,
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
    assert data["queue_items"][0]["queue_id"] == "queue123"
    mock_detail.assert_called_once_with(name="personal-ops-actions-20260509-100000.json")


async def test_review_package_detail_endpoint_sanitizes_unexpected_errors(
    client: AsyncClient,
) -> None:
    with patch(
        "notification_hub.server.load_action_review_package_detail",
        return_value={
            "status": "degraded",
            "path": "/tmp/personal-ops-actions-20260509-100000.json",
            "name": "personal-ops-actions-20260509-100000.json",
            "error": "failed to open /Users/d/private/actions.json",
            "validation": {
                "status": "degraded",
                "load_error": "traceback with local path",
                "errors": [
                    "raw validation path /Users/d/private/actions.json",
                    "package validation failed",
                ],
            },
            "applied": False,
        },
    ):
        resp = await client.get("/review/package/personal-ops-actions-20260509-100000.json")

    assert resp.status_code == 200
    data = resp.json()
    generic = "operation failed; inspect local logs for details"
    assert data["error"] == generic
    assert data["validation"]["load_error"] == generic
    assert data["validation"]["errors"] == [generic, "package validation failed"]


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
    with (
        patch(
            "notification_hub.server.load_action_review_package_detail",
            return_value={
                "status": "ok",
                "path": "/tmp/personal-ops-actions-20260509-100000.json",
                "name": "personal-ops-actions-20260509-100000.json",
                "schema_version": "notification-hub.personal_ops_action_export.v1",
                "generated_at": "2026-05-09T10:00:00+00:00",
                "hours": 2,
                "actions": [],
                "queue_items": [],
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
        ),
        patch(
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
        ) as mock_import,
    ):
        resp = await client.post("/review/package/personal-ops-actions-20260509-100000.json/queue")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["applied"] is False
    assert data["enqueued"] is True
    assert data["queued_count"] == 1
    mock_import.assert_called_once()


async def test_review_queue_package_sanitizes_import_errors(client: AsyncClient) -> None:
    with (
        patch(
            "notification_hub.server.load_action_review_package_detail",
            return_value={
                "path": "/tmp/review-package.json",
                "validation": {"status": "ok", "errors": []},
            },
        ),
        patch(
            "notification_hub.server.action_review_package_path_for_name",
            return_value=Path("/tmp/review-package.json"),
        ),
        patch(
            "notification_hub.server.run_personal_ops_import_stub",
            return_value={
                "status": "degraded",
                "error": "Traceback: PermissionError('/Users/d/private/state.jsonl')",
            },
        ),
    ):
        resp = await client.post("/review/package/review-package.json/queue")

    assert resp.status_code == 200
    data = resp.json()
    assert data["error"] == "operation failed; inspect local logs for details"
    assert "/Users/d/private" not in resp.text


async def test_review_import_queue_endpoint_lists_queue_items(client: AsyncClient) -> None:
    with patch(
        "notification_hub.server.list_personal_ops_import_queue",
        return_value=[
            {
                "queue_id": "queue123",
                "status": "queued",
                "enqueued_at": "2026-05-09T10:00:00+00:00",
                "updated_at": None,
                "source_package_name": "personal-ops-actions-20260509-100000.json",
                "source_package_path": "/tmp/personal-ops-actions-20260509-100000.json",
                "action_id": "action123",
                "title": "Approval Requested",
                "summary": "2 repeated personal-ops events",
                "priority": "high",
                "state": "waiting",
                "evidence_event_id": "abc123",
                "applied": False,
                "snoozed_until": None,
                "outcome_reason": None,
                "promoted_at": None,
                "promotion_target": None,
                "promotion_target_id": None,
                "promotion_outcome": None,
                "promotion_outcome_at": None,
                "promotion_outcome_note": None,
            }
        ],
    ) as mock_queue:
        with patch(
            "notification_hub.server.run_personal_ops_import_queue_health_check",
            return_value={
                "status": "warn",
                "health": {
                    "status": "warn",
                    "queue_path": "/tmp/queue.jsonl",
                    "total_count": 1,
                    "queued_count": 1,
                    "reviewed_count": 0,
                    "rejected_count": 0,
                    "snoozed_count": 0,
                    "superseded_count": 0,
                    "promoted_count": 0,
                    "promoted_pending_count": 0,
                    "promoted_pending_stale_count": 0,
                    "promoted_accepted_count": 0,
                    "promoted_rejected_count": 0,
                    "promoted_ignored_count": 0,
                    "needs_outcome_sync": False,
                    "needs_review": True,
                    "oldest_queued_at": "2026-05-09T10:00:00+00:00",
                    "oldest_queued_age_seconds": 1.0,
                    "oldest_promoted_pending_at": None,
                    "oldest_promoted_pending_age_seconds": None,
                    "stale_after_hours": 4.0,
                    "next_action": "Review queued personal-ops handoff items.",
                },
                "queued_items": [],
                "pending_promotion_items": [],
                "next_commands": ["uv run notification-hub personal-ops-queue"],
                "applied": False,
            },
        ) as mock_health:
            with patch(
                "notification_hub.server.run_personal_ops_outcome_sync_reminder",
                return_value={
                    "status": "ok",
                    "should_remind": False,
                    "pending_count": 0,
                    "stale_count": 0,
                    "reminders": [],
                    "next_commands": ["uv run notification-hub personal-ops-queue-health"],
                    "next_action": "No pending promoted personal-ops handoff outcomes.",
                    "applied": False,
                },
            ) as mock_reminder:
                resp = await client.get("/review/import-queue?limit=3")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["applied"] is False
    assert data["items"][0]["status"] == "queued"
    assert data["health"]["needs_review"] is True
    assert data["next_commands"] == ["uv run notification-hub personal-ops-queue"]
    assert data["outcome_sync_reminder"]["should_remind"] is False
    mock_queue.assert_called_once_with(limit=3)
    mock_health.assert_called_once_with(limit=3, stale_after_hours=4.0)
    mock_reminder.assert_called_once_with(limit=3, stale_after_hours=4.0)


async def test_review_import_queue_review_endpoint_is_read_only(client: AsyncClient) -> None:
    with patch(
        "notification_hub.server.run_personal_ops_queue_review",
        return_value={
            "status": "warn",
            "queue_status": "warn",
            "queued_count": 2,
            "pending_count": 0,
            "stale_count": 0,
            "operator_decision_count": 2,
            "batch_count": 1,
            "batches": [
                {
                    "batch_key": "package.json:Approval Requested:high:waiting",
                    "source_package_name": "package.json",
                    "title": "Approval Requested",
                    "priority": "high",
                    "state": "waiting",
                    "item_count": 2,
                    "queue_ids": ["queue-1", "queue-2"],
                    "evidence_event_ids": ["event-1", "event-2"],
                    "summaries": ["Approval draft", "Outbound workflow reply"],
                    "first_queue_id": "queue-1",
                    "suggested_next_action": "Review this batch evidence.",
                }
            ],
            "next_action": "Review queued handoff batches before expanding coordination.",
            "next_commands": ["uv run notification-hub personal-ops-queue"],
            "applied": False,
        },
    ) as mock_review:
        resp = await client.get("/review/import-queue-review?limit=3")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "warn"
    assert data["operator_decision_count"] == 2
    assert data["batches"][0]["first_queue_id"] == "queue-1"
    assert data["applied"] is False
    mock_review.assert_called_once_with(limit=3, stale_after_hours=4.0)


async def test_review_burn_in_reports_endpoint_lists_saved_reports(
    client: AsyncClient,
) -> None:
    with patch(
        "notification_hub.server.list_personal_ops_queue_burn_in_reports",
        return_value=[
            {
                "path": "/tmp/report.json",
                "name": "personal-ops-queue-burn-in-20260510-040904.json",
                "modified_at": "2026-05-10T04:09:04+00:00",
                "size_bytes": 200,
                "status": "ok",
                "generated_at": "2026-05-10T04:09:04+00:00",
                "ready_for_live_promotion": True,
                "queued_count": 0,
                "pending_count": 0,
                "stale_count": 0,
                "runtime_status": "ok",
                "noise_candidate_count": 0,
                "next_action": "Queue loop is ready.",
            }
        ],
    ) as mock_reports:
        resp = await client.get("/review/burn-in-reports?limit=3")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["applied"] is False
    assert data["reports"][0]["ready_for_live_promotion"] is True
    mock_reports.assert_called_once_with(limit=3)


async def test_review_burn_in_report_detail_endpoint_inspects_saved_report(
    client: AsyncClient,
) -> None:
    with patch(
        "notification_hub.server.load_personal_ops_queue_burn_in_report_detail",
        return_value={
            "status": "ok",
            "path": "/tmp/report.json",
            "name": "personal-ops-queue-burn-in-20260510-040904.json",
            "schema_version": "notification-hub.personal_ops_queue_burn_in.v1",
            "generated_at": "2026-05-10T04:09:04+00:00",
            "summary": {
                "path": "/tmp/report.json",
                "name": "personal-ops-queue-burn-in-20260510-040904.json",
                "modified_at": "2026-05-10T04:09:04+00:00",
                "size_bytes": 200,
                "status": "ok",
                "generated_at": "2026-05-10T04:09:04+00:00",
                "ready_for_live_promotion": True,
                "queued_count": 0,
                "pending_count": 0,
                "stale_count": 0,
                "runtime_status": "ok",
                "noise_candidate_count": 0,
                "next_action": "Queue loop is ready.",
            },
            "report": {"status": "ok"},
            "applied": False,
            "error": None,
        },
    ) as mock_detail:
        resp = await client.get(
            "/review/burn-in-report/personal-ops-queue-burn-in-20260510-040904.json"
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["applied"] is False
    assert data["summary"]["ready_for_live_promotion"] is True
    mock_detail.assert_called_once_with(name="personal-ops-queue-burn-in-20260510-040904.json")
