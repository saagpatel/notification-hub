"""Tests for the personal-ops handoff queue surfaces."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import notification_hub.operations as ops_mod
from notification_hub.operations import (
    list_personal_ops_import_queue,
    run_personal_ops_import_stub,
    run_personal_ops_outcome_sync_reminder,
    run_personal_ops_queue_burn_in,
    run_personal_ops_queue_review,
    run_personal_ops_queue_scenario,
    summarize_personal_ops_import_queue,
    update_personal_ops_import_queue_item,
)


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


def test_import_queue_item_report_surfaces_evidence_quality(tmp_path: Path) -> None:
    """Queue listing must surface evidence_quality so operators see rich vs thin."""
    package_path = tmp_path / "actions.json"
    queue_path = tmp_path / "queue.jsonl"
    package_path.write_text(
        json.dumps(
            {
                "schema_version": "notification-hub.personal_ops_action_export.v1",
                "actions": [
                    {
                        "action_id": "notification-hub:personal-ops:mail:waiting_on_user:rich",
                        "source": "personal-ops",
                        "project": "mail",
                        "intent": "waiting_on_user",
                        "priority": "high",
                        "state": "waiting",
                        "title": "Approval Requested",
                        "summary": "rich evidence proposal",
                        "suggested_next_action": "Review.",
                        "evidence_event_id": "rich-event-1",
                        "evidence_timestamp": "2026-05-11T00:00:00+00:00",
                        "evidence_context": {
                            "thread_id": "thread-real-1",
                            "draft_id": "draft-real-1",
                            "approval_id": "approval-real-1",
                            "mailbox": "real@example.com",
                        },
                        "evidence_quality": "rich",
                        "count": 2,
                    },
                    {
                        "action_id": "notification-hub:personal-ops:mail:waiting_on_user:thin",
                        "source": "personal-ops",
                        "project": "mail",
                        "intent": "waiting_on_user",
                        "priority": "high",
                        "state": "waiting",
                        "title": "Approval Requested",
                        "summary": "thin evidence proposal",
                        "suggested_next_action": "Review.",
                        "evidence_event_id": "thin-event-1",
                        "evidence_timestamp": "2026-05-11T00:00:00+00:00",
                        "evidence_context": {},
                        "evidence_quality": "thin",
                        "count": 2,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    run_personal_ops_import_stub(path=package_path, enqueue=True, queue_path=queue_path)

    items = list_personal_ops_import_queue(queue_path=queue_path)
    by_action = {item["action_id"]: item for item in items}
    rich = by_action["notification-hub:personal-ops:mail:waiting_on_user:rich"]
    thin = by_action["notification-hub:personal-ops:mail:waiting_on_user:thin"]

    assert rich["evidence_quality"] == "rich"
    assert thin["evidence_quality"] == "thin"


def test_personal_ops_queue_review_groups_queued_handoffs(tmp_path: Path) -> None:
    package_path = tmp_path / "actions.json"
    queue_path = tmp_path / "queue.jsonl"
    package_path.write_text(
        json.dumps(
            {
                "schema_version": "notification-hub.personal_ops_action_export.v1",
                "actions": [
                    {
                        "action_id": "action-1",
                        "source": "personal-ops",
                        "project": "mail",
                        "intent": "waiting_on_user",
                        "priority": "high",
                        "state": "waiting",
                        "title": "Approval Requested",
                        "summary": "Outbound workflow reply",
                        "suggested_next_action": "Review the waiting item.",
                        "evidence_event_id": "abc123",
                        "evidence_timestamp": "2026-05-09T00:00:00+00:00",
                        "count": 2,
                    },
                    {
                        "action_id": "action-2",
                        "source": "personal-ops",
                        "project": "mail",
                        "intent": "waiting_on_user",
                        "priority": "high",
                        "state": "waiting",
                        "title": "Approval Requested",
                        "summary": "Approval draft",
                        "suggested_next_action": "Review the waiting item.",
                        "evidence_event_id": "def456",
                        "evidence_timestamp": "2026-05-09T00:01:00+00:00",
                        "count": 2,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    run_personal_ops_import_stub(path=package_path, enqueue=True, queue_path=queue_path)

    report = run_personal_ops_queue_review(queue_path=queue_path)

    assert report["status"] == "warn"
    assert report["queued_count"] == 2
    assert report["operator_decision_count"] == 2
    assert report["batch_count"] == 1
    assert report["batches"][0]["item_count"] == 2
    assert report["batches"][0]["title"] == "Approval Requested"
    assert report["batches"][0]["first_queue_id"] in report["batches"][0]["queue_ids"]
    assert "personal-ops-queue --queue-id" in report["next_commands"][1]
    assert report["applied"] is False


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
    assert report["evidence_quality"] == "rich"
    assert report["rich_evidence_ready"] is True
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
