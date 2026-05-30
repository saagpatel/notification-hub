"""Tests for the FastAPI review endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from httpx import AsyncClient


async def test_review_page_inherits_action_proposal_review_window(
    client: AsyncClient,
) -> None:
    resp = await client.get("/review")

    assert resp.status_code == 200
    assert "const actionProposalReviewWindowHours = 24;" in resp.text
    assert "__ACTION_PROPOSAL_REVIEW_WINDOW_HOURS__" not in resp.text
    assert "hours=${actionProposalReviewWindowHours}&limit=25" in resp.text
    assert "hours: actionProposalReviewWindowHours" in resp.text


async def test_review_page_endpoint(client: AsyncClient) -> None:
    resp = await client.get("/review")

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "notification-hub review" in resp.text
    assert "Coordination Readiness" in resp.text
    assert "Coordination Console" in resp.text
    assert "Real Signal Readiness" in resp.text
    assert "First Rich Proof Gate" in resp.text
    assert "No resolved rich-evidence handoff outcomes yet" in resp.text
    assert "Latest proof" in resp.text
    assert "rich resolved" in resp.text
    assert "First rich handoff checklist" in resp.text
    assert "Proof trend" in resp.text
    assert "readiness improved" in resp.text
    assert "Policy Drift" in resp.text
    assert "Operator Decision Required" in resp.text
    assert "Noise Candidate Review" in resp.text
    assert "Burn-In Reports" in resp.text
    assert "Latest Review Session" in resp.text
    assert "Review Sessions" in resp.text
    assert "Review Session Retention" in resp.text
    assert "Run drill + save proof" in resp.text
    assert "save_burn_in_report=true" in resp.text
    assert "Rich evidence ready" in resp.text
    assert "Saved proof" in resp.text
    assert "Live runtime" in resp.text
    assert "proof age" in resp.text
    assert 'metric("Uptime", durationLabel(data.runtime.uptime_seconds))' in resp.text
    assert "function durationLabel" in resp.text
    assert "function olderThanDays" in resp.text
    assert "function readinessExplanation" in resp.text
    assert "Readiness explanation" in resp.text
    assert "Blocked by" in resp.text
    assert "runtime, policy, queue, and saved burn-in proof are clear" in resp.text
    assert "function renderFirstRichProofGate" in resp.text
    assert "first_rich_handoff_gate" in resp.text
    assert "Rich candidates" in resp.text
    assert "function metric" in resp.text


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
        patch(
            "notification_hub.server.run_personal_ops_import_queue_health_check",
            return_value={
                "status": "ok",
                "health": {
                    "status": "ok",
                    "queue_path": "/tmp/queue.jsonl",
                    "total_count": 0,
                    "queued_count": 0,
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
                    "needs_review": False,
                    "oldest_queued_at": None,
                    "oldest_queued_age_seconds": None,
                    "oldest_promoted_pending_at": None,
                    "oldest_promoted_pending_age_seconds": None,
                    "stale_after_hours": 4.0,
                    "next_action": "No queued personal-ops handoff items.",
                },
                "queued_items": [],
                "pending_promotion_items": [],
                "next_commands": ["uv run notification-hub personal-ops-queue-health"],
                "applied": False,
            },
        ) as mock_queue_health,
        patch(
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
        ) as mock_reminder,
        patch(
            "notification_hub.server.run_coordination_readiness",
            return_value={
                "status": "ok",
                "decision": "ready_to_expand",
                "summary": "Runtime, queue, and saved burn-in evidence are ready.",
                "queue_status": "ok",
                "queued_count": 0,
                "pending_count": 0,
                "stale_count": 0,
                "saved_burn_in_reports": 1,
                "latest_burn_in_ready": True,
                "latest_burn_in_noise_candidates": 0,
                "runtime_status": "ok",
                "policy_warning_count": 0,
                "next_action": "Plan the next compact coordination console slice.",
                "evidence": ["runtime=ok"],
                "applied": False,
            },
        ) as mock_readiness,
    ):
        resp = await client.get("/review/data?hours=2&limit=3")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["operator_focus"]["title"] == "No action needed"
    assert data["operator_focus"]["queued_count"] == 0
    assert data["coordination_readiness"]["decision"] == "ready_to_expand"
    assert data["trust"]["applied"] is False
    assert data["trust"]["validated"] is False
    mock_inbox.assert_called_once_with(hours=2, limit=3)
    mock_actions.assert_called_once_with(hours=2, limit=3)
    mock_status.assert_awaited_once_with()
    mock_queue_health.assert_called_once_with(limit=3)
    mock_reminder.assert_called_once_with(limit=3)
    mock_readiness.assert_called_once_with(limit=3)


async def test_review_noise_candidates_endpoint_is_read_only(client: AsyncClient) -> None:
    with patch(
        "notification_hub.server.review_latest_noise_candidates",
        return_value={
            "status": "warn",
            "report_name": "personal-ops-queue-burn-in-20260510-040904.json",
            "generated_at": "2026-05-10T04:09:04+00:00",
            "noise_candidate_count": 1,
            "candidates": [
                {
                    "count": 4,
                    "source": "personal-ops",
                    "project": "mail",
                    "level": "urgent",
                    "title": "Approval Requested",
                    "body": "Initial reply needed",
                    "decision_hint": "operator_decision_required",
                    "suggested_rule": "Review noise rule candidate: source='personal-ops'",
                }
            ],
            "next_action": "Review operator-decision candidates before adding policy coverage.",
            "applied": False,
            "error": None,
        },
    ) as mock_review:
        resp = await client.get("/review/noise-candidates?limit=3")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "warn"
    assert data["applied"] is False
    assert data["candidates"][0]["decision_hint"] == "operator_decision_required"
    mock_review.assert_called_once_with(limit=3)


async def test_review_coordination_readiness_endpoint_is_read_only(
    client: AsyncClient,
) -> None:
    with patch(
        "notification_hub.server.run_coordination_readiness",
        return_value={
            "status": "ok",
            "decision": "ready_to_expand",
            "summary": "Runtime, queue, and saved burn-in evidence are ready.",
            "queue_status": "ok",
            "queued_count": 0,
            "pending_count": 0,
            "stale_count": 0,
            "saved_burn_in_reports": 1,
            "latest_burn_in_ready": True,
            "latest_burn_in_noise_candidates": 0,
            "runtime_status": "ok",
            "policy_warning_count": 0,
            "next_action": "Plan the next compact coordination console slice.",
            "evidence": ["runtime=ok"],
            "applied": False,
        },
    ) as mock_readiness:
        resp = await client.get("/review/coordination-readiness?limit=3")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["decision"] == "ready_to_expand"
    assert data["applied"] is False
    mock_readiness.assert_called_once_with(limit=3)


async def test_review_coordination_console_endpoint_is_read_only(
    client: AsyncClient,
) -> None:
    with patch(
        "notification_hub.server.run_coordination_console",
        return_value={
            "status": "ok",
            "readiness": {
                "status": "ok",
                "decision": "ready_to_expand",
                "summary": "Runtime, queue, and saved burn-in evidence are ready.",
                "queue_status": "ok",
                "queued_count": 0,
                "pending_count": 0,
                "stale_count": 0,
                "saved_burn_in_reports": 2,
                "latest_burn_in_ready": True,
                "latest_burn_in_noise_candidates": 0,
                "runtime_status": "ok",
                "policy_warning_count": 0,
                "next_action": "Plan the next compact coordination console slice.",
                "evidence": ["runtime=ok"],
                "applied": False,
            },
            "action_count": 1,
            "active_action_count": 1,
            "handled_action_count": 0,
            "actions": [],
            "handled_actions": [],
            "queue_health": {
                "status": "ok",
                "queue_path": "/tmp/queue.jsonl",
                "total_count": 0,
                "queued_count": 0,
                "reviewed_count": 0,
                "rejected_count": 0,
                "snoozed_count": 0,
                "superseded_count": 0,
                "promoted_count": 3,
                "promoted_pending_count": 0,
                "promoted_pending_stale_count": 0,
                "promoted_accepted_count": 0,
                "promoted_rejected_count": 3,
                "promoted_ignored_count": 0,
                "needs_outcome_sync": False,
                "needs_review": False,
                "oldest_queued_at": None,
                "oldest_queued_age_seconds": None,
                "oldest_promoted_pending_at": None,
                "oldest_promoted_pending_age_seconds": None,
                "stale_after_hours": 4.0,
                "next_action": "No queued personal-ops handoff items.",
            },
            "queued_items": [],
            "pending_promotion_items": [],
            "outcome_sync_reminder": {
                "status": "ok",
                "should_remind": False,
                "pending_count": 0,
                "stale_count": 0,
                "reminders": [],
                "next_commands": ["uv run notification-hub personal-ops-queue-health"],
                "next_action": "No pending promoted personal-ops handoff outcomes.",
                "applied": False,
            },
            "burn_in_reports": [],
            "guide_stage": "package_review",
            "guide_steps": [
                {
                    "step": 1,
                    "title": "Save review package",
                    "status": "current",
                    "summary": "Stage the current proposals locally for inspection.",
                    "commands": [
                        "uv run notification-hub personal-ops-actions --save-review-package"
                    ],
                    "action_id": "action-1",
                    "queue_id": None,
                }
            ],
            "next_commands": ["uv run notification-hub personal-ops-actions --save-review-package"],
            "next_action": "Save and validate a review package.",
            "applied": False,
        },
    ) as mock_console:
        resp = await client.get("/review/coordination-console?hours=4&limit=3")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["readiness"]["decision"] == "ready_to_expand"
    assert data["guide_stage"] == "package_review"
    assert data["applied"] is False
    mock_console.assert_called_once_with(hours=4, limit=3)


async def test_review_policy_check_endpoint_is_read_only(client: AsyncClient) -> None:
    with patch(
        "notification_hub.server.run_policy_check",
        return_value={
            "status": "warn",
            "config_path": "/tmp/config.toml",
            "config_found": True,
            "example_path": "/tmp/policy.example.toml",
            "load_error": None,
            "warning_count": 0,
            "suggestion_count": 0,
            "warnings": [],
            "suggestions": [],
            "policy_drift": {
                "status": "warn",
                "live_noise_rule_count": 2,
                "sample_noise_rule_count": 3,
                "missing_sample_noise_rule_count": 1,
                "extra_live_noise_rule_count": 0,
                "missing_sample_noise_rules": [{"source": "personal-ops"}],
                "extra_live_noise_rules": [],
                "next_action": "Add the missing sample noise rules.",
                "error": None,
            },
        },
    ) as mock_policy:
        resp = await client.get("/review/policy-check")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "warn"
    assert data["policy_drift"]["missing_sample_noise_rule_count"] == 1
    mock_policy.assert_called_once_with()
