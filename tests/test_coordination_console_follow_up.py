"""Tests for coordination console follow-up review handling."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import notification_hub.operations as ops_mod
from notification_hub.operations import (
    _rich_handled_follow_up_actions,  # pyright: ignore[reportPrivateUsage]
    run_coordination_console,
)
from tests.coordination_console_fixtures import (
    coordination_action_report,
    coordination_burn_in_report,
    coordination_status,
)


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
    stale_outcome = coordination_action_report(
        evidence_timestamp="2026-05-10T04:50:00+00:00",
        outcome_recorded_at="2026-05-10T04:00:00+00:00",
    )
    fresh_outcome = coordination_action_report(
        evidence_timestamp="2026-05-10T04:50:00+00:00",
        outcome_recorded_at="2026-05-10T05:00:00+00:00",
    )

    assert _rich_handled_follow_up_actions([stale_outcome]) == [stale_outcome]
    assert _rich_handled_follow_up_actions([fresh_outcome]) == []


def test_rich_follow_up_review_clears_multiple_fresh_group_outcomes() -> None:
    first_fresh_outcome = coordination_action_report(
        evidence_timestamp="2026-05-10T04:50:00+00:00",
        outcome_recorded_at="2026-05-10T05:00:00+00:00",
    )
    second_fresh_outcome = coordination_action_report(
        evidence_timestamp="2026-05-10T04:55:00+00:00",
        outcome_recorded_at="2026-05-10T05:01:00+00:00",
    )
    second_fresh_outcome["action"]["action_id"] = "action-follow-up-rich-2"

    assert _rich_handled_follow_up_actions([first_fresh_outcome, second_fresh_outcome]) == []
