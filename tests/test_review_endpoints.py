"""Tests for the FastAPI review endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

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


async def test_review_page_inherits_action_proposal_review_window(
    client: AsyncClient,
) -> None:
    resp = await client.get("/review")

    assert resp.status_code == 200
    assert "const actionProposalReviewWindowHours = 24;" in resp.text
    assert "__ACTION_PROPOSAL_REVIEW_WINDOW_HOURS__" not in resp.text
    assert (
        "hours=${actionProposalReviewWindowHours}&limit=25"
        in resp.text
    )
    assert "hours: actionProposalReviewWindowHours" in resp.text


async def test_review_page_endpoint(client: AsyncClient) -> None:
    resp = await client.get("/review")

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "notification-hub review" in resp.text
    assert "Coordination Readiness" in resp.text
    assert "Coordination Console" in resp.text
    assert "Real Signal Readiness" in resp.text
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


async def test_review_action_proposal_group_package_endpoint_is_read_only(
    client: AsyncClient,
) -> None:
    with patch(
        "notification_hub.server.save_action_proposal_group_package",
        return_value={
            "status": "ok",
            "group_key": "personal-ops:mail:waiting_on_user:high:waiting",
            "action_count": 2,
            "review_package": {"status": "ok", "path": "/tmp/actions.json"},
            "import_result": None,
            "next_action": "Inspect the saved group package before queueing it.",
            "applied": False,
            "error": None,
        },
    ) as mock_save_group:
        resp = await client.post(
            "/review/action-proposal-group/package",
            json={"group_key": "personal-ops:mail:waiting_on_user:high:waiting", "hours": 4},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["action_count"] == 2
    assert data["applied"] is False
    mock_save_group.assert_called_once_with(
        group_key="personal-ops:mail:waiting_on_user:high:waiting",
        route=None,
        hours=4,
        limit=25,
        enqueue=False,
    )


async def test_review_action_proposal_group_queue_endpoint_stays_non_applying(
    client: AsyncClient,
) -> None:
    with patch(
        "notification_hub.server.save_action_proposal_group_package",
        return_value={
            "status": "ok",
            "group_key": "personal-ops:mail:waiting_on_user:high:waiting",
            "action_count": 2,
            "review_package": {"status": "ok", "path": "/tmp/actions.json"},
            "import_result": {"status": "ok", "queued_count": 2},
            "next_action": "Review the queued personal-ops handoff items.",
            "applied": False,
            "error": None,
        },
    ) as mock_queue_group:
        resp = await client.post(
            "/review/action-proposal-group/queue",
            json={"group_key": "personal-ops:mail:waiting_on_user:high:waiting"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["import_result"]["queued_count"] == 2
    assert data["applied"] is False
    mock_queue_group.assert_called_once_with(
        group_key="personal-ops:mail:waiting_on_user:high:waiting",
        route=None,
        hours=24,
        limit=25,
        enqueue=True,
    )


async def test_review_action_proposal_group_dismiss_endpoint_is_local_only(
    client: AsyncClient,
) -> None:
    with patch(
        "notification_hub.server.dismiss_action_proposal_group",
        return_value={
            "status": "ok",
            "group_key": "personal-ops:mail:waiting_on_user:high:waiting",
            "dismissed_count": 2,
            "dismissals": [],
            "next_action": "Group dismissed from the local console.",
            "applied": False,
            "error": None,
        },
    ) as mock_dismiss_group:
        resp = await client.post(
            "/review/action-proposal-group/dismiss",
            json={
                "group_key": "personal-ops:mail:waiting_on_user:high:waiting",
                "reason": "known noise",
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["dismissed_count"] == 2
    assert data["applied"] is False
    mock_dismiss_group.assert_called_once_with(
        group_key="personal-ops:mail:waiting_on_user:high:waiting",
        reason="known noise",
        route=None,
        hours=24,
        limit=25,
    )


async def test_review_action_proposal_group_route_posts_are_local_only(
    client: AsyncClient,
) -> None:
    with patch(
        "notification_hub.server.save_action_proposal_group_package",
        return_value={
            "status": "ok",
            "group_key": "personal-ops:mail:waiting_on_user:high:waiting",
            "action_count": 1,
            "review_package": {"status": "ok", "path": "/tmp/actions.json"},
            "import_result": {"status": "ok", "queued_count": 1},
            "next_action": "Review the queued personal-ops handoff items.",
            "applied": False,
            "error": None,
        },
    ) as mock_queue_group:
        resp = await client.post(
            "/review/action-proposal-group/queue",
            json={
                "group_key": "personal-ops:mail:waiting_on_user:high:waiting",
                "route": "promote",
            },
        )

    assert resp.status_code == 200
    assert resp.json()["applied"] is False
    mock_queue_group.assert_called_once_with(
        group_key="personal-ops:mail:waiting_on_user:high:waiting",
        route="promote",
        hours=24,
        limit=25,
        enqueue=True,
    )

    with patch(
        "notification_hub.server.dismiss_action_proposal_group",
        return_value={
            "status": "ok",
            "group_key": "personal-ops:mail:waiting_on_user:high:waiting",
            "dismissed_count": 1,
            "dismissals": [],
            "next_action": "Group dismissed from the local console.",
            "applied": False,
            "error": None,
        },
    ) as mock_dismiss_group:
        resp = await client.post(
            "/review/action-proposal-group/dismiss",
            json={
                "group_key": "personal-ops:mail:waiting_on_user:high:waiting",
                "route": "suppress",
                "reason": "already covered",
            },
        )

    assert resp.status_code == 200
    assert resp.json()["applied"] is False
    mock_dismiss_group.assert_called_once_with(
        group_key="personal-ops:mail:waiting_on_user:high:waiting",
        reason="already covered",
        route="suppress",
        hours=24,
        limit=25,
    )


async def test_review_action_proposal_group_outcome_endpoint_is_local_only(
    client: AsyncClient,
) -> None:
    with patch(
        "notification_hub.server.record_action_proposal_group_outcome",
        return_value={
            "status": "ok",
            "group_key": "personal-ops:mail:waiting_on_user:high:waiting",
            "outcome": "needs_follow_up",
            "group_history": {"event_type": "outcome", "outcome": "needs_follow_up"},
            "next_action": "Group outcome recorded locally.",
            "applied": False,
            "error": None,
        },
    ) as mock_outcome_group:
        resp = await client.post(
            "/review/action-proposal-group/outcome",
            json={
                "group_key": "personal-ops:mail:waiting_on_user:high:waiting",
                "outcome": "needs_follow_up",
                "reason": "follow up",
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["outcome"] == "needs_follow_up"
    assert data["applied"] is False
    mock_outcome_group.assert_called_once_with(
        group_key="personal-ops:mail:waiting_on_user:high:waiting",
        outcome="needs_follow_up",
        reason="follow up",
        hours=24,
        limit=25,
    )


async def test_review_outcome_sync_reminder_endpoint_reports_pending(
    client: AsyncClient,
) -> None:
    with patch(
        "notification_hub.server.run_personal_ops_outcome_sync_reminder",
        return_value={
            "status": "warn",
            "should_remind": True,
            "pending_count": 1,
            "stale_count": 1,
            "reminders": [],
            "next_commands": [
                'personal-ops suggestion accept|reject SUGGESTION_ID --note "..."',
                "uv run notification-hub personal-ops-queue --queue-id QUEUE_ID "
                "--status promoted --promotion-target-id SUGGESTION_ID "
                '--promotion-outcome accepted|rejected --promotion-outcome-note "..."',
            ],
            "next_action": "Resolve stale promoted personal-ops handoff outcomes before promoting more work.",
            "applied": False,
        },
    ) as mock_reminder:
        resp = await client.get("/review/outcome-sync-reminder?limit=3&stale_after_hours=2")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "warn"
    assert data["should_remind"] is True
    assert data["applied"] is False
    mock_reminder.assert_called_once_with(limit=3, stale_after_hours=2.0)


async def test_review_action_proposal_dismiss_endpoint_persists_dismissal(
    client: AsyncClient,
) -> None:
    with (
        patch(
            "notification_hub.server.run_personal_ops_action_export",
            return_value={
                "status": "ok",
                "schema_version": "notification-hub.personal_ops_action_export.v1",
                "generated_at": "2026-05-10T04:40:00+00:00",
                "hours": 24,
                "actions": [
                    {
                        "action_id": "action-1",
                        "dismissal_key": "proposal:personal-ops:mail:waiting-on-user:abc",
                        "source": "personal-ops",
                        "project": "mail",
                        "intent": "waiting_on_user",
                        "priority": "high",
                        "state": "waiting",
                        "title": "Approval Requested",
                        "summary": "2 repeated personal-ops events: Test draft",
                        "signal_level": "urgent",
                        "signal_body": "Test draft",
                        "suggested_next_action": "Review the waiting item.",
                        "evidence_event_id": "event-1",
                        "evidence_timestamp": "2026-05-10T04:40:00+00:00",
                        "count": 2,
                    }
                ],
                "dismissed_action_count": 0,
                "dismissals": [],
                "review_package": {"status": "not_requested"},
                "inbox": {},
                "error": None,
            },
        ) as mock_actions,
        patch(
            "notification_hub.server.dismiss_action_proposal",
            return_value={
                "status": "ok",
                "path": "/tmp/dismissals.jsonl",
                "dismissal": {
                    "dismissal_key": "proposal:personal-ops:mail:waiting-on-user:abc",
                    "dismissed_at": "2026-05-10T04:41:00+00:00",
                    "deleted_at": None,
                    "active": True,
                    "reason": "known test signal",
                    "source": "personal-ops",
                    "project": "mail",
                    "intent": "waiting_on_user",
                    "title": "Approval Requested",
                    "body": "Test draft",
                    "evidence_event_id": "event-1",
                },
                "applied": False,
                "error": None,
            },
        ) as mock_dismiss,
    ):
        resp = await client.post(
            "/review/action-proposal/proposal%3Apersonal-ops%3Amail%3Awaiting-on-user%3Aabc/dismiss",
            json={"reason": "known test signal"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["applied"] is False
    mock_actions.assert_called_once_with(hours=24, limit=100, include_dismissed=True)
    mock_dismiss.assert_called_once_with(
        dismissal_key="proposal:personal-ops:mail:waiting-on-user:abc",
        reason="known test signal",
        source="personal-ops",
        project="mail",
        intent="waiting_on_user",
        title="Approval Requested",
        body="Test draft",
        evidence_event_id="event-1",
    )


async def test_review_action_proposal_dismissals_endpoint_lists_dismissals(
    client: AsyncClient,
) -> None:
    with patch(
        "notification_hub.server.run_action_proposal_dismissal_list",
        return_value={
            "status": "ok",
            "path": "/tmp/dismissals.jsonl",
            "dismissal_count": 1,
            "dismissals": [
                {
                    "dismissal_key": "proposal:abc",
                    "dismissed_at": "2026-05-10T04:41:00+00:00",
                    "deleted_at": None,
                    "active": True,
                    "reason": "known noise",
                    "source": "personal-ops",
                    "project": "mail",
                    "intent": "waiting_on_user",
                    "title": "Approval Requested",
                    "body": "Test draft",
                    "evidence_event_id": "event-1",
                }
            ],
            "applied": False,
        },
    ) as mock_list:
        resp = await client.get(
            "/review/action-proposal-dismissals?limit=3&dismissal_key=proposal%3Aabc&include_inactive=true"
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["dismissals"][0]["dismissal_key"] == "proposal:abc"
    assert data["applied"] is False
    mock_list.assert_called_once_with(
        limit=3,
        dismissal_key="proposal:abc",
        include_inactive=True,
    )


async def test_review_action_proposal_undismiss_endpoint_adds_tombstone(
    client: AsyncClient,
) -> None:
    with patch(
        "notification_hub.server.undismiss_action_proposal",
        return_value={
            "status": "ok",
            "path": "/tmp/dismissals.jsonl",
            "dismissal_key": "proposal:abc",
            "removed": True,
            "applied": False,
            "error": None,
        },
    ) as mock_undismiss:
        resp = await client.post(
            "/review/action-proposal/proposal%3Aabc/undismiss",
            json={"reason": "useful again"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["removed"] is True
    assert data["applied"] is False
    mock_undismiss.assert_called_once_with(
        dismissal_key="proposal:abc",
        reason="useful again",
    )


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
