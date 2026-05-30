"""Tests for review operator endpoints."""

from __future__ import annotations

from unittest.mock import patch

from httpx import AsyncClient

import notification_hub.server as server_mod


async def test_review_operator_daily_state_endpoint_is_read_only(client: AsyncClient) -> None:
    with patch(
        "notification_hub.server.run_operator_daily_state",
        return_value={
            "status": "ok",
            "generated_at": "2026-05-10T04:50:00+00:00",
            "hours": 24,
            "runtime": {"status": "ok"},
            "queue_health": {"status": "ok", "health": {"queued_count": 0}},
            "coordination_console": {
                "status": "ok",
                "next_signal": {"title": "Waiting for next real signal"},
            },
            "burn_in": {"status": "ok"},
            "dismissals": [],
            "next_action": "Monitor /review for the next real handoff signal.",
            "report_file": {"requested": False},
            "applied": False,
        },
    ) as mock_daily_state:
        resp = await client.get("/review/operator-daily-state?hours=6&limit=3")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["applied"] is False
    mock_daily_state.assert_called_once_with(hours=6, limit=3, save_report=False)


async def test_review_operator_review_session_endpoint_is_read_only(client: AsyncClient) -> None:
    with patch(
        "notification_hub.server.run_operator_review_session",
        return_value={
            "status": "ok",
            "generated_at": "2026-05-10T04:52:00+00:00",
            "hours": 2,
            "since": "2026-05-10T02:52:00+00:00",
            "group_history_count": 1,
            "queue_item_count": 1,
            "saved_count": 1,
            "queued_count": 0,
            "dismissed_count": 0,
            "outcome_count": 0,
            "reviewed_count": 1,
            "active_queue_count": 0,
            "pending_promotion_count": 0,
            "route_counts": {"promote": 1},
            "group_summaries": [],
            "recent_group_history": [],
            "recent_queue_items": [],
            "next_action": "Recent review activity is summarized.",
            "report_file": {"requested": False, "status": "not_requested"},
            "applied": False,
        },
    ) as mock_review_session:
        resp = await client.get("/review/operator-review-session?hours=4&limit=7")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["applied"] is False
    assert data["route_counts"] == {"promote": 1}
    mock_review_session.assert_called_once_with(hours=4, limit=7, save_report=False)


async def test_review_operator_review_session_endpoint_can_save_report(
    client: AsyncClient,
) -> None:
    with patch(
        "notification_hub.server.run_operator_review_session",
        return_value={
            "status": "ok",
            "generated_at": "2026-05-10T04:52:00+00:00",
            "hours": 2,
            "since": "2026-05-10T02:52:00+00:00",
            "group_history_count": 0,
            "queue_item_count": 0,
            "saved_count": 0,
            "queued_count": 0,
            "dismissed_count": 0,
            "outcome_count": 0,
            "reviewed_count": 0,
            "active_queue_count": 0,
            "pending_promotion_count": 0,
            "route_counts": {},
            "group_summaries": [],
            "recent_group_history": [],
            "recent_queue_items": [],
            "next_action": "No recent review-session activity found in this window.",
            "report_file": {
                "requested": True,
                "status": "ok",
                "path": "/tmp/operator-review-session.json",
                "error": None,
            },
            "applied": False,
        },
    ) as mock_review_session:
        resp = await client.get("/review/operator-review-session?save_report=true")

    assert resp.status_code == 200
    data = resp.json()
    assert data["report_file"]["status"] == "ok"
    mock_review_session.assert_called_once_with(hours=2, limit=25, save_report=True)


async def test_review_operator_review_session_reports_endpoint_lists_saved_reports(
    client: AsyncClient,
) -> None:
    with patch(
        "notification_hub.server.list_operator_review_session_reports",
        return_value=[
            {
                "path": "/tmp/operator-review-session.json",
                "name": "operator-review-session-20260510-091508.json",
                "modified_at": "2026-05-10T09:15:08+00:00",
                "size_bytes": 200,
                "status": "ok",
                "generated_at": "2026-05-10T09:15:08+00:00",
                "hours": 2,
                "group_history_count": 3,
                "queue_item_count": 3,
                "saved_count": 1,
                "queued_count": 2,
                "dismissed_count": 0,
                "outcome_count": 0,
                "reviewed_count": 3,
                "active_queue_count": 0,
                "pending_promotion_count": 0,
                "next_action": "Recent review activity is summarized.",
            }
        ],
    ) as mock_reports:
        resp = await client.get("/review/operator-review-session-reports?limit=4")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["applied"] is False
    assert data["reports"][0]["group_history_count"] == 3
    mock_reports.assert_called_once_with(limit=4)


async def test_review_operator_review_session_retention_endpoint_is_read_only(
    client: AsyncClient,
) -> None:
    with patch(
        "notification_hub.server.prune_operator_review_session_reports",
        return_value={
            "status": "ok",
            "report_dir": "/tmp/operator-review-session-reports",
            "keep": 5,
            "dry_run": True,
            "total_count": 7,
            "kept_count": 5,
            "candidate_count": 2,
            "deleted_count": 0,
            "candidate_reports": [
                {
                    "path": "/tmp/operator-review-session-old.json",
                    "name": "operator-review-session-20260510-091508.json",
                    "modified_at": "2026-05-10T09:15:08+00:00",
                    "size_bytes": 200,
                    "status": "ok",
                    "generated_at": "2026-05-10T09:15:08+00:00",
                    "hours": 2,
                    "group_history_count": 0,
                    "queue_item_count": 0,
                    "saved_count": 0,
                    "queued_count": 0,
                    "dismissed_count": 0,
                    "outcome_count": 0,
                    "reviewed_count": 0,
                    "active_queue_count": 0,
                    "pending_promotion_count": 0,
                    "next_action": "No recent review-session activity found in this window.",
                }
            ],
            "deleted_reports": [],
            "next_action": "Run again with --apply to delete older review-session reports.",
            "applied": False,
            "error": None,
        },
    ) as mock_retention:
        resp = await client.get("/review/operator-review-session-retention?keep=5")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["dry_run"] is True
    assert data["applied"] is False
    assert data["candidate_count"] == 2
    assert data["deleted_count"] == 0
    mock_retention.assert_called_once_with(keep=5, dry_run=True)


