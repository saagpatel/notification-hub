"""Tests for operator review-session reports and retention."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from notification_hub.operations import (
    list_operator_review_session_reports,
    load_operator_review_session_report_detail,
    prune_operator_review_session_reports,
    run_operator_review_session,
)


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
    assert report["report_file"]["status"] == "not_requested"


def test_operator_review_session_can_save_report(tmp_path: Path) -> None:
    report = run_operator_review_session(
        hours=2,
        limit=10,
        queue_path=tmp_path / "missing-queue.jsonl",
        group_history_path=tmp_path / "missing-history.jsonl",
        save_report=True,
        report_dir=tmp_path / "reports",
    )

    assert report["status"] == "ok"
    report_file = report["report_file"]
    assert report_file["status"] == "ok"
    report_path = Path(str(report_file["path"]))
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "notification-hub.operator_review_session.v1"
    assert payload["report"]["applied"] is False
    assert payload["report"]["group_history_count"] == 0


def test_list_and_load_operator_review_session_reports(tmp_path: Path) -> None:
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    report_path = report_dir / "operator-review-session-20260510-091508.json"
    payload: dict[str, object] = {
        "schema_version": "notification-hub.operator_review_session.v1",
        "generated_at": "2026-05-10T09:15:08+00:00",
        "report": {
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
            "group_summaries": [],
            "recent_group_history": [],
            "recent_queue_items": [],
            "next_action": "Recent review activity is summarized.",
            "applied": False,
        },
    }
    report_path.write_text(json.dumps(payload), encoding="utf-8")

    reports = list_operator_review_session_reports(report_dir=report_dir)
    detail = load_operator_review_session_report_detail(
        name=report_path.name, report_dir=report_dir
    )
    invalid = load_operator_review_session_report_detail(
        name="../events.jsonl", report_dir=tmp_path
    )

    assert reports[0]["name"] == report_path.name
    assert reports[0]["group_history_count"] == 3
    assert reports[0]["reviewed_count"] == 3
    assert detail["status"] == "ok"
    assert detail["schema_version"] == "notification-hub.operator_review_session.v1"
    assert detail["summary"] is not None
    assert detail["summary"]["queued_count"] == 2
    assert detail["report"] is not None
    assert detail["report"]["applied"] is False
    assert invalid["status"] == "degraded"
    assert invalid["error"] == "invalid review-session report name"


def test_prune_operator_review_session_reports_keeps_newest(tmp_path: Path) -> None:
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    payload: dict[str, object] = {
        "schema_version": "notification-hub.operator_review_session.v1",
        "generated_at": "2026-05-10T09:15:08+00:00",
        "report": {
            "status": "ok",
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
            "applied": False,
        },
    }
    paths = [
        report_dir / "operator-review-session-20260510-091508.json",
        report_dir / "operator-review-session-20260510-091608.json",
        report_dir / "operator-review-session-20260510-091708.json",
    ]
    for index, path in enumerate(paths):
        path.write_text(json.dumps(payload), encoding="utf-8")
        os.utime(path, (100 + index, 100 + index))

    dry_run = prune_operator_review_session_reports(report_dir=report_dir, keep=2)

    assert dry_run["status"] == "ok"
    assert dry_run["dry_run"] is True
    assert dry_run["applied"] is False
    assert dry_run["candidate_count"] == 1
    assert dry_run["deleted_count"] == 0
    assert paths[0].exists()
    applied = prune_operator_review_session_reports(report_dir=report_dir, keep=2, dry_run=False)
    assert applied["status"] == "ok"
    assert applied["applied"] is True
    assert applied["candidate_count"] == 1
    assert applied["deleted_count"] == 1
    assert not paths[0].exists()
    assert paths[1].exists()
    assert paths[2].exists()
