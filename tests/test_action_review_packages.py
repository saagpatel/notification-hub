"""Tests for saved action review packages and validation."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

from notification_hub.operations import (
    delete_action_review_package,
    list_action_review_packages,
    load_action_review_package_detail,
    run_personal_ops_action_export,
    validate_action_package,
)


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
                        "evidence_context": {
                            "thread_id": "thread-123",
                            "draft_id": "draft-456",
                        },
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


def test_validate_action_package_rejects_non_scalar_evidence_context(tmp_path: Path) -> None:
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
                        "evidence_context": {"thread_id": {"nested": "nope"}},
                        "count": 2,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = validate_action_package(package_path)

    assert report["status"] == "degraded"
    assert report["error_count"] == 1
    assert "evidence_context.thread_id must be a scalar value" in report["errors"][0]
