"""Tests for smoke and retention operator actions."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import httpx
import pytest

import notification_hub.operations as ops_mod
from notification_hub.config import (
    ClassificationPolicy,
    NoisePolicy,
    NoiseRule,
    PolicyConfig,
    RetentionPolicy,
    RoutingPolicy,
    RoutingRule,
    SuppressionPolicy,
)
from notification_hub.models import StoredEvent
from notification_hub.operations import (
    bootstrap_policy_config,
    delete_action_review_package,
    dismiss_action_proposal,
    list_action_review_packages,
    list_action_proposal_dismissals,
    list_action_proposal_group_history,
    list_personal_ops_queue_burn_in_reports,
    list_personal_ops_import_queue,
    load_action_review_package_detail,
    load_personal_ops_queue_burn_in_report_detail,
    run_action_proposal_dismissal_list,
    run_burn_in,
    run_coordination_console,
    run_coordination_readiness,
    run_coordination_snapshot,
    run_inbox,
    run_logs,
    run_operator_daily_state,
    run_operator_handoff_drill,
    run_operator_review_session,
    run_personal_ops_action_export,
    run_personal_ops_import_stub,
    run_personal_ops_outcome_sync_reminder,
    run_personal_ops_queue_burn_in,
    run_personal_ops_queue_scenario,
    run_policy_check,
    run_retention,
    save_action_proposal_group_package,
    run_smoke_check,
    summarize_personal_ops_import_queue,
    dismiss_action_proposal_group,
    undismiss_action_proposal,
    update_personal_ops_import_queue_item,
    validate_action_package,
    record_action_proposal_group_outcome,
)


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


def test_smoke_check_reports_success_when_event_hits_log() -> None:
    response = MagicMock()
    response.status_code = 201
    response.json.return_value = {"event_id": "abc123"}

    with (
        patch("notification_hub.operations.httpx.post", return_value=response),
        patch(
            "notification_hub.operations.read_jsonl",
            return_value=[
                StoredEvent(source="codex", level="info", title="x", body="y", event_id="abc123")
            ],
        ),
    ):
        report = run_smoke_check()

    assert report["status"] == "ok"
    assert report["event_id"] == "abc123"
    assert report["log_verified"] is True


def test_smoke_check_reports_http_failure() -> None:
    with patch(
        "notification_hub.operations.httpx.post",
        side_effect=httpx.ConnectError("boom"),
    ):
        report = run_smoke_check()

    assert report["status"] == "degraded"
    assert report["response_status"] is None
    assert report["event_id"] is None


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
                "action_ids": ["action-1", "action-2"],
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
                        "count": 2,
                    }
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
    assert routing["promote_candidate_count"] == 1
    assert routing["suppress_candidate_count"] == 1
    assert routing["promote_candidate_action_ids"] == ["action-1"]
    assert routing["suppress_candidate_action_ids"] == ["action-2"]
    assert report["proposal_review"]["group_history"][0]["group_key"] == (
        "personal-ops:mail:waiting_on_user:high:waiting"
    )
    assert report["next_signal"]["status"] == "ready"
    assert report["next_signal"]["title"] == "Active proposal waiting"
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
    assert report["handled_actions"][0]["queue_id"] == "queue-resolved"
    assert report["guide_stage"] == "monitor"
    assert report["guide_steps"][0]["summary"].startswith("1 handled proposal")
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
    assert report["proposal_review"]["mode"] == "monitor"
    assert report["guide_stage"] == "monitor"
    assert report["next_action"] == "Monitor /review for the next real handoff signal."


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
                "next_action": "Review queued personal-ops handoff items.",
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


def test_inbox_groups_recent_events_by_coordination_intent() -> None:
    events = [
        StoredEvent(
            source="codex",
            level="urgent",
            title="Approval Requested",
            body="Approval needed before merge",
            project="notification-hub",
        ),
        StoredEvent(
            source="codex",
            level="normal",
            title="Review ready",
            body="Ready to review implementation",
            project="notification-hub",
        ),
        StoredEvent(
            source="codex",
            level="normal",
            title="Codex finished a turn",
            body="A Codex turn completed.",
            project="notification-hub",
        ),
        StoredEvent(
            source="codex",
            level="normal",
            title="Codex finished a turn",
            body="A Codex turn completed.",
            project="notification-hub",
        ),
    ]

    with (
        patch("notification_hub.operations.read_jsonl", return_value=events),
        patch(
            "notification_hub.operations.run_burn_in",
            return_value={
                "status": "ok",
                "minutes": 1440,
                "events_seen": 4,
                "accepted_event_posts": 3,
                "rejected_event_posts": 0,
                "validation_error_count": 0,
                "health": {
                    "accepted_event_posts": 3,
                    "rejected_event_posts": 0,
                    "validation_error_count": 0,
                    "slack_delivery_failure_count": 0,
                    "status": "ok",
                },
                "noise_candidates": [
                    {
                        "count": 2,
                        "source": "codex",
                        "project": "notification-hub",
                        "level": "normal",
                        "title": "Codex finished a turn",
                        "body": "A Codex turn completed.",
                    }
                ],
                "repeated_signatures": [],
                "slack_eligible_events": 2,
                "slack_volume": [],
                "daemon_summary": {
                    "access_status_counts": {},
                    "accepted_event_posts": 0,
                    "rejected_event_posts": 0,
                    "validation_error_count": 0,
                    "recent_validation_errors": [],
                    "slack_delivery_failure_count": 0,
                    "recent_slack_delivery_failures": [],
                },
                "error": None,
            },
        ),
    ):
        report = run_inbox(hours=24, limit=5)

    assert report["status"] == "ok"
    assert report["events_seen"] == 4
    assert report["waiting_or_blocked"][0]["intent"] == "waiting_on_user"
    assert report["ready"][0]["intent"] == "ready_to_review"
    assert report["completed"][0]["intent"] == "completed"
    assert report["noise_candidates"][0]["title"] == "Codex finished a turn"
    assert report["rollups"][0]["count"] == 2
    assert report["rollups"][0]["title"] == "Codex finished a turn"


def test_coordination_snapshot_wraps_inbox_and_runtime_for_bridge_db() -> None:
    inbox_report: dict[str, object] = {
        "status": "ok",
        "hours": 2,
        "events_seen": 1,
        "needs_attention": [],
        "waiting_or_blocked": [],
        "ready": [
            {
                "event_id": "abc123",
                "timestamp": "2026-05-09T00:00:00+00:00",
                "source": "codex",
                "project": "notification-hub",
                "level": "normal",
                "intent": "ready_to_review",
                "title": "Review ready",
                "body": "Ready to review",
            }
        ],
        "completed": [],
        "rollups": [],
        "noise_candidates": [],
        "error": None,
    }
    status_report = {
        "status": "ok",
        "health_url": "http://127.0.0.1:9199/health/details",
        "daemon_reachable": True,
        "watcher_active": True,
        "events_processed": 10,
        "uptime_seconds": 123.4,
        "policy_config_found": True,
        "policy_warning_count": 0,
        "retention_enabled": True,
        "retention_last_status": "ok",
        "runtime_wiring_current": True,
        "push_notifier_available": True,
        "slack_configured": True,
        "slack_delivery_failures": 0,
        "next_action": "No action needed.",
    }

    with (
        patch("notification_hub.operations.run_inbox", return_value=inbox_report),
        patch("notification_hub.operations.run_status", return_value=status_report),
    ):
        report = run_coordination_snapshot(hours=2, limit=3)

    assert report["status"] == "ok"
    assert report["bridge_target_system"] == "codex"
    assert report["bridge_snapshot"]["coordination"] == {
        "generated_at": report["generated_at"],
        "hours": 2,
        "events_seen": 1,
        "needs_attention": [],
        "waiting_or_blocked": [],
        "ready": inbox_report["ready"],
        "completed": [],
        "rollups": [],
        "noise_candidates": [],
    }
    assert report["bridge_snapshot"]["runtime"] == status_report
    assert report["bridge_save"]["status"] == "not_requested"


def test_personal_ops_action_export_prepares_actions_from_rollups() -> None:
    inbox_report: dict[str, object] = {
        "status": "ok",
        "hours": 2,
        "events_seen": 2,
        "needs_attention": [],
        "waiting_or_blocked": [],
        "ready": [],
        "completed": [],
        "rollups": [
            {
                "count": 2,
                "source": "personal-ops",
                "project": "mail",
                "intent": "waiting_on_user",
                "level": "urgent",
                "title": "Approval Requested",
                "body": "Console reply needed",
                "latest_timestamp": "2026-05-09T00:00:00+00:00",
                "latest_event_id": "abc123",
            }
        ],
        "noise_candidates": [],
        "error": None,
    }

    with patch("notification_hub.operations.run_inbox", return_value=inbox_report) as mock_inbox:
        report = run_personal_ops_action_export(hours=2, limit=5)

    assert report["status"] == "ok"
    assert report["schema_version"] == "notification-hub.personal_ops_action_export.v1"
    assert report["actions"][0]["priority"] == "high"
    assert report["actions"][0]["state"] == "waiting"
    assert report["actions"][0]["action_id"].endswith(":abc123")
    assert report["actions"][0]["dismissal_key"].startswith(
        "proposal:personal-ops:mail:waiting-on-user:"
    )
    assert report["actions"][0]["signal_body"] == "Console reply needed"
    assert report["actions"][0]["evidence_event_id"] == "abc123"
    assert report["actions"][0]["suggested_next_action"] == (
        "Review the waiting item and approve, reply, or dismiss it."
    )
    assert report["review_package"]["status"] == "not_requested"
    assert report["dismissed_action_count"] == 0
    mock_inbox.assert_called_once_with(hours=2, limit=25)


def test_personal_ops_action_export_scans_past_dismissed_candidates(tmp_path: Path) -> None:
    dismissals_path = tmp_path / "dismissals.jsonl"
    dismissed_rollup = {
        "count": 3,
        "source": "personal-ops",
        "project": "mail",
        "intent": "waiting_on_user",
        "level": "urgent",
        "title": "Approval Requested",
        "body": "Known test draft",
        "latest_timestamp": "2026-05-09T00:00:00+00:00",
        "latest_event_id": "dismissed123",
    }
    active_rollup = {
        "count": 2,
        "source": "personal-ops",
        "project": "mail",
        "intent": "waiting_on_user",
        "level": "urgent",
        "title": "Approval Requested",
        "body": "Real reply needed",
        "latest_timestamp": "2026-05-09T00:01:00+00:00",
        "latest_event_id": "active123",
    }
    inbox_report: dict[str, object] = {
        "status": "ok",
        "hours": 2,
        "events_seen": 5,
        "needs_attention": [],
        "waiting_or_blocked": [],
        "ready": [],
        "completed": [],
        "rollups": [dismissed_rollup, active_rollup],
        "noise_candidates": [],
        "error": None,
    }

    with patch("notification_hub.operations.run_inbox", return_value=inbox_report):
        first_report = run_personal_ops_action_export(
            hours=2,
            limit=1,
            dismissals_path=dismissals_path,
        )
    dismiss_action_proposal(
        dismissal_key=first_report["actions"][0]["dismissal_key"],
        reason="known first candidate",
        dismissals_path=dismissals_path,
    )

    with patch("notification_hub.operations.run_inbox", return_value=inbox_report) as mock_inbox:
        second_report = run_personal_ops_action_export(
            hours=2,
            limit=1,
            dismissals_path=dismissals_path,
        )

    assert [action["signal_body"] for action in second_report["actions"]] == ["Real reply needed"]
    assert second_report["dismissed_action_count"] == 1
    mock_inbox.assert_called_once_with(hours=2, limit=25)


def test_action_proposal_dismissal_filters_matching_rollup(tmp_path: Path) -> None:
    dismissals_path = tmp_path / "dismissals.jsonl"
    inbox_report: dict[str, object] = {
        "status": "ok",
        "hours": 2,
        "events_seen": 2,
        "needs_attention": [],
        "waiting_or_blocked": [],
        "ready": [],
        "completed": [],
        "rollups": [
            {
                "count": 2,
                "source": "personal-ops",
                "project": "mail",
                "intent": "waiting_on_user",
                "level": "urgent",
                "title": "Approval Requested",
                "body": "Test draft",
                "latest_timestamp": "2026-05-09T00:00:00+00:00",
                "latest_event_id": "abc123",
            }
        ],
        "noise_candidates": [],
        "error": None,
    }

    with patch("notification_hub.operations.run_inbox", return_value=inbox_report):
        first_report = run_personal_ops_action_export(
            hours=2,
            limit=5,
            dismissals_path=dismissals_path,
        )
    dismissal_key = first_report["actions"][0]["dismissal_key"]

    dismiss_report = dismiss_action_proposal(
        dismissal_key=dismissal_key,
        reason="known test signal",
        source="personal-ops",
        project="mail",
        intent="waiting_on_user",
        title="Approval Requested",
        body="Test draft",
        evidence_event_id="abc123",
        dismissals_path=dismissals_path,
    )

    with patch("notification_hub.operations.run_inbox", return_value=inbox_report):
        second_report = run_personal_ops_action_export(
            hours=2,
            limit=5,
            dismissals_path=dismissals_path,
        )

    assert dismiss_report["status"] == "ok"
    assert second_report["actions"] == []
    assert second_report["dismissed_action_count"] == 1
    assert second_report["dismissals"][0]["dismissal_key"] == dismissal_key
    assert list_action_proposal_dismissals(dismissals_path=dismissals_path)[0]["reason"] == (
        "known test signal"
    )


def test_action_proposal_dismissals_can_be_listed_and_undismissed(tmp_path: Path) -> None:
    dismissals_path = tmp_path / "dismissals.jsonl"
    dismiss_report = dismiss_action_proposal(
        dismissal_key="proposal:personal-ops:mail:waiting-on-user:abc",
        reason="known repeated signal",
        source="personal-ops",
        project="mail",
        intent="waiting_on_user",
        title="Approval Requested",
        body="Test draft",
        evidence_event_id="abc123",
        dismissals_path=dismissals_path,
    )

    listed = run_action_proposal_dismissal_list(dismissals_path=dismissals_path)
    undismissed = undismiss_action_proposal(
        dismissal_key="proposal:personal-ops:mail:waiting-on-user:abc",
        reason="signal is useful again",
        dismissals_path=dismissals_path,
    )
    active_after = run_action_proposal_dismissal_list(dismissals_path=dismissals_path)
    inactive_after = run_action_proposal_dismissal_list(
        dismissals_path=dismissals_path,
        include_inactive=True,
    )

    assert dismiss_report["status"] == "ok"
    assert listed["dismissal_count"] == 1
    assert listed["dismissals"][0]["active"] is True
    assert undismissed["status"] == "ok"
    assert undismissed["removed"] is True
    assert active_after["dismissals"] == []
    assert inactive_after["dismissals"][0]["active"] is False
    assert inactive_after["dismissals"][0]["deleted_at"] is not None


def test_personal_ops_action_export_keeps_repeated_titles_unique() -> None:
    inbox_report: dict[str, object] = {
        "status": "ok",
        "hours": 2,
        "events_seen": 4,
        "needs_attention": [],
        "waiting_or_blocked": [],
        "ready": [],
        "completed": [],
        "rollups": [
            {
                "count": 2,
                "source": "personal-ops",
                "project": "mail",
                "intent": "waiting_on_user",
                "level": "urgent",
                "title": "Approval Requested",
                "body": "Outbound workflow reply",
                "latest_timestamp": "2026-05-09T00:00:00+00:00",
                "latest_event_id": "abc123",
            },
            {
                "count": 2,
                "source": "personal-ops",
                "project": "mail",
                "intent": "waiting_on_user",
                "level": "urgent",
                "title": "Approval Requested",
                "body": "Send this reply",
                "latest_timestamp": "2026-05-09T00:01:00+00:00",
                "latest_event_id": "def456",
            },
        ],
        "noise_candidates": [],
        "error": None,
    }

    with patch("notification_hub.operations.run_inbox", return_value=inbox_report):
        report = run_personal_ops_action_export(hours=2, limit=5)

    action_ids = [action["action_id"] for action in report["actions"]]
    assert len(action_ids) == len(set(action_ids))
    assert action_ids == [
        "notification-hub:personal-ops:mail:waiting_on_user:approval-requested:abc123",
        "notification-hub:personal-ops:mail:waiting_on_user:approval-requested:def456",
    ]


def test_action_proposal_group_package_can_save_selected_group(tmp_path: Path) -> None:
    inbox_report: dict[str, object] = {
        "status": "ok",
        "hours": 2,
        "events_seen": 4,
        "needs_attention": [],
        "waiting_or_blocked": [],
        "ready": [],
        "completed": [],
        "rollups": [
            {
                "count": 2,
                "source": "personal-ops",
                "project": "mail",
                "intent": "waiting_on_user",
                "level": "urgent",
                "title": "Approval Requested",
                "body": "Outbound workflow reply",
                "latest_timestamp": "2026-05-09T00:00:00+00:00",
                "latest_event_id": "abc123",
            },
            {
                "count": 2,
                "source": "personal-ops",
                "project": "mail",
                "intent": "waiting_on_user",
                "level": "urgent",
                "title": "Approval Requested",
                "body": "Send this reply",
                "latest_timestamp": "2026-05-09T00:01:00+00:00",
                "latest_event_id": "def456",
            },
            {
                "count": 2,
                "source": "codex",
                "project": "personal-ops",
                "intent": "ready_to_review",
                "level": "normal",
                "title": "Codex needs attention",
                "body": "A verification or runtime issue needs review.",
                "latest_timestamp": "2026-05-09T00:02:00+00:00",
                "latest_event_id": "ghi789",
            },
        ],
        "noise_candidates": [],
        "error": None,
    }

    with patch("notification_hub.operations.run_inbox", return_value=inbox_report):
        report = save_action_proposal_group_package(
            group_key="personal-ops:mail:waiting_on_user:high:waiting",
            hours=2,
            limit=5,
            review_dir=tmp_path,
            group_history_path=tmp_path / "group-history.jsonl",
        )

    assert report["status"] == "ok"
    assert report["action_count"] == 2
    package_path = Path(str(report["review_package"]["path"]))
    payload = json.loads(package_path.read_text(encoding="utf-8"))
    assert payload["selected_group"]["group_key"] == "personal-ops:mail:waiting_on_user:high:waiting"
    assert payload["selected_group"]["route"] == "all"
    assert [action["evidence_event_id"] for action in payload["actions"]] == ["abc123", "def456"]
    assert report["group_history"] is not None
    assert report["group_history"]["event_type"] == "saved"
    assert report["group_history"]["action_count"] == 2


def test_action_proposal_group_package_can_save_promote_route(tmp_path: Path) -> None:
    inbox_report: dict[str, object] = {
        "status": "ok",
        "hours": 2,
        "events_seen": 3,
        "needs_attention": [],
        "waiting_or_blocked": [],
        "ready": [],
        "completed": [],
        "rollups": [
            {
                "count": 2,
                "source": "personal-ops",
                "project": "mail",
                "intent": "waiting_on_user",
                "level": "urgent",
                "title": "Approval Requested",
                "body": "Outbound workflow reply",
                "latest_timestamp": "2026-05-09T00:00:00+00:00",
                "latest_event_id": "abc123",
            },
            {
                "count": 2,
                "source": "personal-ops",
                "project": "mail",
                "intent": "waiting_on_user",
                "level": "urgent",
                "title": "Approval Requested",
                "body": "Phase 36 prepared handoff",
                "latest_timestamp": "2026-05-09T00:01:00+00:00",
                "latest_event_id": "def456",
            },
            {
                "count": 2,
                "source": "personal-ops",
                "project": "mail",
                "intent": "waiting_on_user",
                "level": "urgent",
                "title": "Approval Requested",
                "body": "Orphan approval draft",
                "latest_timestamp": "2026-05-09T00:02:00+00:00",
                "latest_event_id": "ghi789",
            },
        ],
        "noise_candidates": [],
        "error": None,
    }

    with patch("notification_hub.operations.run_inbox", return_value=inbox_report):
        report = save_action_proposal_group_package(
            group_key="personal-ops:mail:waiting_on_user:high:waiting",
            route="promote",
            hours=2,
            limit=5,
            review_dir=tmp_path,
            group_history_path=tmp_path / "group-history.jsonl",
        )

    assert report["status"] == "ok"
    assert report["action_count"] == 1
    package_path = Path(str(report["review_package"]["path"]))
    payload = json.loads(package_path.read_text(encoding="utf-8"))
    assert payload["selected_group"]["route"] == "promote"
    assert [action["evidence_event_id"] for action in payload["actions"]] == ["abc123"]
    assert report["group_history"] is not None
    assert report["group_history"]["event_type"] == "saved_promote"


def test_action_review_package_names_are_collision_safe(tmp_path: Path) -> None:
    inbox_report: dict[str, object] = {
        "status": "ok",
        "hours": 2,
        "events_seen": 1,
        "needs_attention": [],
        "waiting_or_blocked": [],
        "ready": [],
        "completed": [],
        "rollups": [
            {
                "count": 2,
                "source": "personal-ops",
                "project": "mail",
                "intent": "waiting_on_user",
                "level": "urgent",
                "title": "Approval Requested",
                "body": "Outbound workflow reply",
                "latest_timestamp": "2026-05-09T00:00:00+00:00",
                "latest_event_id": "abc123",
            }
        ],
        "noise_candidates": [],
        "error": None,
    }

    with patch("notification_hub.operations.run_inbox", return_value=inbox_report):
        first = save_action_proposal_group_package(
            group_key="personal-ops:mail:waiting_on_user:high:waiting",
            review_dir=tmp_path,
            group_history_path=tmp_path / "group-history.jsonl",
        )
        second = save_action_proposal_group_package(
            group_key="personal-ops:mail:waiting_on_user:high:waiting",
            review_dir=tmp_path,
            group_history_path=tmp_path / "group-history.jsonl",
        )

    assert first["review_package"]["path"] != second["review_package"]["path"]
    assert Path(str(first["review_package"]["path"])).exists()
    assert Path(str(second["review_package"]["path"])).exists()


def test_action_proposal_group_package_can_enqueue_selected_group(tmp_path: Path) -> None:
    inbox_report: dict[str, object] = {
        "status": "ok",
        "hours": 2,
        "events_seen": 2,
        "needs_attention": [],
        "waiting_or_blocked": [],
        "ready": [],
        "completed": [],
        "rollups": [
            {
                "count": 2,
                "source": "personal-ops",
                "project": "mail",
                "intent": "waiting_on_user",
                "level": "urgent",
                "title": "Approval Requested",
                "body": "Outbound workflow reply",
                "latest_timestamp": "2026-05-09T00:00:00+00:00",
                "latest_event_id": "abc123",
            }
        ],
        "noise_candidates": [],
        "error": None,
    }

    with patch("notification_hub.operations.run_inbox", return_value=inbox_report):
        report = save_action_proposal_group_package(
            group_key="personal-ops:mail:waiting_on_user:high:waiting",
            hours=2,
            limit=5,
            enqueue=True,
            review_dir=tmp_path,
            queue_path=tmp_path / "queue.jsonl",
            group_history_path=tmp_path / "group-history.jsonl",
        )

    assert report["status"] == "ok"
    assert report["import_result"] is not None
    assert report["import_result"]["queued_count"] == 1
    assert (tmp_path / "queue.jsonl").exists()
    history = list_action_proposal_group_history(
        history_path=tmp_path / "group-history.jsonl",
    )
    assert history[0]["event_type"] == "queued"
    assert history[0]["queued_count"] == 1


def test_operator_review_session_summarizes_recent_group_and_queue_activity(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    group_history_path = tmp_path / "group-history.jsonl"
    group_history_records = [
        {
            "group_key": "personal-ops:mail:waiting_on_user:high:waiting",
            "event_type": "saved_promote",
            "recorded_at": (now - timedelta(minutes=20)).isoformat(),
            "status": "ok",
            "action_count": 2,
            "action_ids": ["action-1", "action-2"],
            "package_path": "/tmp/promote.json",
            "queued_count": None,
            "dismissed_count": None,
            "outcome": None,
            "reason": None,
            "error": None,
        },
        {
            "group_key": "personal-ops:mail:waiting_on_user:high:waiting",
            "event_type": "queued_promote",
            "recorded_at": (now - timedelta(minutes=15)).isoformat(),
            "status": "ok",
            "action_count": 2,
            "action_ids": ["action-1", "action-2"],
            "package_path": "/tmp/promote.json",
            "queued_count": 2,
            "dismissed_count": None,
            "outcome": None,
            "reason": None,
            "error": None,
        },
        {
            "group_key": "personal-ops:mail:waiting_on_user:low:open",
            "event_type": "dismissed_suppress",
            "recorded_at": (now - timedelta(minutes=10)).isoformat(),
            "status": "ok",
            "action_count": 1,
            "action_ids": ["action-3"],
            "package_path": None,
            "queued_count": None,
            "dismissed_count": 1,
            "outcome": None,
            "reason": "known repeated mail workflow chatter",
            "error": None,
        },
    ]
    group_history_path.write_text(
        "\n".join(json.dumps(record) for record in group_history_records) + "\n",
        encoding="utf-8",
    )
    queue_path = tmp_path / "queue.jsonl"
    queue_records = [
        {
            "queue_id": "queue-reviewed",
            "status": "reviewed",
            "enqueued_at": (now - timedelta(minutes=14)).isoformat(),
            "updated_at": (now - timedelta(minutes=9)).isoformat(),
            "source_package_name": "promote.json",
            "source_package_path": "/tmp/promote.json",
            "action_id": "action-1",
            "action": {
                "title": "Approval Requested",
                "summary": "Repeated approval request.",
                "priority": "high",
                "state": "waiting",
                "evidence_event_id": "event-1",
            },
            "applied": False,
        },
        {
            "queue_id": "queue-active",
            "status": "queued",
            "enqueued_at": (now - timedelta(minutes=8)).isoformat(),
            "updated_at": (now - timedelta(minutes=8)).isoformat(),
            "source_package_name": "promote.json",
            "source_package_path": "/tmp/promote.json",
            "action_id": "action-2",
            "action": {
                "title": "Reply Requested",
                "summary": "Repeated reply request.",
                "priority": "medium",
                "state": "waiting",
                "evidence_event_id": "event-2",
            },
            "applied": False,
        },
    ]
    queue_path.write_text(
        "\n".join(json.dumps(record) for record in queue_records) + "\n",
        encoding="utf-8",
    )

    report = run_operator_review_session(
        hours=2,
        limit=10,
        queue_path=queue_path,
        group_history_path=group_history_path,
    )

    assert report["status"] == "warn"
    assert report["applied"] is False
    assert report["saved_count"] == 1
    assert report["queued_count"] == 1
    assert report["dismissed_count"] == 1
    assert report["reviewed_count"] == 1
    assert report["active_queue_count"] == 1
    assert report["route_counts"] == {"promote": 2, "suppress": 1}
    assert len(report["group_summaries"]) == 2
    assert report["recent_queue_items"][0]["queue_id"] == "queue-active"


def test_action_proposal_group_dismisses_each_current_match(tmp_path: Path) -> None:
    inbox_report: dict[str, object] = {
        "status": "ok",
        "hours": 2,
        "events_seen": 2,
        "needs_attention": [],
        "waiting_or_blocked": [],
        "ready": [],
        "completed": [],
        "rollups": [
            {
                "count": 2,
                "source": "personal-ops",
                "project": "mail",
                "intent": "waiting_on_user",
                "level": "urgent",
                "title": "Approval Requested",
                "body": "Outbound workflow reply",
                "latest_timestamp": "2026-05-09T00:00:00+00:00",
                "latest_event_id": "abc123",
            },
            {
                "count": 2,
                "source": "personal-ops",
                "project": "mail",
                "intent": "waiting_on_user",
                "level": "urgent",
                "title": "Approval Requested",
                "body": "Send this reply",
                "latest_timestamp": "2026-05-09T00:01:00+00:00",
                "latest_event_id": "def456",
            },
        ],
        "noise_candidates": [],
        "error": None,
    }

    with patch("notification_hub.operations.run_inbox", return_value=inbox_report):
        report = dismiss_action_proposal_group(
            group_key="personal-ops:mail:waiting_on_user:high:waiting",
            reason="known grouped signal",
            hours=2,
            limit=5,
            dismissals_path=tmp_path / "dismissals.jsonl",
            group_history_path=tmp_path / "group-history.jsonl",
        )

    assert report["status"] == "ok"
    assert report["dismissed_count"] == 2
    assert {dismissal["body"] for dismissal in report["dismissals"]} == {
        "Outbound workflow reply",
        "Send this reply",
    }
    assert report["group_history"] is not None
    assert report["group_history"]["event_type"] == "dismissed"
    assert report["group_history"]["dismissed_count"] == 2


def test_action_proposal_group_dismisses_suppress_route_only(tmp_path: Path) -> None:
    inbox_report: dict[str, object] = {
        "status": "ok",
        "hours": 2,
        "events_seen": 3,
        "needs_attention": [],
        "waiting_or_blocked": [],
        "ready": [],
        "completed": [],
        "rollups": [
            {
                "count": 2,
                "source": "personal-ops",
                "project": "mail",
                "intent": "waiting_on_user",
                "level": "urgent",
                "title": "Approval Requested",
                "body": "Outbound workflow reply",
                "latest_timestamp": "2026-05-09T00:00:00+00:00",
                "latest_event_id": "abc123",
            },
            {
                "count": 2,
                "source": "personal-ops",
                "project": "mail",
                "intent": "waiting_on_user",
                "level": "urgent",
                "title": "Approval Requested",
                "body": "Phase 36 prepared handoff",
                "latest_timestamp": "2026-05-09T00:01:00+00:00",
                "latest_event_id": "def456",
            },
            {
                "count": 2,
                "source": "personal-ops",
                "project": "mail",
                "intent": "waiting_on_user",
                "level": "urgent",
                "title": "Approval Requested",
                "body": "Orphan approval draft",
                "latest_timestamp": "2026-05-09T00:02:00+00:00",
                "latest_event_id": "ghi789",
            },
        ],
        "noise_candidates": [],
        "error": None,
    }

    with patch("notification_hub.operations.run_inbox", return_value=inbox_report):
        report = dismiss_action_proposal_group(
            group_key="personal-ops:mail:waiting_on_user:high:waiting",
            route="suppress",
            reason="already covered workflow chatter",
            hours=2,
            limit=5,
            dismissals_path=tmp_path / "dismissals.jsonl",
            group_history_path=tmp_path / "group-history.jsonl",
        )

    assert report["status"] == "ok"
    assert report["dismissed_count"] == 1
    assert [dismissal["body"] for dismissal in report["dismissals"]] == [
        "Phase 36 prepared handoff"
    ]
    assert report["group_history"] is not None
    assert report["group_history"]["event_type"] == "dismissed_suppress"
    assert report["group_history"]["dismissed_count"] == 1


def test_action_proposal_group_outcome_records_local_decision(tmp_path: Path) -> None:
    inbox_report: dict[str, object] = {
        "status": "ok",
        "hours": 2,
        "events_seen": 1,
        "needs_attention": [],
        "waiting_or_blocked": [],
        "ready": [],
        "completed": [],
        "rollups": [
            {
                "count": 2,
                "source": "personal-ops",
                "project": "mail",
                "intent": "waiting_on_user",
                "level": "urgent",
                "title": "Approval Requested",
                "body": "Outbound workflow reply",
                "latest_timestamp": "2026-05-09T00:00:00+00:00",
                "latest_event_id": "abc123",
            }
        ],
        "noise_candidates": [],
        "error": None,
    }

    with patch("notification_hub.operations.run_inbox", return_value=inbox_report):
        report = record_action_proposal_group_outcome(
            group_key="personal-ops:mail:waiting_on_user:high:waiting",
            outcome="needs_follow_up",
            reason="operator follow-up required",
            hours=2,
            limit=5,
            group_history_path=tmp_path / "group-history.jsonl",
        )

    assert report["status"] == "ok"
    assert report["outcome"] == "needs_follow_up"
    assert report["group_history"] is not None
    assert report["group_history"]["event_type"] == "outcome"
    assert report["group_history"]["outcome"] == "needs_follow_up"


def test_personal_ops_action_export_can_save_review_package(tmp_path: Path) -> None:
    inbox_report: dict[str, object] = {
        "status": "ok",
        "hours": 2,
        "events_seen": 0,
        "needs_attention": [],
        "waiting_or_blocked": [],
        "ready": [],
        "completed": [],
        "rollups": [],
        "noise_candidates": [],
        "error": None,
    }
    with patch("notification_hub.operations.run_inbox", return_value=inbox_report):
        report = run_personal_ops_action_export(
            hours=2,
            limit=5,
            save_review_package=True,
            review_dir=tmp_path,
        )

    assert report["status"] == "ok"
    assert report["review_package"]["status"] == "ok"
    package_path = Path(str(report["review_package"]["path"]))
    assert package_path.exists()
    payload = json.loads(package_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "notification-hub.personal_ops_action_export.v1"


def test_validate_action_package_accepts_saved_review_package(tmp_path: Path) -> None:
    package_path = tmp_path / "actions.json"
    package_path.write_text(
        json.dumps(
            {
                "schema_version": "notification-hub.personal_ops_action_export.v1",
                "actions": [
                    {
                        "action_id": "notification-hub:personal-ops:mail:waiting_on_user:approval-requested",
                        "source": "personal-ops",
                        "project": "mail",
                        "intent": "waiting_on_user",
                        "priority": "high",
                        "state": "waiting",
                        "title": "Approval Requested",
                        "summary": "2 repeated personal-ops events",
                        "suggested_next_action": "Review the waiting item.",
                        "evidence_event_id": "abc123",
                        "evidence_timestamp": "2026-05-09T00:00:00+00:00",
                        "count": 2,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = validate_action_package(package_path)

    assert report["status"] == "ok"
    assert report["action_count"] == 1
    assert report["valid_action_count"] == 1
    assert report["error_count"] == 0


def test_list_action_review_packages_reports_recent_valid_packages(tmp_path: Path) -> None:
    older_package = tmp_path / "personal-ops-actions-20260509-100000.json"
    newer_package = tmp_path / "personal-ops-actions-20260509-100100.json"
    payload = {
        "schema_version": "notification-hub.personal_ops_action_export.v1",
        "actions": [
            {
                "action_id": "notification-hub:personal-ops:mail:waiting_on_user:approval-requested",
                "source": "personal-ops",
                "project": "mail",
                "intent": "waiting_on_user",
                "priority": "high",
                "state": "waiting",
                "title": "Approval Requested",
                "summary": "2 repeated personal-ops events",
                "suggested_next_action": "Review the waiting item.",
                "evidence_event_id": "abc123",
                "evidence_timestamp": "2026-05-09T00:00:00+00:00",
                "count": 2,
            }
        ],
    }
    older_package.write_text(json.dumps(payload), encoding="utf-8")
    newer_package.write_text(json.dumps(payload), encoding="utf-8")
    older_time = 1_700_000_000
    newer_time = 1_700_000_100
    os.utime(older_package, (older_time, older_time))
    os.utime(newer_package, (newer_time, newer_time))

    packages = list_action_review_packages(review_dir=tmp_path, limit=1)

    assert len(packages) == 1
    assert packages[0]["name"] == newer_package.name
    assert packages[0]["validation_status"] == "ok"
    assert packages[0]["valid_action_count"] == 1


def test_load_action_review_package_detail_returns_actions(tmp_path: Path) -> None:
    package_name = "personal-ops-actions-20260509-100000.json"
    package_path = tmp_path / package_name
    package_path.write_text(
        json.dumps(
            {
                "schema_version": "notification-hub.personal_ops_action_export.v1",
                "generated_at": "2026-05-09T10:00:00+00:00",
                "hours": 2,
                "actions": [
                    {
                        "action_id": "notification-hub:personal-ops:mail:waiting_on_user:approval-requested",
                        "source": "personal-ops",
                        "project": "mail",
                        "intent": "waiting_on_user",
                        "priority": "high",
                        "state": "waiting",
                        "title": "Approval Requested",
                        "summary": "2 repeated personal-ops events",
                        "suggested_next_action": "Review the waiting item.",
                        "evidence_event_id": "abc123",
                        "evidence_timestamp": "2026-05-09T00:00:00+00:00",
                        "count": 2,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    queue_path = tmp_path / "queue.jsonl"
    queue_path.write_text(
        json.dumps(
            {
                "schema_version": "notification-hub.personal_ops_import_queue.v1",
                "queue_id": "queue123",
                "status": "promoted",
                "enqueued_at": "2026-05-09T10:05:00+00:00",
                "updated_at": "2026-05-09T10:10:00+00:00",
                "source_package_path": str(package_path),
                "source_package_name": package_name,
                "action_id": "notification-hub:personal-ops:mail:waiting_on_user:approval-requested",
                "action": {
                    "title": "Approval Requested",
                    "summary": "2 repeated personal-ops events",
                    "priority": "high",
                    "state": "waiting",
                    "evidence_event_id": "abc123",
                },
                "applied": True,
                "promotion_outcome": "accepted",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    detail = load_action_review_package_detail(
        name=package_name, review_dir=tmp_path, queue_path=queue_path
    )

    assert detail["status"] == "ok"
    assert detail["applied"] is False
    assert detail["generated_at"] == "2026-05-09T10:00:00+00:00"
    assert detail["hours"] == 2
    assert detail["validation"]["valid_action_count"] == 1
    assert detail["actions"][0]["evidence_event_id"] == "abc123"
    assert detail["queue_items"][0]["queue_id"] == "queue123"
    assert detail["queue_items"][0]["promotion_outcome"] == "accepted"


def test_load_action_review_package_detail_rejects_unsafe_name(tmp_path: Path) -> None:
    detail = load_action_review_package_detail(name="../events.jsonl", review_dir=tmp_path)

    assert detail["status"] == "degraded"
    assert detail["applied"] is False
    assert detail["actions"] == []
    assert detail["validation"]["errors"] == ["invalid review package name"]


def test_delete_action_review_package_removes_safe_package(tmp_path: Path) -> None:
    package_name = "personal-ops-actions-20260509-100000.json"
    package_path = tmp_path / package_name
    package_path.write_text("{}", encoding="utf-8")

    report = delete_action_review_package(name=package_name, review_dir=tmp_path)

    assert report["status"] == "ok"
    assert report["deleted"] is True
    assert report["applied"] is False
    assert not package_path.exists()


def test_delete_action_review_package_rejects_unsafe_name(tmp_path: Path) -> None:
    report = delete_action_review_package(name="../events.jsonl", review_dir=tmp_path)

    assert report["status"] == "degraded"
    assert report["deleted"] is False
    assert report["applied"] is False
    assert report["error"] == "invalid review package name"


def test_validate_action_package_rejects_invalid_action(tmp_path: Path) -> None:
    package_path = tmp_path / "actions.json"
    package_path.write_text(
        json.dumps(
            {
                "schema_version": "notification-hub.personal_ops_action_export.v1",
                "actions": [{"action_id": "bad", "priority": "maybe"}],
            }
        ),
        encoding="utf-8",
    )

    report = validate_action_package(package_path)

    assert report["status"] == "degraded"
    assert report["action_count"] == 1
    assert report["valid_action_count"] == 0
    assert report["error_count"] > 0


def test_personal_ops_import_stub_validates_without_applying(tmp_path: Path) -> None:
    package_path = tmp_path / "actions.json"
    package_path.write_text(
        json.dumps(
            {
                "schema_version": "notification-hub.personal_ops_action_export.v1",
                "actions": [
                    {
                        "action_id": "notification-hub:personal-ops:mail:waiting_on_user:approval-requested",
                        "source": "personal-ops",
                        "project": "mail",
                        "intent": "waiting_on_user",
                        "priority": "high",
                        "state": "waiting",
                        "title": "Approval Requested",
                        "summary": "2 repeated personal-ops events",
                        "suggested_next_action": "Review the waiting item.",
                        "evidence_event_id": "abc123",
                        "evidence_timestamp": "2026-05-09T00:00:00+00:00",
                        "count": 2,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = run_personal_ops_import_stub(path=package_path)

    assert report["status"] == "ok"
    assert report["applied"] is False
    assert report["enqueued"] is False
    assert report["queued_count"] == 0
    assert report["validation"]["valid_action_count"] == 1


def test_personal_ops_import_can_enqueue_valid_package(tmp_path: Path) -> None:
    package_path = tmp_path / "actions.json"
    queue_path = tmp_path / "queue.jsonl"
    package_path.write_text(
        json.dumps(
            {
                "schema_version": "notification-hub.personal_ops_action_export.v1",
                "actions": [
                    {
                        "action_id": "notification-hub:personal-ops:mail:waiting_on_user:approval-requested",
                        "source": "personal-ops",
                        "project": "mail",
                        "intent": "waiting_on_user",
                        "priority": "high",
                        "state": "waiting",
                        "title": "Approval Requested",
                        "summary": "2 repeated personal-ops events",
                        "suggested_next_action": "Review the waiting item.",
                        "evidence_event_id": "abc123",
                        "evidence_timestamp": "2026-05-09T00:00:00+00:00",
                        "count": 2,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = run_personal_ops_import_stub(path=package_path, enqueue=True, queue_path=queue_path)
    duplicate_report = run_personal_ops_import_stub(
        path=package_path, enqueue=True, queue_path=queue_path
    )
    queue_items = list_personal_ops_import_queue(queue_path=queue_path)

    assert report["status"] == "ok"
    assert report["applied"] is False
    assert report["enqueued"] is True
    assert report["queued_count"] == 1
    assert duplicate_report["queued_count"] == 0
    assert duplicate_report["skipped_count"] == 1
    assert len(queue_items) == 1
    assert queue_items[0]["title"] == "Approval Requested"
    assert queue_items[0]["applied"] is False
    assert queue_items[0]["source_package_path"] == str(package_path)


def test_personal_ops_import_queue_lifecycle_and_health(tmp_path: Path) -> None:
    package_path = tmp_path / "actions.json"
    queue_path = tmp_path / "queue.jsonl"
    package_path.write_text(
        json.dumps(
            {
                "schema_version": "notification-hub.personal_ops_action_export.v1",
                "actions": [
                    {
                        "action_id": "notification-hub:personal-ops:mail:waiting_on_user:approval-requested",
                        "source": "personal-ops",
                        "project": "mail",
                        "intent": "waiting_on_user",
                        "priority": "high",
                        "state": "waiting",
                        "title": "Approval Requested",
                        "summary": "2 repeated personal-ops events",
                        "suggested_next_action": "Review the waiting item.",
                        "evidence_event_id": "abc123",
                        "evidence_timestamp": "2026-05-09T00:00:00+00:00",
                        "count": 2,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    run_personal_ops_import_stub(path=package_path, enqueue=True, queue_path=queue_path)
    queue_id = list_personal_ops_import_queue(queue_path=queue_path)[0]["queue_id"]

    health_before = summarize_personal_ops_import_queue(queue_path=queue_path)
    reviewed = update_personal_ops_import_queue_item(
        queue_id=queue_id,
        status="reviewed",
        reason="operator checked evidence",
        queue_path=queue_path,
    )
    promoted = update_personal_ops_import_queue_item(
        queue_id=queue_id,
        status="promoted",
        reason="created personal-ops task suggestion",
        promotion_target="personal-ops task suggestion",
        promotion_target_id="suggestion-1",
        promotion_outcome="pending",
        queue_path=queue_path,
    )
    accepted = update_personal_ops_import_queue_item(
        queue_id=queue_id,
        status="promoted",
        reason="accepted in personal-ops",
        promotion_target="personal-ops task suggestion",
        promotion_target_id="suggestion-1",
        promotion_outcome="accepted",
        promotion_outcome_note="operator accepted the suggestion",
        queue_path=queue_path,
    )
    health_after = summarize_personal_ops_import_queue(queue_path=queue_path)

    assert health_before["queued_count"] == 1
    assert health_before["needs_review"] is True
    assert reviewed["status"] == "ok"
    assert reviewed["item"] is not None
    assert reviewed["item"]["status"] == "reviewed"
    assert reviewed["item"]["outcome_reason"] == "operator checked evidence"
    assert promoted["status"] == "ok"
    assert promoted["item"] is not None
    assert promoted["item"]["status"] == "promoted"
    assert promoted["item"]["applied"] is True
    assert promoted["item"]["promotion_target"] == "personal-ops task suggestion"
    assert promoted["item"]["promotion_target_id"] == "suggestion-1"
    assert promoted["item"]["promotion_outcome"] == "pending"
    assert accepted["status"] == "ok"
    assert accepted["item"] is not None
    assert accepted["item"]["promotion_outcome"] == "accepted"
    assert accepted["item"]["promotion_outcome_note"] == "operator accepted the suggestion"
    assert health_after["queued_count"] == 0
    assert health_after["promoted_count"] == 1
    assert health_after["promoted_accepted_count"] == 1
    assert health_after["promoted_pending_count"] == 0
    assert health_after["needs_review"] is False


def test_personal_ops_import_queue_health_flags_stale_promotions(tmp_path: Path) -> None:
    queue_path = tmp_path / "queue.jsonl"
    old_timestamp = "2026-05-09T00:00:00+00:00"
    queue_path.write_text(
        json.dumps(
            {
                "schema_version": "notification-hub.personal_ops_import_queue.v1",
                "queue_id": "queue-stale",
                "status": "promoted",
                "enqueued_at": old_timestamp,
                "updated_at": old_timestamp,
                "promoted_at": old_timestamp,
                "promotion_target": "personal-ops task suggestion",
                "promotion_target_id": "suggestion-stale",
                "promotion_outcome": "pending",
                "promotion_outcome_at": old_timestamp,
                "source_package_path": "/tmp/actions.json",
                "source_package_name": "actions.json",
                "action_id": "action-stale",
                "applied": True,
                "action": {
                    "action_id": "action-stale",
                    "title": "Stale promoted handoff",
                    "summary": "Waiting on outcome sync.",
                    "priority": "high",
                    "state": "waiting",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    health = summarize_personal_ops_import_queue(queue_path=queue_path, stale_after_hours=0)
    report = ops_mod.run_personal_ops_import_queue_health_check(
        queue_path=queue_path, stale_after_hours=0
    )

    assert health["status"] == "warn"
    assert health["promoted_pending_count"] == 1
    assert health["promoted_pending_stale_count"] == 1
    assert health["needs_outcome_sync"] is True
    assert "record the promotion outcome" in health["next_action"]
    assert report["pending_promotion_items"][0]["queue_id"] == "queue-stale"
    assert (
        'personal-ops suggestion accept|reject SUGGESTION_ID --note "..."'
        in report["next_commands"]
    )


def test_personal_ops_outcome_sync_reminder_reports_pending_promotions(tmp_path: Path) -> None:
    queue_path = tmp_path / "queue.jsonl"
    old_timestamp = "2026-05-09T00:00:00+00:00"
    queue_path.write_text(
        json.dumps(
            {
                "schema_version": "notification-hub.personal_ops_import_queue.v1",
                "queue_id": "queue-pending",
                "status": "promoted",
                "enqueued_at": old_timestamp,
                "updated_at": old_timestamp,
                "promoted_at": old_timestamp,
                "promotion_target": "personal-ops task suggestion",
                "promotion_target_id": "suggestion-pending",
                "promotion_outcome": "pending",
                "promotion_outcome_at": old_timestamp,
                "source_package_path": "/tmp/actions.json",
                "source_package_name": "actions.json",
                "action_id": "action-pending",
                "applied": True,
                "action": {
                    "action_id": "action-pending",
                    "title": "Pending promoted handoff",
                    "summary": "Waiting on outcome sync.",
                    "priority": "high",
                    "state": "waiting",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = run_personal_ops_outcome_sync_reminder(
        queue_path=queue_path,
        stale_after_hours=0,
    )

    assert report["status"] == "warn"
    assert report["should_remind"] is True
    assert report["pending_count"] == 1
    assert report["stale_count"] == 1
    assert report["reminders"][0]["queue_id"] == "queue-pending"
    assert report["next_commands"] == [
        'personal-ops suggestion accept|reject SUGGESTION_ID --note "..."',
        "uv run notification-hub personal-ops-queue --queue-id QUEUE_ID "
        "--status promoted --promotion-target-id SUGGESTION_ID "
        '--promotion-outcome accepted|rejected --promotion-outcome-note "..."',
    ]
    assert report["applied"] is False


def test_personal_ops_queue_scenario_records_final_outcome() -> None:
    report = run_personal_ops_queue_scenario()

    assert report["status"] == "ok"
    assert report["queued_count"] == 1
    assert report["queue_id"] is not None
    assert report["promotion_outcome"] == "accepted"
    assert report["final_health"]["promoted_accepted_count"] == 1
    assert report["applied"] is True


def test_personal_ops_queue_burn_in_reports_operator_steps() -> None:
    with (
        patch(
            "notification_hub.operations.run_personal_ops_import_queue_health_check"
        ) as mock_health,
        patch("notification_hub.operations.run_personal_ops_queue_scenario") as mock_scenario,
        patch("notification_hub.operations.run_burn_in") as mock_burn_in,
    ):
        mock_health.return_value = {
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
                "oldest_queued_age_seconds": 60.0,
                "oldest_promoted_pending_at": None,
                "oldest_promoted_pending_age_seconds": None,
                "stale_after_hours": 4.0,
                "next_action": "Review queued personal-ops handoff items.",
            },
            "queued_items": [],
            "pending_promotion_items": [],
            "next_commands": ["uv run notification-hub personal-ops-queue"],
            "applied": False,
        }
        mock_scenario.return_value = {
            "status": "ok",
            "queue_path": "/tmp/scenario-queue.jsonl",
            "package_path": "/tmp/package.json",
            "queue_id": "queue123",
            "queued_count": 1,
            "review_status": "ok",
            "promotion_status": "ok",
            "promotion_outcome": "accepted",
            "final_health": mock_health.return_value["health"],
            "applied": True,
            "next_action": "Scenario passed; use the same lifecycle for real queued handoffs.",
            "error": None,
        }
        mock_burn_in.return_value = {
            "status": "ok",
            "minutes": 10,
            "events_seen": 0,
            "accepted_event_posts": 0,
            "rejected_event_posts": 0,
            "validation_error_count": 0,
            "health": {
                "accepted_event_posts": 0,
                "rejected_event_posts": 0,
                "validation_error_count": 0,
                "slack_delivery_failure_count": 0,
                "status": "ok",
            },
            "noise_candidates": [],
            "noise_rule_suggestions": [],
            "repeated_signatures": [],
            "slack_eligible_events": 0,
            "slack_volume": [],
            "daemon_summary": {
                "access_status_counts": {},
                "accepted_event_posts": 0,
                "rejected_event_posts": 0,
                "validation_error_count": 0,
                "recent_validation_errors": [],
                "slack_delivery_failure_count": 0,
                "recent_slack_delivery_failures": [],
            },
            "error": None,
        }

        report = run_personal_ops_queue_burn_in(minutes=5, lines=20, limit=3)

    assert report["status"] == "warn"
    assert report["ready_for_live_promotion"] is True
    assert "operator-mediated" in report["outcome_sync_posture"]
    assert "Promote one reviewed handoff" in report["next_action"]
    assert any("record the outcome" in step for step in report["operator_steps"])
    assert report["report_file"]["status"] == "not_requested"
    mock_health.assert_called_once_with(limit=3)
    mock_burn_in.assert_called_once_with(minutes=5, lines=20)


def test_personal_ops_queue_burn_in_can_save_report(tmp_path: Path) -> None:
    with (
        patch(
            "notification_hub.operations.run_personal_ops_import_queue_health_check"
        ) as mock_health,
        patch("notification_hub.operations.run_personal_ops_queue_scenario") as mock_scenario,
        patch("notification_hub.operations.run_burn_in") as mock_burn_in,
    ):
        mock_health.return_value = {
            "status": "ok",
            "health": summarize_personal_ops_import_queue(queue_path=tmp_path / "queue.jsonl"),
            "queued_items": [],
            "pending_promotion_items": [],
            "next_commands": ["uv run notification-hub personal-ops-queue-health"],
            "applied": False,
        }
        mock_scenario.return_value = {
            "status": "ok",
            "queue_path": "/tmp/scenario-queue.jsonl",
            "package_path": "/tmp/package.json",
            "queue_id": "queue123",
            "queued_count": 1,
            "review_status": "ok",
            "promotion_status": "ok",
            "promotion_outcome": "accepted",
            "final_health": mock_health.return_value["health"],
            "applied": True,
            "next_action": "Scenario passed; use the same lifecycle for real queued handoffs.",
            "error": None,
        }
        mock_burn_in.return_value = {
            "status": "ok",
            "minutes": 10,
            "events_seen": 0,
            "accepted_event_posts": 0,
            "rejected_event_posts": 0,
            "validation_error_count": 0,
            "health": {
                "accepted_event_posts": 0,
                "rejected_event_posts": 0,
                "validation_error_count": 0,
                "slack_delivery_failure_count": 0,
                "status": "ok",
            },
            "noise_candidates": [],
            "noise_rule_suggestions": [],
            "repeated_signatures": [],
            "slack_eligible_events": 0,
            "slack_volume": [],
            "daemon_summary": {
                "access_status_counts": {},
                "accepted_event_posts": 0,
                "rejected_event_posts": 0,
                "validation_error_count": 0,
                "recent_validation_errors": [],
                "slack_delivery_failure_count": 0,
                "recent_slack_delivery_failures": [],
            },
            "error": None,
        }

        report = run_personal_ops_queue_burn_in(save_report=True, report_dir=tmp_path / "reports")

    report_file = report["report_file"]
    assert report_file["status"] == "ok"
    report_path = Path(str(report_file["path"]))
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "notification-hub.personal_ops_queue_burn_in.v1"
    assert payload["report"]["ready_for_live_promotion"] is True


def test_operator_daily_state_can_save_resume_snapshot(tmp_path: Path) -> None:
    queue_check = {
        "status": "ok",
        "health": summarize_personal_ops_import_queue(queue_path=tmp_path / "queue.jsonl"),
        "queued_items": [],
        "pending_promotion_items": [],
        "next_commands": ["uv run notification-hub personal-ops-queue-health"],
        "applied": False,
    }
    console = {
        "status": "ok",
        "next_action": "Monitor /review for the next real handoff signal.",
        "next_signal": {
            "status": "monitor",
            "title": "Waiting for next real signal",
            "summary": "Waiting.",
            "qualifying_intents": ["waiting_on_user"],
            "hidden_action_count": 0,
            "dismissed_count": 0,
            "policy_covered_repeated_count": 0,
            "policy_covered_signatures": [],
            "dismissed_proposals": [],
            "next_action": "Monitor.",
        },
    }
    burn_in: dict[str, object] = {
        "status": "ok",
        "minutes": 60,
        "events_seen": 0,
        "accepted_event_posts": 0,
        "rejected_event_posts": 0,
        "validation_error_count": 0,
        "health": {"status": "ok"},
        "noise_candidates": [],
        "noise_rule_suggestions": [],
        "repeated_signatures": [],
        "slack_eligible_events": 0,
        "slack_volume": [],
        "daemon_summary": {},
        "error": None,
    }

    with (
        patch("notification_hub.operations.run_status", return_value=_coordination_status()),
        patch(
            "notification_hub.operations.run_personal_ops_import_queue_health_check",
            return_value=queue_check,
        ),
        patch("notification_hub.operations.run_coordination_console", return_value=console),
        patch("notification_hub.operations.run_burn_in", return_value=burn_in),
        patch("notification_hub.operations.list_action_proposal_dismissals", return_value=[]),
    ):
        report = run_operator_daily_state(
            hours=6,
            limit=3,
            save_report=True,
            report_dir=tmp_path / "state",
        )

    report_file = report["report_file"]
    assert report["status"] == "ok"
    assert report["applied"] is False
    assert report_file["status"] == "ok"
    payload = json.loads(Path(str(report_file["path"])).read_text(encoding="utf-8"))
    assert payload["schema_version"] == "notification-hub.operator_daily_state.v1"
    assert payload["report"]["next_action"] == "Monitor /review for the next real handoff signal."


def test_operator_handoff_drill_runs_temporary_lifecycle() -> None:
    scenario = {
        "status": "ok",
        "queue_path": "/tmp/scenario-queue.jsonl",
        "package_path": "/tmp/package.json",
        "queue_id": "queue123",
        "queued_count": 1,
        "review_status": "ok",
        "promotion_status": "ok",
        "promotion_outcome": "accepted",
        "final_health": summarize_personal_ops_import_queue(),
        "applied": True,
        "next_action": "Scenario passed; use the same lifecycle for real queued handoffs.",
        "error": None,
    }
    queue_burn_in: dict[str, object] = {
        "status": "ok",
        "ready_for_live_promotion": True,
        "scenario": scenario,
        "queue_health": {"health": summarize_personal_ops_import_queue()},
        "runtime_burn_in": {"health": {"status": "ok"}},
        "outcome_sync_posture": "operator-mediated",
        "operator_steps": [],
        "next_action": "Queue loop is ready.",
        "report_file": {"status": "not_requested"},
    }
    with (
        patch("notification_hub.operations.run_personal_ops_queue_scenario", return_value=scenario),
        patch(
            "notification_hub.operations.run_personal_ops_queue_burn_in",
            return_value=queue_burn_in,
        ) as mock_burn_in,
    ):
        report = run_operator_handoff_drill(save_burn_in_report=True)

    assert report["status"] == "ok"
    assert report["applied"] is False
    assert report["scenario"]["queue_id"] == "queue123"
    assert report["queue_burn_in"]["ready_for_live_promotion"] is True
    mock_burn_in.assert_called_once_with(save_report=True, report_dir=None)


def test_list_and_load_personal_ops_queue_burn_in_reports(tmp_path: Path) -> None:
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    report_path = report_dir / "personal-ops-queue-burn-in-20260510-040904.json"
    report_path.write_text(
        json.dumps(
            {
                "schema_version": "notification-hub.personal_ops_queue_burn_in.v1",
                "generated_at": "2026-05-10T04:09:04+00:00",
                "report": {
                    "status": "ok",
                    "ready_for_live_promotion": True,
                    "next_action": "Queue loop is ready.",
                    "queue_health": {
                        "health": {
                            "queued_count": 0,
                            "promoted_pending_count": 0,
                            "promoted_pending_stale_count": 0,
                        }
                    },
                    "runtime_burn_in": {
                        "health": {"status": "ok"},
                        "noise_candidates": [],
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    reports = list_personal_ops_queue_burn_in_reports(report_dir=report_dir)
    detail = load_personal_ops_queue_burn_in_report_detail(
        name=report_path.name, report_dir=report_dir
    )

    assert reports[0]["name"] == report_path.name
    assert reports[0]["ready_for_live_promotion"] is True
    assert reports[0]["runtime_status"] == "ok"
    assert detail["status"] == "ok"
    assert detail["summary"] is not None
    assert detail["summary"]["next_action"] == "Queue loop is ready."


def test_load_personal_ops_queue_burn_in_report_rejects_unsafe_name(tmp_path: Path) -> None:
    detail = load_personal_ops_queue_burn_in_report_detail(
        name="../events.jsonl", report_dir=tmp_path
    )

    assert detail["status"] == "degraded"
    assert detail["error"] == "invalid burn-in report name"


def test_personal_ops_import_queue_snooze_requires_until(tmp_path: Path) -> None:
    report = update_personal_ops_import_queue_item(
        queue_id="missing",
        status="snoozed",
        queue_path=tmp_path / "queue.jsonl",
    )

    assert report["status"] == "degraded"
    assert report["updated"] is False
    assert "snoozed_until" in str(report["error"])


def test_personal_ops_import_stub_rejects_invalid_package(tmp_path: Path) -> None:
    package_path = tmp_path / "actions.json"
    package_path.write_text(json.dumps({"actions": [{"action_id": "bad"}]}), encoding="utf-8")

    report = run_personal_ops_import_stub(path=package_path)

    assert report["status"] == "degraded"
    assert report["applied"] is False
    assert report["error"] == "package validation failed"


def test_coordination_snapshot_can_save_to_bridge_db(tmp_path: Path) -> None:
    db_path = tmp_path / "bridge.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE system_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                system TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            );
            CREATE VIRTUAL TABLE content_index USING fts5(
                source_type UNINDEXED,
                source_id UNINDEXED,
                text
            );
            """
        )

    inbox_report: dict[str, object] = {
        "status": "ok",
        "hours": 2,
        "events_seen": 0,
        "needs_attention": [],
        "waiting_or_blocked": [],
        "ready": [],
        "completed": [],
        "rollups": [],
        "noise_candidates": [],
        "error": None,
    }
    status_report = {
        "status": "ok",
        "health_url": "http://127.0.0.1:9199/health/details",
        "daemon_reachable": True,
        "watcher_active": True,
        "events_processed": 10,
        "uptime_seconds": 123.4,
        "policy_config_found": True,
        "policy_warning_count": 0,
        "retention_enabled": True,
        "retention_last_status": "ok",
        "runtime_wiring_current": True,
        "push_notifier_available": True,
        "slack_configured": True,
        "slack_delivery_failures": 0,
        "next_action": "No action needed.",
    }

    with (
        patch("notification_hub.operations.run_inbox", return_value=inbox_report),
        patch("notification_hub.operations.run_status", return_value=status_report),
    ):
        report = run_coordination_snapshot(
            hours=2,
            limit=3,
            save_bridge_db=True,
            bridge_db_path=db_path,
        )

    assert report["status"] == "ok"
    assert report["bridge_save"]["status"] == "ok"
    assert report["bridge_save"]["snapshot_id"] == 1
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT system, snapshot_date, data FROM system_snapshots").fetchone()
    assert row is not None
    assert row[0] == "codex"
    assert row[1] == report["bridge_snapshot_date"]
    assert json.loads(row[2])["runtime"]["status"] == "ok"