async def test_review_operator_review_session_report_detail_endpoint_inspects_saved_report(
    client: AsyncClient,
) -> None:
    with patch(
        "notification_hub.server.load_operator_review_session_report_detail",
        return_value={
            "status": "ok",
            "path": "/tmp/operator-review-session.json",
            "name": "operator-review-session-20260510-091508.json",
            "schema_version": "notification-hub.operator_review_session.v1",
            "generated_at": "2026-05-10T09:15:08+00:00",
            "summary": {
                "path": "/tmp/operator-review-session.json",
                "name": "operator-review-session-20260510-091508.json",
                "modified_at": "2026-05-10T09:15:08+00:00",
                "size_bytes": 200,
                "status": "ok",
                "generated_at": "2026-05-10T09:15:08+00:00",
                "hours": 2,
                "group_history_count": 3,
                "queue_item_count": 3,
                "saved_count": 1,
                "queued_count": 2,
                "dismissed_count": 0,
                "outcome_count": 0,
                "reviewed_count": 3,
                "active_queue_count": 0,
                "pending_promotion_count": 0,
                "next_action": "Recent review activity is summarized.",
            },
            "report": {"status": "ok", "applied": False},
            "applied": False,
            "error": None,
        },
    ) as mock_detail:
        resp = await client.get(
            "/review/operator-review-session-report/operator-review-session-20260510-091508.json"
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["applied"] is False
    assert data["summary"]["queued_count"] == 2
    mock_detail.assert_called_once_with(name="operator-review-session-20260510-091508.json")


async def test_review_operator_handoff_drill_endpoint_is_temporary(client: AsyncClient) -> None:
    with patch(
        "notification_hub.server.run_operator_handoff_drill",
        return_value={
            "status": "ok",
            "generated_at": "2026-05-10T04:55:00+00:00",
            "scenario": {
                "status": "ok",
                "queue_id": "queue123",
                "rich_evidence_ready": True,
                "evidence_quality": "rich",
            },
            "queue_burn_in": {
                "status": "ok",
                "ready_for_live_promotion": True,
                "report_file": {
                    "requested": True,
                    "status": "ok",
                    "path": "/tmp/burn-in.json",
                    "error": None,
                },
            },
            "review_steps": ["Open /review and inspect an action proposal."],
            "next_action": "Use the same operator-mediated lifecycle for the next real handoff.",
            "applied": False,
        },
    ) as mock_drill:
        resp = await client.post("/review/operator-handoff-drill?save_burn_in_report=true")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["applied"] is False
    mock_drill.assert_called_once_with(save_burn_in_report=True)


async def test_review_import_queue_patch_updates_lifecycle(client: AsyncClient) -> None:
    with patch(
        "notification_hub.server.update_personal_ops_import_queue_item",
        return_value={
            "status": "ok",
            "queue_id": "queue123",
            "queue_path": "/tmp/queue.jsonl",
            "updated": True,
            "item": {
                "queue_id": "queue123",
                "status": "rejected",
                "enqueued_at": "2026-05-09T10:00:00+00:00",
                "updated_at": "2026-05-09T10:05:00+00:00",
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
                "outcome_reason": "duplicate",
                "promoted_at": None,
                "promotion_target": None,
                "promotion_target_id": None,
                "promotion_outcome": None,
                "promotion_outcome_at": None,
                "promotion_outcome_note": None,
            },
            "next_action": "No personal-ops action will be created for this handoff.",
            "error": None,
        },
    ) as mock_update:
        resp = await client.patch(
            "/review/import-queue/queue123",
            json={"status": "rejected", "reason": "duplicate"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["updated"] is True
    assert data["item"]["status"] == "rejected"
    mock_update.assert_called_once_with(
        queue_id="queue123",
        status="rejected",
        reason="duplicate",
        snoozed_until=None,
        promotion_target=None,
        promotion_target_id=None,
        promotion_outcome=None,
        promotion_outcome_note=None,
    )


async def test_review_validate_package_uses_newest_saved_package(client: AsyncClient) -> None:
    server_mod.reset_review_package_state()
    with (
        patch(
            "notification_hub.server.list_action_review_packages",
            return_value=[
                {
                    "path": "/tmp/newest-actions.json",
                    "name": "personal-ops-actions-20260509-100000.json",
                    "modified_at": "2026-05-09T00:00:00+00:00",
                    "size_bytes": 100,
                    "validation_status": "ok",
                    "action_count": 1,
                    "valid_action_count": 1,
                    "error_count": 0,
                }
            ],
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
        ),
    ):
        resp = await client.post("/review/validate-package")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["review_package"]["path"] == "/tmp/newest-actions.json"
    assert data["review_package"]["validation_status"] == "ok"
    assert server_mod.get_latest_review_package_path() == "/tmp/newest-actions.json"
