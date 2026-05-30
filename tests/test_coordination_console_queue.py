"""Tests for coordination console queued handoff guidance."""

from __future__ import annotations

from typing import cast
from unittest.mock import patch

from notification_hub.operations import run_coordination_console
from tests.coordination_console_fixtures import (
    coordination_burn_in_report,
    coordination_status,
)


def test_coordination_console_guides_queued_handoff_lifecycle() -> None:
    queued_item = {
        "queue_id": "queue-active",
        "status": "queued",
        "enqueued_at": "2026-05-10T04:35:00+00:00",
        "updated_at": "2026-05-10T04:37:00+00:00",
        "source_package_name": "package.json",
        "source_package_path": "/tmp/package.json",
        "action_id": "action-active",
        "title": "Approval Requested",
        "summary": "Repeated mail approval.",
        "priority": "high",
        "state": "waiting",
        "evidence_event_id": "event-active",
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
    queue_health = cast(dict[str, object], coordination_status(queued_count=1)["import_queue"])
    assert isinstance(queue_health, dict)
    queue_health = {
        **queue_health,
        "status": "warn",
        "next_action": "Review queued personal-ops handoff items.",
    }
    with (
        patch(
            "notification_hub.operations.run_coordination_readiness",
            return_value={
                "status": "warn",
                "decision": "fix_noise_first",
                "summary": "Runtime or policy needs attention before expanding coordination.",
                "queue_status": "warn",
                "queued_count": 1,
                "pending_count": 0,
                "stale_count": 0,
                "saved_burn_in_reports": 2,
                "latest_burn_in_ready": True,
                "latest_burn_in_noise_candidates": 0,
                "runtime_status": "degraded",
                "policy_warning_count": 0,
                "next_action": "Clear readiness noise before expanding coordination.",
                "evidence": ["runtime=degraded", "queue=warn queued=1 pending=0 stale=0"],
                "applied": False,
            },
        ),
        patch(
            "notification_hub.operations.run_personal_ops_action_export",
            return_value={
                "status": "ok",
                "schema_version": "notification-hub.personal_ops_action_export.v1",
                "generated_at": "2026-05-10T04:40:00+00:00",
                "hours": 2,
                "actions": [
                    {
                        "action_id": "action-active",
                        "source": "personal-ops",
                        "project": "mail",
                        "intent": "waiting_on_user",
                        "priority": "high",
                        "state": "waiting",
                        "title": "Approval Requested",
                        "summary": "Repeated mail approval.",
                        "suggested_next_action": "Review the waiting item.",
                        "evidence_event_id": "event-active",
                        "evidence_timestamp": "2026-05-10T04:40:00+00:00",
                        "count": 3,
                    }
                ],
                "review_package": {"status": "not_requested"},
                "inbox": {},
                "error": None,
            },
        ),
        patch(
            "notification_hub.operations.run_personal_ops_import_queue_health_check",
            return_value={
                "status": "warn",
                "health": queue_health,
                "queued_items": [queued_item],
                "pending_promotion_items": [],
                "next_commands": ["uv run notification-hub personal-ops-queue"],
                "applied": False,
            },
        ),
        patch(
            "notification_hub.operations.run_personal_ops_outcome_sync_reminder",
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
        ),
        patch(
            "notification_hub.operations.list_personal_ops_queue_burn_in_reports",
            return_value=[coordination_burn_in_report()],
        ),
        patch("notification_hub.operations._read_import_queue_items", return_value=[queued_item]),
    ):
        report = run_coordination_console(hours=2, limit=3)

    assert report["guide_stage"] == "queue_review"
    assert report["readiness"]["decision"] == "fix_noise_first"
    assert report["actions"][0]["lineage_status"] == "queued"
    assert report["guide_steps"][0]["queue_id"] == "queue-active"
    assert "queue-active" in report["next_commands"][1]
    assert report["next_action"] == "Review queued personal-ops handoff items."
