"""Tests for coordination console operator views."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from notification_hub.operations import run_coordination_console, run_coordination_readiness
from tests.coordination_console_fixtures import (
    coordination_burn_in_report,
    coordination_status,
)


def test_coordination_readiness_reports_ready_to_expand() -> None:
    with (
        patch("notification_hub.operations.run_status", return_value=coordination_status()),
        patch(
            "notification_hub.operations.list_personal_ops_queue_burn_in_reports",
            return_value=[coordination_burn_in_report()],
        ) as mock_reports,
    ):
        report = run_coordination_readiness(limit=3)

    assert report["status"] == "ok"
    assert report["decision"] == "ready_to_expand"
    assert report["latest_burn_in_noise_candidates"] == 0
    assert report["applied"] is False
    mock_reports.assert_called_once_with(limit=3)


def test_coordination_readiness_keeps_burning_in_without_saved_reports() -> None:
    with (
        patch("notification_hub.operations.run_status", return_value=coordination_status()),
        patch(
            "notification_hub.operations.list_personal_ops_queue_burn_in_reports",
            return_value=[],
        ),
    ):
        report = run_coordination_readiness()

    assert report["status"] == "ok"
    assert report["decision"] == "keep_burning_in"
    assert report["latest_burn_in_ready"] is None
    assert "not enough saved burn-in evidence" in report["summary"]


def test_coordination_readiness_prioritizes_noise_before_expansion() -> None:
    with (
        patch("notification_hub.operations.run_status", return_value=coordination_status()),
        patch(
            "notification_hub.operations.list_personal_ops_queue_burn_in_reports",
            return_value=[coordination_burn_in_report(noise_candidate_count=2)],
        ),
    ):
        report = run_coordination_readiness()

    assert report["status"] == "warn"
    assert report["decision"] == "fix_noise_first"
    assert "noise candidates" in report["summary"]


def test_coordination_console_summarizes_ready_expansion(tmp_path: Path) -> None:
    group_history_path = tmp_path / "group-history.jsonl"
    group_history_path.write_text(
        json.dumps(
            {
                "group_key": "personal-ops:mail:waiting_on_user:high:waiting",
                "event_type": "outcome",
                "recorded_at": "2026-05-10T04:38:00+00:00",
                "status": "ok",
                "action_count": 2,
                "action_ids": ["older-action-1", "older-action-2"],
                "package_path": None,
                "queued_count": None,
                "dismissed_count": None,
                "outcome": "needs_follow_up",
                "reason": "operator follow-up needed",
                "error": None,
            }
        )
        + "\n"
        + json.dumps(
            {
                "group_key": "personal-ops:mail:waiting_on_user:high:waiting",
                "event_type": "saved",
                "recorded_at": "2026-05-10T04:39:00+00:00",
                "status": "ok",
                "action_count": 2,
                "action_ids": ["action-1", "action-2"],
                "package_path": "/tmp/package.json",
                "queued_count": None,
                "dismissed_count": None,
                "reason": None,
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
        ) as mock_readiness,
        patch(
            "notification_hub.operations.run_personal_ops_action_export",
            return_value={
                "status": "ok",
                "schema_version": "notification-hub.personal_ops_action_export.v1",
                "generated_at": "2026-05-10T04:40:00+00:00",
                "hours": 2,
                "actions": [
                    {
                        "action_id": "action-1",
                        "source": "personal-ops",
                        "project": "mail",
                        "intent": "waiting_on_user",
                        "priority": "high",
                        "state": "waiting",
                        "title": "Approval Requested",
                        "summary": "Repeated mail approval.",
                        "signal_body": "Initial reply needed",
                        "suggested_next_action": "Review the waiting item.",
                        "evidence_event_id": "event-1",
                        "evidence_timestamp": "2026-05-10T04:40:00+00:00",
                        "evidence_context": {
                            "thread_id": "thread-1",
                            "draft_id": "draft-1",
                            "approval_id": "approval-1",
                        },
                        "evidence_quality": "rich",
                        "count": 3,
                    },
                    {
                        "action_id": "action-2",
                        "source": "personal-ops",
                        "project": "mail",
                        "intent": "waiting_on_user",
                        "priority": "high",
                        "state": "waiting",
                        "title": "Approval Requested",
                        "summary": "Second repeated mail approval.",
                        "signal_body": "Phase 32 review and approval flow",
                        "suggested_next_action": "Review the waiting item.",
                        "evidence_event_id": "event-2",
                        "evidence_timestamp": "2026-05-10T04:42:00+00:00",
                        "evidence_context": {},
                        "evidence_quality": "thin",
                        "count": 2,
                    },
                ],
                "review_package": {"status": "not_requested"},
                "inbox": {},
                "error": None,
            },
        ) as mock_actions,
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
        ) as mock_queue,
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
        ) as mock_reminder,
        patch(
            "notification_hub.operations.list_personal_ops_queue_burn_in_reports",
            return_value=[coordination_burn_in_report()],
        ) as mock_reports,
        patch("notification_hub.operations._read_import_queue_items", return_value=[]),
    ):
        report = run_coordination_console(hours=2, limit=3, group_history_path=group_history_path)

    assert report["status"] == "ok"
    assert report["readiness"]["decision"] == "ready_to_expand"
    assert report["action_count"] == 2
    assert report["active_action_count"] == 2
    assert report["handled_action_count"] == 0
    assert report["actions"][0]["lineage_status"] == "new"
    assert report["proposal_review"]["mode"] == "batch_review"
    assert report["proposal_review"]["new_count"] == 2
    assert report["proposal_review"]["group_count"] == 1
    assert report["proposal_review"]["groups"][0]["action_count"] == 2
    assert report["proposal_review"]["groups"][0]["total_event_count"] == 5
    assert report["proposal_review"]["groups"][0]["rich_evidence_count"] == 1
    assert report["proposal_review"]["groups"][0]["thin_evidence_count"] == 1
    assert report["proposal_review"]["groups"][0]["promotion_readiness"] == "split_required"
    assert report["proposal_review"]["groups"][0]["promotion_ready_action_ids"] == ["action-1"]
    assert report["proposal_review"]["groups"][0]["promotion_blocked_action_ids"] == ["action-2"]
    assert (
        "Queue only the rich promote route"
        in (report["proposal_review"]["groups"][0]["promotion_readiness_summary"])
    )
    assert report["proposal_review"]["groups"][0]["newest_evidence_timestamp"] == (
        "2026-05-10T04:42:00+00:00"
    )
    assert report["proposal_review"]["groups"][0]["history_count"] == 2
    assert report["proposal_review"]["groups"][0]["latest_history"] is not None
    assert report["proposal_review"]["groups"][0]["latest_history"]["event_type"] == "saved"
    assert report["proposal_review"]["groups"][0]["latest_outcome"] == "needs_follow_up"
    routing = report["proposal_review"]["groups"][0]["routing_recommendation"]
    assert routing is not None
    assert routing["decision"] == "split_mail_batch"
    assert routing["operator_decision_required_count"] == 1
    assert routing["promote_candidate_count"] == 1
    assert routing["suppress_candidate_count"] == 1
    assert routing["operator_decision_required_action_ids"] == ["action-1"]
    assert routing["promote_candidate_action_ids"] == ["action-1"]
    assert routing["suppress_candidate_action_ids"] == ["action-2"]
    assert report["proposal_review"]["group_history"][0]["group_key"] == (
        "personal-ops:mail:waiting_on_user:high:waiting"
    )
    assert report["next_signal"]["status"] == "ready"
    assert report["next_signal"]["title"] == "Active proposal waiting"
    assert report["next_signal"]["watch_posture"] == "notify_now"
    assert report["outcome_quality"]["summary"] == (
        "No promoted handoff outcomes are recorded yet."
    )
    assert report["guide_stage"] == "package_review"
    assert report["guide_steps"][0]["title"] == "Save review package"
    assert report["guide_steps"][0]["action_id"] == "action-1"
    assert report["next_commands"] == [
        "uv run notification-hub personal-ops-actions --save-review-package"
    ]
    assert report["next_action"] == (
        "Save and validate a review package, then queue one handoff for operator review."
    )
    assert report["applied"] is False
    mock_readiness.assert_called_once_with(limit=3)
    mock_actions.assert_called_once_with(hours=2, limit=3)
    mock_queue.assert_called_once_with(limit=3)
    mock_reminder.assert_called_once_with(limit=3)
    mock_reports.assert_called_once_with(limit=3)


def test_coordination_console_default_hours_matches_action_export() -> None:
    """The default console window must match personal-ops-actions (24h).

    Why: a narrower default silently hides proposals whose latest evidence has
    aged past the window, defeating the operator handoff path before it can be
    exercised. See investigation 2026-05-11 where 3 rich-evidence approvals
    aged ~6 h were invisible under the prior 2 h default.
    """
    empty_action_export: dict[str, object] = {
        "status": "ok",
        "schema_version": "notification-hub.personal_ops_action_export.v1",
        "generated_at": "2026-05-11T00:00:00+00:00",
        "hours": 24,
        "actions": [],
        "dismissed_action_count": 0,
        "dismissals": [],
        "review_package": {
            "requested": False,
            "status": "not_requested",
            "path": None,
            "error": None,
        },
        "inbox": {},
        "error": None,
    }
    with (
        patch(
            "notification_hub.operations.run_coordination_readiness",
            return_value={
                "status": "ok",
                "decision": "ready_to_expand",
                "summary": "ready.",
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
                "evidence": [],
                "applied": False,
            },
        ),
        patch(
            "notification_hub.operations.run_personal_ops_action_export",
            return_value=empty_action_export,
        ) as mock_actions,
        patch(
            "notification_hub.operations.run_personal_ops_import_queue_health_check",
            return_value={
                "status": "ok",
                "health": coordination_status()["import_queue"],
                "queued_items": [],
                "pending_promotion_items": [],
                "next_commands": [],
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
                "next_commands": [],
                "next_action": "No pending.",
                "applied": False,
            },
        ),
        patch(
            "notification_hub.operations.list_personal_ops_queue_burn_in_reports",
            return_value=[coordination_burn_in_report()],
        ),
        patch("notification_hub.operations._read_import_queue_items", return_value=[]),
    ):
        run_coordination_console()

    mock_actions.assert_called_once_with(hours=24, limit=5)


def test_coordination_console_keeps_thin_mail_promote_cues_in_follow_up(
    tmp_path: Path,
) -> None:
    with (
        patch(
            "notification_hub.operations.run_coordination_readiness",
            return_value={
                "status": "ok",
                "decision": "ready_to_expand",
                "summary": "ready",
                "queue_status": "ok",
                "queued_count": 0,
                "pending_count": 0,
                "stale_count": 0,
                "saved_burn_in_reports": 1,
                "latest_burn_in_ready": True,
                "latest_burn_in_noise_candidates": 0,
                "runtime_status": "ok",
                "policy_warning_count": 0,
                "next_action": "Review proposals.",
                "evidence": [],
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
                        "action_id": "rich-action",
                        "source": "personal-ops",
                        "project": "mail",
                        "intent": "waiting_on_user",
                        "priority": "high",
                        "state": "waiting",
                        "title": "Approval Requested",
                        "summary": "Context-rich reply.",
                        "signal_body": "Initial reply needed",
                        "suggested_next_action": "Review the waiting item.",
                        "evidence_event_id": "rich-event",
                        "evidence_timestamp": "2026-05-10T04:40:00+00:00",
                        "evidence_context": {"thread_id": "thread-1", "draft_id": "draft-1"},
                        "evidence_quality": "rich",
                        "count": 2,
                    },
                    {
                        "action_id": "thin-action",
                        "source": "personal-ops",
                        "project": "mail",
                        "intent": "waiting_on_user",
                        "priority": "high",
                        "state": "waiting",
                        "title": "Approval Requested",
                        "summary": "Thin reply.",
                        "signal_body": "Send this reply",
                        "suggested_next_action": "Review the waiting item.",
                        "evidence_event_id": "thin-event",
                        "evidence_timestamp": "2026-05-10T04:41:00+00:00",
                        "evidence_context": {"draft_id": "draft-2"},
                        "evidence_quality": "thin",
                        "count": 2,
                    },
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
            group_history_path=tmp_path / "history.jsonl",
        )

    group = report["proposal_review"]["groups"][0]
    routing = group["routing_recommendation"]
    assert routing is not None
    assert group["rich_evidence_count"] == 1
    assert group["thin_evidence_count"] == 1
    assert group["promotion_readiness"] == "split_required"
    assert group["promotion_ready_action_ids"] == ["rich-action"]
    assert group["promotion_blocked_action_ids"] == ["thin-action"]
    assert routing["decision"] == "promote_rich_evidence"
    assert routing["promote_candidate_action_ids"] == ["rich-action"]
    assert routing["follow_up_candidate_action_ids"] == ["thin-action"]