def test_logs_report_tails_events_and_daemon_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events_dir = tmp_path / "notification-hub"
    events_dir.mkdir()
    events_log = events_dir / "events.jsonl"
    events = [
        StoredEvent(
            event_id=f"id-{i}",
            source="codex",
            level="info",
            title=f"title {i}",
            body=f"body {i}",
            project="notification-hub",
        )
        for i in range(3)
    ]
    events_log.write_text(
        "\n".join(event.model_dump_json() for event in events) + "\n", encoding="utf-8"
    )
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    stdout_log.write_text(
        "\n".join(
            [
                'INFO:     127.0.0.1:1 - "POST /events HTTP/1.1" 201 Created',
                'INFO:     127.0.0.1:2 - "POST /events HTTP/1.1" 422 Unprocessable Entity',
                'INFO:     127.0.0.1:3 - "GET /health/details HTTP/1.1" 200 OK',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    stderr_log.write_text(
        "\n".join(
            [
                "err 1",
                "Rejected event payload from 127.0.0.1: [{'type': 'literal_error'}]",
                "err 3",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(ops_mod, "EVENTS_LOG", events_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDOUT_LOG", stdout_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDERR_LOG", stderr_log)

    report = run_logs(events=2, lines=2)

    assert report["status"] == "ok"
    assert [event["event_id"] for event in report["recent_events"]] == ["id-1", "id-2"]
    assert report["daemon_summary"]["accepted_event_posts"] == 0
    assert report["daemon_summary"]["rejected_event_posts"] == 1
    assert report["daemon_summary"]["validation_error_count"] == 1
    assert report["daemon_summary"]["slack_delivery_failure_count"] == 0
    assert report["stdout_tail"] == [
        'INFO:     127.0.0.1:2 - "POST /events HTTP/1.1" 422 Unprocessable Entity',
        'INFO:     127.0.0.1:3 - "GET /health/details HTTP/1.1" 200 OK',
    ]
    assert report["stderr_tail"] == [
        "Rejected event payload from 127.0.0.1: [{'type': 'literal_error'}]",
        "err 3",
    ]
    assert report["missing_paths"] == []


def test_logs_report_degrades_on_invalid_event_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events_log = tmp_path / "events.jsonl"
    events_log.write_text("{not-json}\n", encoding="utf-8")
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    stdout_log.write_text("", encoding="utf-8")
    stderr_log.write_text("", encoding="utf-8")

    monkeypatch.setattr(ops_mod, "EVENTS_LOG", events_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDOUT_LOG", stdout_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDERR_LOG", stderr_log)

    report = run_logs()

    assert report["status"] == "degraded"
    assert report["recent_events"] == []
    assert report["error"] is not None


def test_logs_report_counts_validation_errors_since_latest_daemon_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events_log = tmp_path / "events.jsonl"
    events_log.write_text("", encoding="utf-8")
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    stdout_log.write_text("", encoding="utf-8")
    stderr_log.write_text(
        "\n".join(
            [
                "Rejected event payload from 127.0.0.1: [{'type': 'old_error'}]",
                "INFO:     Started server process [123]",
                "INFO:     Waiting for application startup.",
                "INFO:     Application startup complete.",
                "INFO:     Uvicorn running on http://127.0.0.1:9199 (Press CTRL+C to quit)",
                "Rejected event payload from 127.0.0.1: [{'type': 'current_error'}]",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(ops_mod, "EVENTS_LOG", events_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDOUT_LOG", stdout_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDERR_LOG", stderr_log)

    report = run_logs(events=0, lines=20)

    assert report["daemon_summary"]["validation_error_count"] == 1
    assert report["daemon_summary"]["recent_validation_errors"] == [
        "Rejected event payload from 127.0.0.1: [{'type': 'current_error'}]"
    ]
    assert report["daemon_summary"]["slack_delivery_failure_count"] == 0


def test_logs_report_counts_slack_delivery_failures_since_latest_daemon_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events_log = tmp_path / "events.jsonl"
    events_log.write_text("", encoding="utf-8")
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    stdout_log.write_text("", encoding="utf-8")
    stderr_log.write_text(
        "\n".join(
            [
                "Slack send failed for old: [Errno 2] No such file or directory",
                "INFO:     Started server process [123]",
                "INFO:     Waiting for application startup.",
                "INFO:     Application startup complete.",
                "Slack digest failed: [Errno 2] No such file or directory",
                "Failed to flush overflow digest; returning 11 events to buffer",
                "Slack send failed for current: [Errno 2] No such file or directory",
                "Slack delivery failed for current",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(ops_mod, "EVENTS_LOG", events_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDOUT_LOG", stdout_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDERR_LOG", stderr_log)

    report = run_logs(events=0, lines=20)

    assert report["daemon_summary"]["slack_delivery_failure_count"] == 2
    assert report["daemon_summary"]["recent_slack_delivery_failures"] == [
        "Slack digest failed: [Errno 2] No such file or directory",
        "Slack send failed for current: [Errno 2] No such file or directory",
    ]


def test_logs_report_handles_zero_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events_log = tmp_path / "events.jsonl"
    events_log.write_text(
        StoredEvent(source="codex", level="info", title="title", body="body").model_dump_json()
        + "\n",
        encoding="utf-8",
    )
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    stdout_log.write_text("out\n", encoding="utf-8")
    stderr_log.write_text("err\n", encoding="utf-8")

    monkeypatch.setattr(ops_mod, "EVENTS_LOG", events_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDOUT_LOG", stdout_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDERR_LOG", stderr_log)

    report = run_logs(events=0, lines=0)

    assert report["status"] == "ok"
    assert report["recent_events"] == []
    assert report["stdout_tail"] == []
    assert report["stderr_tail"] == []


def test_burn_in_reports_repeated_signatures_and_daemon_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events_log = tmp_path / "events.jsonl"
    events = [
        StoredEvent(
            source="personal-ops",
            level="info",
            classified_level="info",
            title="Approval expires soon",
            body="Approval expires soon: review or cancel",
            project="personal-ops",
        ),
        StoredEvent(
            source="personal-ops",
            level="info",
            classified_level="info",
            title="Approval expires soon",
            body="Approval expires soon: review or cancel",
            project="personal-ops",
        ),
        StoredEvent(
            source="codex",
            level="normal",
            classified_level="normal",
            title="Codex finished a turn",
            body="A Codex turn completed.",
            project="notification-hub",
        ),
    ]
    events_log.write_text(
        "\n".join(event.model_dump_json() for event in events) + "\n", encoding="utf-8"
    )
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    stdout_log.write_text(
        "\n".join(
            [
                'INFO:     127.0.0.1:1 - "POST /events HTTP/1.1" 201 Created',
                'INFO:     127.0.0.1:2 - "POST /events HTTP/1.1" 422 Unprocessable Entity',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    stderr_log.write_text(
        "Rejected event payload from 127.0.0.1: [{'type': 'literal_error'}]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ops_mod, "EVENTS_LOG", events_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDOUT_LOG", stdout_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDERR_LOG", stderr_log)
    monkeypatch.setattr(
        ops_mod,
        "get_policy_config",
        lambda: PolicyConfig(noise=NoisePolicy(rules=())),
    )

    report = run_burn_in(minutes=10, lines=10)

    assert report["status"] == "ok"
    assert report["events_seen"] == 3
    assert report["accepted_event_posts"] == 1
    assert report["rejected_event_posts"] == 1
    assert report["validation_error_count"] == 1
    assert report["health"] == {
        "accepted_event_posts": 1,
        "rejected_event_posts": 1,
        "validation_error_count": 1,
        "slack_delivery_failure_count": 0,
        "status": "degraded",
    }
    assert report["noise_candidates"] == report["repeated_signatures"]
    assert report["noise_rule_suggestions"] == [
        "Review noise rule candidate: source='personal-ops', project='personal-ops', title_contains='Approval expires soon', level='info', window_minutes=10"
    ]
    assert report["repeated_signatures"][0]["count"] == 2
    assert report["repeated_signatures"][0]["source"] == "personal-ops"
    assert report["slack_eligible_events"] == 1
    assert report["slack_volume"] == [
        {
            "count": 1,
            "source": "codex",
            "level": "normal",
        }
    ]


def test_burn_in_filters_policy_covered_noise_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events_log = tmp_path / "events.jsonl"
    events = [
        StoredEvent(
            source="codex",
            level="normal",
            classified_level="normal",
            title="Codex finished a turn",
            body="A Codex turn completed.",
            project="personal-ops",
        ),
        StoredEvent(
            source="codex",
            level="normal",
            classified_level="normal",
            title="Codex finished a turn",
            body="A Codex turn completed.",
            project="personal-ops",
        ),
    ]
    events_log.write_text(
        "\n".join(event.model_dump_json() for event in events) + "\n", encoding="utf-8"
    )
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    stdout_log.write_text("", encoding="utf-8")
    stderr_log.write_text("", encoding="utf-8")
    monkeypatch.setattr(ops_mod, "EVENTS_LOG", events_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDOUT_LOG", stdout_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDERR_LOG", stderr_log)
    monkeypatch.setattr(
        ops_mod,
        "get_policy_config",
        lambda: PolicyConfig(
            noise=NoisePolicy(
                rules=(
                    NoiseRule(
                        source="codex",
                        title_contains="codex finished a turn",
                        body_contains="a codex turn completed.",
                        level="normal",
                    ),
                )
            )
        ),
    )

    report = run_burn_in(minutes=10, lines=10)

    assert report["noise_candidates"] == []
    assert report["noise_rule_suggestions"] == []
    assert report["repeated_signatures"][0]["title"] == "Codex finished a turn"


def test_burn_in_degrades_on_slack_delivery_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events_log = tmp_path / "events.jsonl"
    events_log.write_text("", encoding="utf-8")
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    stdout_log.write_text(
        'INFO:     127.0.0.1:1 - "POST /events HTTP/1.1" 201 Created\n',
        encoding="utf-8",
    )
    stderr_log.write_text(
        "Slack send failed for abc123: [Errno 2] No such file or directory\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(ops_mod, "EVENTS_LOG", events_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDOUT_LOG", stdout_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDERR_LOG", stderr_log)

    report = run_burn_in(minutes=10, lines=20)

    assert report["status"] == "ok"
    assert report["health"] == {
        "accepted_event_posts": 1,
        "rejected_event_posts": 0,
        "validation_error_count": 0,
        "slack_delivery_failure_count": 1,
        "status": "degraded",
    }


def test_retention_noop_when_log_missing() -> None:
    report = run_retention(max_events=10, keep_archives=2)

    assert report["status"] == "ok"
    assert report["rotated"] is False
    assert report["events_before"] == 0


def test_retention_archives_older_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events_dir = tmp_path / "notification-hub"
    events_dir.mkdir()
    events_log = events_dir / "events.jsonl"
    lines = [
        json.dumps(
            {"event_id": f"id-{i}", "source": "codex", "level": "info", "title": "t", "body": "b"}
        )
        + "\n"
        for i in range(5)
    ]
    events_log.write_text("".join(lines), encoding="utf-8")

    monkeypatch.setattr(ops_mod, "EVENTS_DIR", events_dir)
    monkeypatch.setattr(ops_mod, "EVENTS_LOG", events_log)

    report = run_retention(max_events=2, keep_archives=5)

    assert report["rotated"] is True
    assert report["events_before"] == 5
    assert report["events_after"] == 2
    assert report["archived_events"] == 3
    archive_path = report["archive_path"]
    assert isinstance(archive_path, str)
    assert Path(archive_path).exists()
    assert len(events_log.read_text(encoding="utf-8").splitlines()) == 2


def test_bootstrap_policy_config_copies_example(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    example_path = tmp_path / "policy.example.toml"
    config_path = tmp_path / "config" / "config.toml"
    example_path.write_text('[classifier]\nurgent_keywords = ["database down"]\n', encoding="utf-8")

    monkeypatch.setattr(ops_mod, "EXAMPLE_POLICY_CONFIG", example_path)
    monkeypatch.setattr(ops_mod, "POLICY_CONFIG", config_path)

    report = bootstrap_policy_config()

    assert report["status"] == "ok"
    assert report["copied"] is True
    assert config_path.read_text(encoding="utf-8") == example_path.read_text(encoding="utf-8")
    assert oct(config_path.stat().st_mode & 0o777) == "0o600"


def test_bootstrap_policy_config_noop_without_force(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    example_path = tmp_path / "policy.example.toml"
    config_path = tmp_path / "config" / "config.toml"
    config_path.parent.mkdir(parents=True)
    example_path.write_text('[classifier]\nurgent_keywords = ["database down"]\n', encoding="utf-8")
    config_path.write_text('[classifier]\nurgent_keywords = ["keep me"]\n', encoding="utf-8")

    monkeypatch.setattr(ops_mod, "EXAMPLE_POLICY_CONFIG", example_path)
    monkeypatch.setattr(ops_mod, "POLICY_CONFIG", config_path)

    report = bootstrap_policy_config()

    assert report["status"] == "ok"
    assert report["copied"] is False
    assert "keep me" in config_path.read_text(encoding="utf-8")


def test_policy_check_reports_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = PolicyConfig(
        config_found=True,
        classification=ClassificationPolicy(
            urgent_keywords=("ship it",),
            normal_keywords=("ship it",),
            info_keywords=(),
        ),
        suppression=SuppressionPolicy(),
        routing=RoutingPolicy(rules=(RoutingRule(source="codex"),)),
    )

    monkeypatch.setattr(ops_mod, "get_policy_config", lambda: policy)

    def _warnings_for_policy(_policy: PolicyConfig) -> tuple[str, ...]:
        return ("warning one", "warning two")

    monkeypatch.setattr(ops_mod, "analyze_policy_config", _warnings_for_policy)

    report = run_policy_check()

    assert report["status"] == "warn"
    assert report["warning_count"] == 2
    assert report["suggestion_count"] == 2
    assert report["warnings"] == ["warning one", "warning two"]
    assert len(report["suggestions"]) == 2
    assert all(isinstance(suggestion, str) and suggestion for suggestion in report["suggestions"])


def test_policy_check_reports_degraded_on_load_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = PolicyConfig(
        config_found=True,
        load_error="bad toml",
        classification=ClassificationPolicy(),
        suppression=SuppressionPolicy(),
        routing=RoutingPolicy(),
    )

    monkeypatch.setattr(ops_mod, "get_policy_config", lambda: policy)

    def _no_warnings(_policy: PolicyConfig) -> tuple[str, ...]:
        return ()

    monkeypatch.setattr(ops_mod, "analyze_policy_config", _no_warnings)

    report = run_policy_check()

    assert report["status"] == "degraded"
    assert report["load_error"] == "bad toml"
    assert report["suggestion_count"] == 0
    assert report["suggestions"] == []


def test_policy_check_maps_shadowed_rule_to_fix_suggestion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = PolicyConfig(
        config_found=True,
        classification=ClassificationPolicy(),
        suppression=SuppressionPolicy(),
        routing=RoutingPolicy(
            rules=(
                RoutingRule(source="codex"),
                RoutingRule(source="codex", project="notification-hub", disable_slack=True),
            )
        ),
    )

    monkeypatch.setattr(ops_mod, "get_policy_config", lambda: policy)

    report = run_policy_check()

    assert report["status"] == "warn"
    assert any(
        "Move the narrower rule earlier" in suggestion for suggestion in report["suggestions"]
    )


def test_policy_check_maps_shared_priority_warning_to_fix_suggestion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = PolicyConfig(
        config_found=True,
        classification=ClassificationPolicy(),
        suppression=SuppressionPolicy(),
        routing=RoutingPolicy(
            rules=(
                RoutingRule(project="notification-hub", disable_slack=True, priority=10),
                RoutingRule(project_prefix="notification-", force_level="normal", priority=10),
            )
        ),
    )

    monkeypatch.setattr(ops_mod, "get_policy_config", lambda: policy)

    report = run_policy_check()

    assert report["status"] == "warn"
    assert any(
        "Give the more important rule a higher `priority`" in suggestion
        for suggestion in report["suggestions"]
    )


def test_policy_check_maps_retention_and_continue_warnings_to_fix_suggestions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = PolicyConfig(
        config_found=True,
        classification=ClassificationPolicy(),
        suppression=SuppressionPolicy(),
        retention=RetentionPolicy(enabled=False),
        routing=RoutingPolicy(
            rules=(
                RoutingRule(
                    project="notification-hub",
                    disable_slack=True,
                    continue_matching=True,
                ),
            )
        ),
    )

    monkeypatch.setattr(ops_mod, "get_policy_config", lambda: policy)

    report = run_policy_check()

    assert report["status"] == "warn"
    assert any("Re-enable retention" in suggestion for suggestion in report["suggestions"])
    assert any(
        "Remove `continue_matching` on the final rule" in suggestion
        for suggestion in report["suggestions"]
    )


def test_policy_check_maps_redundant_continue_chain_warning_to_fix_suggestion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = PolicyConfig(
        config_found=True,
        classification=ClassificationPolicy(),
        suppression=SuppressionPolicy(),
        routing=RoutingPolicy(
            rules=(
                RoutingRule(
                    project_prefix="notification-",
                    disable_slack=True,
                    continue_matching=True,
                ),
                RoutingRule(
                    project="notification-hub",
                    disable_slack=True,
                ),
            )
        ),
    )

    monkeypatch.setattr(ops_mod, "get_policy_config", lambda: policy)

    report = run_policy_check()

    assert report["status"] == "warn"
    assert any("Delete the redundant rule" in suggestion for suggestion in report["suggestions"])
