"""Tests for smoke and retention operator actions."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

import notification_hub.operations as ops_mod
from notification_hub.config import (
    ClassificationPolicy,
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
    list_action_review_packages,
    list_personal_ops_import_queue,
    load_action_review_package_detail,
    run_burn_in,
    run_coordination_snapshot,
    run_inbox,
    run_logs,
    run_personal_ops_action_export,
    run_personal_ops_import_stub,
    run_personal_ops_queue_burn_in,
    run_personal_ops_queue_scenario,
    run_policy_check,
    run_retention,
    run_smoke_check,
    summarize_personal_ops_import_queue,
    update_personal_ops_import_queue_item,
    validate_action_package,
)


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

    with patch("notification_hub.operations.run_inbox", return_value=inbox_report):
        report = run_personal_ops_action_export(hours=2, limit=5)

    assert report["status"] == "ok"
    assert report["schema_version"] == "notification-hub.personal_ops_action_export.v1"
    assert report["actions"][0]["priority"] == "high"
    assert report["actions"][0]["state"] == "waiting"
    assert report["actions"][0]["action_id"].endswith(":abc123")
    assert report["actions"][0]["evidence_event_id"] == "abc123"
    assert report["actions"][0]["suggested_next_action"] == (
        "Review the waiting item and approve, reply, or dismiss it."
    )
    assert report["review_package"]["status"] == "not_requested"


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
    assert "sync-outcomes" in health["next_action"]
    assert report["pending_promotion_items"][0]["queue_id"] == "queue-stale"
    assert "personal-ops notification-hub sync-outcomes" in report["next_commands"]


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
    assert any("sync" in step for step in report["operator_steps"])
    mock_health.assert_called_once_with(limit=3)
    mock_burn_in.assert_called_once_with(minutes=5, lines=20)


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
