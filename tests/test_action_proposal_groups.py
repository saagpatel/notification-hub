"""Tests for action proposal group packages and outcomes."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

from notification_hub.operations import (
    dismiss_action_proposal_group,
    list_action_proposal_group_history,
    prune_action_export_files,
    record_action_proposal_group_outcome,
    save_action_proposal_group_package,
)


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
                "latest_context": {
                    "thread_id": "thread-abc123",
                    "draft_id": "draft-abc123",
                    "approval_id": "approval-abc123",
                },
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
    assert (
        payload["selected_group"]["group_key"] == "personal-ops:mail:waiting_on_user:high:waiting"
    )
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
                "latest_context": {
                    "thread_id": "thread-abc123",
                    "draft_id": "draft-abc123",
                    "approval_id": "approval-abc123",
                },
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


def test_action_proposal_group_package_can_save_operator_decision_route(tmp_path: Path) -> None:
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
            route="operator_decision",
            hours=2,
            limit=5,
            review_dir=tmp_path,
            group_history_path=tmp_path / "group-history.jsonl",
        )

    assert report["status"] == "ok"
    assert report["action_count"] == 2
    package_path = Path(str(report["review_package"]["path"]))
    payload = json.loads(package_path.read_text(encoding="utf-8"))
    assert payload["selected_group"]["route"] == "operator_decision"
    assert [action["evidence_event_id"] for action in payload["actions"]] == ["abc123", "ghi789"]
    assert report["group_history"] is not None
    assert report["group_history"]["event_type"] == "saved_operator_decision"


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


def test_prune_action_export_files_dry_run_keeps_all(tmp_path: Path) -> None:
    export_dir = tmp_path / "action-exports"
    export_dir.mkdir()
    files = [export_dir / f"personal-ops-actions-2026050{i}-120000.json" for i in range(1, 6)]
    for index, path in enumerate(files):
        path.write_text("{}", encoding="utf-8")
        os.utime(path, (100 + index, 100 + index))

    report = prune_action_export_files(keep=3, export_dir=export_dir)

    assert report["status"] == "ok"
    assert report["dry_run"] is True
    assert report["applied"] is False
    assert report["total_count"] == 5
    assert report["candidate_count"] == 2
    assert report["deleted_count"] == 0
    assert all(p.exists() for p in files)


def test_prune_action_export_files_apply_deletes_oldest(tmp_path: Path) -> None:
    export_dir = tmp_path / "action-exports"
    export_dir.mkdir()
    files = [export_dir / f"personal-ops-actions-2026050{i}-120000.json" for i in range(1, 6)]
    for index, path in enumerate(files):
        path.write_text("{}", encoding="utf-8")
        os.utime(path, (100 + index, 100 + index))

    report = prune_action_export_files(keep=3, dry_run=False, export_dir=export_dir)

    assert report["status"] == "ok"
    assert report["applied"] is True
    assert report["deleted_count"] == 2
    assert report["kept_count"] == 3
    # oldest two (lowest mtime) should be gone
    assert not files[0].exists()
    assert not files[1].exists()
    assert files[2].exists()
    assert files[3].exists()
    assert files[4].exists()


def test_prune_action_export_files_noop_when_under_limit(tmp_path: Path) -> None:
    export_dir = tmp_path / "action-exports"
    export_dir.mkdir()
    path = export_dir / "personal-ops-actions-20260501-120000.json"
    path.write_text("{}", encoding="utf-8")

    report = prune_action_export_files(keep=20, dry_run=False, export_dir=export_dir)

    assert report["status"] == "ok"
    assert report["candidate_count"] == 0
    assert report["deleted_count"] == 0
    assert path.exists()
    assert "No action-export files needed pruning." in report["next_action"]


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
