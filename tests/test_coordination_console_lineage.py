"""Tests for coordination console lineage and follow-up handling."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from notification_hub.operations import run_coordination_console
from tests.coordination_console_fixtures import (
    coordination_burn_in_report,
    coordination_status,
)


def test_coordination_console_marks_handled_actions_as_history() -> None:
    with (
        patch(
            "notification_hub.operations.run_coordination_readiness",
            return_value={
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
                        "action_id": "action-resolved",
                        "source": "personal-ops",
                        "project": "mail",
                        "intent": "waiting_on_user",
                        "priority": "high",
                        "state": "waiting",
                        "title": "Approval Requested",
                        "summary": "Repeated mail approval.",
                        "suggested_next_action": "Review the waiting item.",
                        "evidence_event_id": "event-resolved",
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
                "status": "ok",
                "health": coordination_status(promoted_count=3)["import_queue"],
                "queued_items": [],
                "pending_promotion_items": [],
                "next_commands": ["uv run notification-hub personal-ops-queue-health"],
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
        patch(
            "notification_hub.operations._read_import_queue_items",
            return_value=[
                {
                    "queue_id": "queue-resolved",
                    "status": "promoted",
                    "enqueued_at": "2026-05-10T04:35:00+00:00",
                    "updated_at": "2026-05-10T04:37:00+00:00",
                    "source_package_name": "package.json",
                    "source_package_path": "/tmp/package.json",
                    "action_id": "action-resolved",
                    "applied": True,
                    "promoted_at": "2026-05-10T04:36:00+00:00",
                    "promotion_target": "personal-ops task suggestion",
                    "promotion_target_id": "suggestion-1",
                    "promotion_outcome": "rejected",
                    "promotion_outcome_at": "2026-05-10T04:37:00+00:00",
                    "action": {
                        "title": "Approval Requested",
                        "summary": "Repeated mail approval.",
                        "priority": "high",
                        "state": "waiting",
                        "evidence_event_id": "event-resolved",
                    },
                }
            ],
        ),
    ):
        report = run_coordination_console(hours=2, limit=3)

    assert report["action_count"] == 1
    assert report["active_action_count"] == 0
    assert report["handled_action_count"] == 1
    assert report["actions"] == []
    assert report["handled_actions"][0]["lineage_status"] == "resolved"
    assert report["handled_actions"][0]["lineage_label"] == "Resolved"
    assert report["proposal_review"]["resolved_count"] == 1
    assert report["proposal_review"]["reviewed_only_count"] == 0
    assert report["handled_actions"][0]["queue_id"] == "queue-resolved"
    assert report["guide_stage"] == "monitor"
    assert report["guide_steps"][0]["summary"].startswith("1 handled mail follow-up")
    assert report["next_commands"] == [
        "uv run notification-hub coordination-console",
        "uv run notification-hub personal-ops-queue-health",
    ]
    assert report["next_action"] == "Monitor /review for the next real handoff signal."


def test_coordination_console_treats_reviewed_handoff_as_history() -> None:
    reviewed_item = {
        "queue_id": "queue-reviewed",
        "status": "reviewed",
        "enqueued_at": "2026-05-10T04:35:00+00:00",
        "updated_at": "2026-05-10T04:37:00+00:00",
        "source_package_name": "package.json",
        "source_package_path": "/tmp/package.json",
        "action_id": "action-reviewed",
        "title": "Approval Requested",
        "summary": "Repeated mail approval.",
        "priority": "high",
        "state": "waiting",
        "evidence_event_id": "event-reviewed",
        "applied": False,
        "snoozed_until": None,
        "outcome_reason": "operator checked evidence",
        "promoted_at": None,
        "promotion_target": None,
        "promotion_target_id": None,
        "promotion_outcome": None,
        "promotion_outcome_at": None,
        "promotion_outcome_note": None,
    }
    with (
        patch(
            "notification_hub.operations.run_coordination_readiness",
            return_value={
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
                        "action_id": "action-reviewed",
                        "source": "personal-ops",
                        "project": "mail",
                        "intent": "waiting_on_user",
                        "priority": "high",
                        "state": "waiting",
                        "title": "Approval Requested",
                        "summary": "Repeated mail approval.",
                        "suggested_next_action": "Review the waiting item.",
                        "evidence_event_id": "event-reviewed",
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
                "status": "ok",
                "health": coordination_status()["import_queue"],
                "queued_items": [],
                "pending_promotion_items": [],
                "next_commands": ["uv run notification-hub personal-ops-queue-health"],
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
        patch("notification_hub.operations._read_import_queue_items", return_value=[reviewed_item]),
    ):
        report = run_coordination_console(hours=2, limit=3)

    assert report["active_action_count"] == 0
    assert report["handled_action_count"] == 1
    assert report["handled_actions"][0]["lineage_status"] == "reviewed"
    assert report["handled_actions"][0]["lineage_label"] == "Reviewed only"
    assert report["handled_actions"][0]["lineage_next_action"] == (
        "Evidence was reviewed and no downstream promotion is required."
    )
    assert report["proposal_review"]["reviewed_only_count"] == 1
    assert report["proposal_review"]["resolved_count"] == 0
    assert report["proposal_review"]["mode"] == "monitor"
    assert "reviewed-only" in report["proposal_review"]["summary"]
    assert report["guide_stage"] == "monitor"
    assert report["next_action"] == "Monitor /review for the next real handoff signal."


def test_coordination_console_treats_superseded_group_outcome_as_history(
    tmp_path: Path,
) -> None:
    group_history_path = tmp_path / "group-history.jsonl"
    group_history_path.write_text(
        json.dumps(
            {
                "group_key": "personal-ops:personal-ops:needs_attention:high:open",
                "event_type": "outcome",
                "recorded_at": "2026-05-17T03:00:00+00:00",
                "status": "ok",
                "action_count": 1,
                "action_ids": ["calendar-sync-old"],
                "action_keys": ["proposal:personal-ops:personal-ops:needs-attention:stable"],
                "package_path": None,
                "queued_count": None,
                "dismissed_count": None,
                "outcome": "superseded",
                "reason": "source recovered",
                "error": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with (
        patch(
            "notification_hub.operations.run_coordination_readiness",
            return_value={
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
        ),
        patch(
            "notification_hub.operations.run_personal_ops_action_export",
            return_value={
                "status": "ok",
                "schema_version": "notification-hub.personal_ops_action_export.v1",
                "generated_at": "2026-05-17T03:05:00+00:00",
                "hours": 2,
                "actions": [
                    {
                        "action_id": "calendar-sync-new",
                        "dismissal_key": (
                            "proposal:personal-ops:personal-ops:needs-attention:stable"
                        ),
                        "source": "personal-ops",
                        "project": "personal-ops",
                        "intent": "needs_attention",
                        "priority": "high",
                        "state": "open",
                        "title": "Calendar sync degraded",
                        "summary": "Calendar sync recovered after review.",
                        "signal_body": "request to https://oauth2.googleapis.com/token failed",
                        "suggested_next_action": "Review the attention item.",
                        "evidence_event_id": "event-calendar-sync-new",
                        "evidence_timestamp": "2026-05-17T03:05:00+00:00",
                        "count": 4,
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
                "status": "ok",
                "health": coordination_status()["import_queue"],
                "queued_items": [],
                "pending_promotion_items": [],
                "next_commands": ["uv run notification-hub personal-ops-queue-health"],
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
        patch("notification_hub.operations._read_import_queue_items", return_value=[]),
    ):
        report = run_coordination_console(
            hours=2,
            limit=3,
            group_history_path=group_history_path,
        )

    assert report["active_action_count"] == 0
    assert report["handled_action_count"] == 1
    assert report["handled_actions"][0]["lineage_status"] == "ignored"
    assert report["handled_actions"][0]["lineage_label"] == "Closed"
    assert report["handled_actions"][0]["lineage_history_outcome"] == "superseded"
    assert report["handled_actions"][0]["stable_key_matched"] is True
    assert report["handled_actions"][0]["evidence_event_rotated"] is True
    assert report["handled_actions"][0]["previous_action_id"] == "calendar-sync-old"
    assert "superseded group outcome" in report["handled_actions"][0]["lineage_reason"]
    assert report["proposal_review"]["mode"] == "monitor"
    assert report["proposal_review"]["ignored_count"] == 1
    assert report["next_signal"]["status"] == "monitor"
