"""Tests for coordination console operator views."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from unittest.mock import patch

import notification_hub.operations as ops_mod
from notification_hub.operations import (
    CoordinationConsoleActionReport,
    _rich_handled_follow_up_actions,  # pyright: ignore[reportPrivateUsage]
    run_coordination_console,
    run_coordination_readiness,
)


def _coordination_action_report(
    *,
    evidence_timestamp: str = "2026-05-10T04:50:00+00:00",
    outcome_recorded_at: str | None = "2026-05-10T04:00:00+00:00",
) -> CoordinationConsoleActionReport:
    return {
        "action": {
            "action_id": "action-follow-up-rich",
            "dismissal_key": "proposal:personal-ops:mail:waiting-on-user:stable",
            "source": "personal-ops",
            "project": "mail",
            "intent": "waiting_on_user",
            "priority": "high",
            "state": "waiting",
            "title": "Approval Requested",
            "summary": "Rich approval draft needs inspection.",
            "signal_level": "urgent",
            "signal_body": "Approval draft",
            "suggested_next_action": "Review the waiting item.",
            "evidence_event_id": "event-follow-up-rich",
            "evidence_timestamp": evidence_timestamp,
            "evidence_context": {
                "thread_id": "thread-rich",
                "draft_id": "draft-rich",
                "approval_id": "approval-rich",
            },
            "evidence_quality": "rich",
            "count": 4,
        },
        "lineage_status": "follow_up",
        "lineage_label": "Needs follow-up",
        "lineage_next_action": "Evidence was inspected and needs operator follow-up.",
        "lineage_reason": "Latest needs_follow_up group outcome matched this action id.",
        "lineage_history_event_type": "outcome",
        "lineage_history_recorded_at": outcome_recorded_at,
        "lineage_history_outcome": "needs_follow_up",
        "stable_key_matched": False,
        "evidence_event_rotated": False,
        "previous_action_id": "action-follow-up-rich",
        "queue_id": None,
        "queue_status": None,
        "promotion_outcome": None,
        "promotion_target_id": None,
    }


def _coordination_status(
    *,
    status: str = "ok",
    policy_warning_count: int = 0,
    queued_count: int = 0,
    promoted_count: int = 3,
    promoted_pending_count: int = 0,
    promoted_pending_stale_count: int = 0,
) -> dict[str, object]:
    return {
        "status": status,
        "health_url": "http://127.0.0.1:9199/health/details",
        "daemon_reachable": True,
        "watcher_active": True,
        "events_processed": 12,
        "uptime_seconds": 123.4,
        "policy_config_found": True,
        "policy_warning_count": policy_warning_count,
        "retention_enabled": True,
        "retention_last_status": "ok",
        "runtime_wiring_current": True,
        "push_notifier_available": True,
        "slack_configured": True,
        "slack_delivery_failures": 0,
        "import_queue": {
            "status": "warn" if promoted_pending_stale_count else "ok",
            "queue_path": "/tmp/queue.jsonl",
            "total_count": promoted_count + queued_count,
            "queued_count": queued_count,
            "reviewed_count": 0,
            "rejected_count": 0,
            "snoozed_count": 0,
            "superseded_count": 0,
            "promoted_count": promoted_count,
            "promoted_pending_count": promoted_pending_count,
            "promoted_pending_stale_count": promoted_pending_stale_count,
            "promoted_accepted_count": promoted_count,
            "promoted_rejected_count": 0,
            "promoted_ignored_count": 0,
            "needs_outcome_sync": promoted_pending_count > 0,
            "needs_review": queued_count > 0,
            "oldest_queued_at": None,
            "oldest_queued_age_seconds": None,
            "oldest_promoted_pending_at": None,
            "oldest_promoted_pending_age_seconds": None,
            "stale_after_hours": 4.0,
            "next_action": "No queued personal-ops handoff items.",
        },
        "next_action": "No action needed.",
    }


def _coordination_burn_in_report(*, noise_candidate_count: int = 0) -> dict[str, object]:
    return {
        "path": "/tmp/report.json",
        "name": "personal-ops-queue-burn-in-20260510-040904.json",
        "modified_at": "2026-05-10T04:09:04+00:00",
        "size_bytes": 200,
        "status": "ok",
        "generated_at": "2026-05-10T04:09:04+00:00",
        "ready_for_live_promotion": noise_candidate_count == 0,
        "queued_count": 0,
        "pending_count": 0,
        "stale_count": 0,
        "runtime_status": "ok",
        "noise_candidate_count": noise_candidate_count,
        "next_action": "Queue loop is ready.",
    }


def test_coordination_readiness_reports_ready_to_expand() -> None:
    with (
        patch("notification_hub.operations.run_status", return_value=_coordination_status()),
        patch(
            "notification_hub.operations.list_personal_ops_queue_burn_in_reports",
            return_value=[_coordination_burn_in_report()],
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
        patch("notification_hub.operations.run_status", return_value=_coordination_status()),
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
        patch("notification_hub.operations.run_status", return_value=_coordination_status()),
        patch(
            "notification_hub.operations.list_personal_ops_queue_burn_in_reports",
            return_value=[_coordination_burn_in_report(noise_candidate_count=2)],
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
                "health": _coordination_status()["import_queue"],
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
            return_value=[_coordination_burn_in_report()],
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
                "health": _coordination_status()["import_queue"],
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
            return_value=[_coordination_burn_in_report()],
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
                "health": _coordination_status()["import_queue"],
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
            return_value=[_coordination_burn_in_report()],
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
                "health": _coordination_status(promoted_count=3)["import_queue"],
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
            return_value=[_coordination_burn_in_report()],
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
                "health": _coordination_status()["import_queue"],
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
            return_value=[_coordination_burn_in_report()],
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


def test_coordination_console_treats_follow_up_outcome_as_history(tmp_path: Path) -> None:
    group_history_path = tmp_path / "group-history.jsonl"
    group_history_path.write_text(
        json.dumps(
            {
                "group_key": "personal-ops:mail:waiting_on_user:high:waiting",
                "event_type": "outcome",
                "recorded_at": "2026-05-10T04:45:00+00:00",
                "status": "ok",
                "action_count": 1,
                "action_ids": ["action-follow-up"],
                "action_keys": ["proposal:personal-ops:mail:waiting-on-user:stable"],
                "package_path": None,
                "queued_count": None,
                "dismissed_count": None,
                "outcome": "needs_follow_up",
                "reason": "operator inspection needed",
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
                "generated_at": "2026-05-10T04:40:00+00:00",
                "hours": 2,
                "actions": [
                    {
                        "action_id": "action-follow-up",
                        "dismissal_key": "proposal:personal-ops:mail:waiting-on-user:stable",
                        "source": "personal-ops",
                        "project": "mail",
                        "intent": "waiting_on_user",
                        "priority": "high",
                        "state": "waiting",
                        "title": "Approval Requested",
                        "summary": "Approval draft needs inspection.",
                        "signal_body": "Approval draft",
                        "suggested_next_action": "Review the waiting item.",
                        "evidence_event_id": "event-follow-up",
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
                "health": _coordination_status()["import_queue"],
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
            return_value=[_coordination_burn_in_report()],
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
    assert report["handled_actions"][0]["lineage_status"] == "follow_up"
    assert report["handled_actions"][0]["lineage_label"] == "Needs follow-up"
    assert report["proposal_review"]["mode"] == "monitor"
    assert report["proposal_review"]["follow_up_count"] == 1
    assert report["proposal_review"]["handled_stable_key_match_count"] == 0
    assert report["proposal_review"]["handled_evidence_rotation_count"] == 0
    assert report["handled_actions"][0]["stable_key_matched"] is False
    assert report["handled_actions"][0]["evidence_event_rotated"] is False
    assert "action id" in report["handled_actions"][0]["lineage_reason"]
    assert report["proposal_review"]["handled_mail_count"] == 1
    assert report["proposal_review"]["handled_mail_thin_count"] == 1
    assert "handled mail follow-up" in report["proposal_review"]["summary"]
    assert "follow-up" in report["proposal_review"]["summary"]
    assert report["next_signal"]["status"] == "monitor"
    assert report["next_signal"]["watch_posture"] == "notify_only_on_active_work"
    assert report["next_signal"]["quiet_reason"] is not None
    assert report["next_action"] == "Monitor /review for the next real handoff signal."


def test_handoff_outcome_quality_splits_rich_and_thin_results() -> None:
    build_outcome_quality = getattr(ops_mod, "_build_handoff_outcome_quality")
    report = build_outcome_quality(
        [
            {
                "status": "promoted",
                "promotion_outcome": "accepted",
                "action": {
                    "evidence_context": {
                        "thread_id": "thread-rich",
                        "draft_id": "draft-rich",
                    },
                },
            },
            {
                "status": "promoted",
                "promotion_outcome": "rejected",
                "action": {"evidence_context": {"thread_id": "thread-thin"}},
            },
            {
                "status": "promoted",
                "promotion_outcome": "pending",
                "action": {"evidence_quality": "rich"},
            },
            {
                "status": "queued",
                "promotion_outcome": "accepted",
                "action": {"evidence_quality": "rich"},
            },
        ]
    )

    assert report["rich"]["total"] == 2
    assert report["rich"]["accepted"] == 1
    assert report["rich"]["pending"] == 1
    assert report["rich"]["acceptance_rate"] == 1.0
    assert report["thin"]["total"] == 1
    assert report["thin"]["rejected"] == 1
    assert "rich 1/2 resolved" in report["summary"]
    assert report["next_action"] == "Record 1 pending promoted outcome(s)."


def test_coordination_console_keeps_follow_up_history_when_action_id_rotates(
    tmp_path: Path,
) -> None:
    group_history_path = tmp_path / "group-history.jsonl"
    group_history_path.write_text(
        json.dumps(
            {
                "group_key": "personal-ops:mail:waiting_on_user:high:waiting",
                "event_type": "outcome",
                "recorded_at": "2026-05-10T04:45:00+00:00",
                "status": "ok",
                "action_count": 1,
                "action_ids": ["action-follow-up-old"],
                "action_keys": ["proposal:personal-ops:mail:waiting-on-user:stable"],
                "package_path": None,
                "queued_count": None,
                "dismissed_count": None,
                "outcome": "needs_follow_up",
                "reason": "operator inspection needed",
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
                "generated_at": "2026-05-10T04:50:00+00:00",
                "hours": 2,
                "actions": [
                    {
                        "action_id": "action-follow-up-new",
                        "dismissal_key": "proposal:personal-ops:mail:waiting-on-user:stable",
                        "source": "personal-ops",
                        "project": "mail",
                        "intent": "waiting_on_user",
                        "priority": "high",
                        "state": "waiting",
                        "title": "Approval Requested",
                        "summary": "Approval draft needs inspection.",
                        "signal_body": "Approval draft",
                        "suggested_next_action": "Review the waiting item.",
                        "evidence_event_id": "event-follow-up-new",
                        "evidence_timestamp": "2026-05-10T04:50:00+00:00",
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
                "health": _coordination_status()["import_queue"],
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
            return_value=[_coordination_burn_in_report()],
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
    assert report["handled_actions"][0]["lineage_status"] == "follow_up"
    assert report["proposal_review"]["mode"] == "monitor"
    assert report["proposal_review"]["follow_up_count"] == 1
    assert report["proposal_review"]["handled_stable_key_match_count"] == 1
    assert report["proposal_review"]["handled_evidence_rotation_count"] == 1
    assert report["handled_actions"][0]["stable_key_matched"] is True
    assert report["handled_actions"][0]["evidence_event_rotated"] is True
    assert report["handled_actions"][0]["previous_action_id"] == "action-follow-up-old"
    assert "stable proposal key" in report["handled_actions"][0]["lineage_reason"]


def test_coordination_console_surfaces_rich_handled_follow_up_for_review(
    tmp_path: Path,
) -> None:
    group_history_path = tmp_path / "group-history.jsonl"
    group_history_path.write_text(
        json.dumps(
            {
                "group_key": "personal-ops:mail:waiting_on_user:high:waiting",
                "event_type": "outcome",
                "recorded_at": "2026-05-10T04:45:00+00:00",
                "status": "ok",
                "action_count": 1,
                "action_ids": ["action-follow-up-old"],
                "action_keys": ["proposal:personal-ops:mail:waiting-on-user:stable"],
                "package_path": None,
                "queued_count": None,
                "dismissed_count": None,
                "outcome": "needs_follow_up",
                "reason": "operator inspection needed",
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
                "generated_at": "2026-05-10T04:50:00+00:00",
                "hours": 2,
                "actions": [
                    {
                        "action_id": "action-follow-up-rich",
                        "dismissal_key": "proposal:personal-ops:mail:waiting-on-user:stable",
                        "source": "personal-ops",
                        "project": "mail",
                        "intent": "waiting_on_user",
                        "priority": "high",
                        "state": "waiting",
                        "title": "Approval Requested",
                        "summary": "Rich approval draft needs inspection.",
                        "signal_body": "Approval draft",
                        "suggested_next_action": "Review the waiting item.",
                        "evidence_event_id": "event-follow-up-rich",
                        "evidence_timestamp": "2026-05-10T04:50:00+00:00",
                        "evidence_context": {
                            "thread_id": "thread-rich",
                            "draft_id": "draft-rich",
                            "approval_id": "approval-rich",
                        },
                        "evidence_quality": "rich",
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
                "health": _coordination_status()["import_queue"],
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
            return_value=[_coordination_burn_in_report()],
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
    assert report["handled_actions"][0]["lineage_status"] == "follow_up"
    assert report["proposal_review"]["mode"] == "follow_up_review"
    assert report["proposal_review"]["rich_follow_up_review_count"] == 1
    assert report["proposal_review"]["rich_follow_up_action_ids"] == ["action-follow-up-rich"]
    assert report["next_signal"]["status"] == "review"
    assert report["next_signal"]["title"] == "Rich handled follow-up needs re-review"
    assert report["next_signal"]["watch_posture"] == "notify_review"
    assert report["next_signal"]["rich_follow_up_review_count"] == 1
    assert report["guide_stage"] == "rich_follow_up_review"
    assert report["guide_steps"][0]["title"] == "Review rich handled follow-up"
    assert report["guide_steps"][0]["action_id"] == "action-follow-up-rich"
    assert report["next_action"] == (
        "Review rich handled follow-up history and record an explicit group outcome if it "
        "should re-open."
    )


def test_rich_follow_up_review_clears_after_fresh_group_outcome() -> None:
    stale_outcome = _coordination_action_report(
        evidence_timestamp="2026-05-10T04:50:00+00:00",
        outcome_recorded_at="2026-05-10T04:00:00+00:00",
    )
    fresh_outcome = _coordination_action_report(
        evidence_timestamp="2026-05-10T04:50:00+00:00",
        outcome_recorded_at="2026-05-10T05:00:00+00:00",
    )

    assert _rich_handled_follow_up_actions([stale_outcome]) == [stale_outcome]
    assert _rich_handled_follow_up_actions([fresh_outcome]) == []


def test_rich_follow_up_review_clears_multiple_fresh_group_outcomes() -> None:
    first_fresh_outcome = _coordination_action_report(
        evidence_timestamp="2026-05-10T04:50:00+00:00",
        outcome_recorded_at="2026-05-10T05:00:00+00:00",
    )
    second_fresh_outcome = _coordination_action_report(
        evidence_timestamp="2026-05-10T04:55:00+00:00",
        outcome_recorded_at="2026-05-10T05:01:00+00:00",
    )
    second_fresh_outcome["action"]["action_id"] = "action-follow-up-rich-2"

    assert _rich_handled_follow_up_actions([first_fresh_outcome, second_fresh_outcome]) == []


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
                "health": _coordination_status()["import_queue"],
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
            return_value=[_coordination_burn_in_report()],
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
    queue_health = cast(dict[str, object], _coordination_status(queued_count=1)["import_queue"])
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
            return_value=[_coordination_burn_in_report()],
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
