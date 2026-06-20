"""Tests for inbox rollups and personal-ops action exports."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from notification_hub.config import NoisePolicy, NoiseRule, PolicyConfig
from notification_hub.models import StoredEvent
from notification_hub.operations import (
    _build_near_rollup_singles,  # pyright: ignore[reportPrivateUsage]
    dismiss_action_proposal,
    list_action_proposal_dismissals,
    run_action_proposal_dismissal_list,
    run_coordination_snapshot,
    run_inbox,
    run_personal_ops_action_export,
    undismiss_action_proposal,
)


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


def test_inbox_rollup_carries_latest_event_context() -> None:
    now = datetime.now(UTC)
    older = StoredEvent(
        source="personal-ops",
        level="urgent",
        title="Approval Requested",
        body="Real reply needed",
        project="mail",
        context={"thread_id": "thread-old", "draft_id": "draft-old"},
        timestamp=now - timedelta(minutes=10),
    )
    newer = StoredEvent(
        source="personal-ops",
        level="urgent",
        title="Approval Requested",
        body="Real reply needed",
        project="mail",
        context={"thread_id": "thread-new", "draft_id": "draft-new"},
        timestamp=now - timedelta(minutes=5),
    )

    with (
        patch("notification_hub.operations.read_jsonl", return_value=[older, newer]),
        patch(
            "notification_hub.operations.run_burn_in",
            return_value={
                "status": "ok",
                "minutes": 1440,
                "events_seen": 2,
                "accepted_event_posts": 2,
                "rejected_event_posts": 0,
                "validation_error_count": 0,
                "health": {
                    "accepted_event_posts": 2,
                    "rejected_event_posts": 0,
                    "validation_error_count": 0,
                    "slack_delivery_failure_count": 0,
                    "status": "ok",
                },
                "noise_candidates": [],
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
            },
        ),
    ):
        report = run_inbox(hours=24, limit=5)

    assert report["rollups"][0].get("latest_context") == {
        "thread_id": "thread-new",
        "draft_id": "draft-new",
    }


def test_near_rollup_singles_returns_count_one_events() -> None:
    now = datetime.now(UTC)
    repeated = StoredEvent(
        source="cc",
        level="info",
        title="Build done",
        body="ok",
        project="proj",
        timestamp=now - timedelta(minutes=10),
    )
    repeated2 = StoredEvent(
        source="cc",
        level="info",
        title="Build done",
        body="ok",
        project="proj",
        timestamp=now - timedelta(minutes=5),
    )
    unique = StoredEvent(
        source="personal-ops",
        level="urgent",
        title="Approval Requested",
        body="One-off event",
        project="mail",
        timestamp=now - timedelta(minutes=3),
    )

    rollups = _build_near_rollup_singles([repeated, repeated2, unique])

    assert len(rollups) == 1
    assert rollups[0]["source"] == "personal-ops"
    assert rollups[0]["count"] == 1
    assert rollups[0]["title"] == "Approval Requested"


def test_near_rollup_singles_excludes_repeated_events() -> None:
    now = datetime.now(UTC)
    e1 = StoredEvent(
        source="cc",
        level="info",
        title="X",
        body="Y",
        project=None,
        timestamp=now - timedelta(minutes=5),
    )
    e2 = StoredEvent(
        source="cc",
        level="info",
        title="X",
        body="Y",
        project=None,
        timestamp=now - timedelta(minutes=2),
    )

    singles = _build_near_rollup_singles([e1, e2])

    assert singles == []


def test_run_inbox_includes_near_rollup_singles() -> None:
    now = datetime.now(UTC)
    unique = StoredEvent(
        source="codex",
        level="info",
        title="Deploy finished",
        body="success",
        project="api",
        timestamp=now - timedelta(minutes=5),
    )
    repeated_a = StoredEvent(
        source="cc",
        level="info",
        title="Test passed",
        body="all green",
        project="app",
        timestamp=now - timedelta(minutes=10),
    )
    repeated_b = StoredEvent(
        source="cc",
        level="info",
        title="Test passed",
        body="all green",
        project="app",
        timestamp=now - timedelta(minutes=4),
    )
    burn_in_stub: dict[str, object] = {
        "status": "ok",
        "minutes": 1440,
        "events_seen": 3,
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
        "noise_candidates": [],
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

    with (
        patch(
            "notification_hub.operations.read_jsonl", return_value=[unique, repeated_a, repeated_b]
        ),
        patch("notification_hub.operations.run_burn_in", return_value=burn_in_stub),
    ):
        report = run_inbox(hours=24, limit=10)

    assert len(report["rollups"]) == 1
    assert report["rollups"][0]["source"] == "cc"
    assert len(report["near_rollup_singles"]) == 1
    assert report["near_rollup_singles"][0]["source"] == "codex"
    assert report["near_rollup_singles"][0]["count"] == 1


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


def test_personal_ops_action_export_filters_phase_34_mail_echoes() -> None:
    policy = PolicyConfig(
        noise=NoisePolicy(
            rules=(
                NoiseRule(
                    source="personal-ops",
                    project="mail",
                    title_contains="approval requested",
                    body_contains="phase 34 secondary approval",
                    level="urgent",
                ),
                NoiseRule(
                    source="personal-ops",
                    project="mail",
                    title_contains="draft ready",
                    body_contains="phase 34 secondary approval",
                    level="info",
                ),
            )
        )
    )
    urgent_echo_rollup = {
        "count": 5,
        "source": "personal-ops",
        "project": "mail",
        "intent": "waiting_on_user",
        "level": "urgent",
        "title": "Approval Requested",
        "body": "Phase 34 secondary approval",
        "latest_timestamp": "2026-05-10T15:00:00+00:00",
        "latest_event_id": "phase34urgent",
    }
    info_echo_rollup = {
        "count": 5,
        "source": "personal-ops",
        "project": "mail",
        "intent": "ready_to_review",
        "level": "info",
        "title": "Draft Ready",
        "body": "Phase 34 secondary approval",
        "latest_timestamp": "2026-05-10T15:01:00+00:00",
        "latest_event_id": "phase34info",
    }
    active_rollup = {
        "count": 2,
        "source": "personal-ops",
        "project": "mail",
        "intent": "waiting_on_user",
        "level": "urgent",
        "title": "Approval Requested",
        "body": "Real reply needed",
        "latest_timestamp": "2026-05-10T15:02:00+00:00",
        "latest_event_id": "active123",
    }
    inbox_report: dict[str, object] = {
        "status": "ok",
        "hours": 2,
        "events_seen": 12,
        "needs_attention": [],
        "waiting_or_blocked": [],
        "ready": [],
        "completed": [],
        "rollups": [urgent_echo_rollup, info_echo_rollup, active_rollup],
        "noise_candidates": [],
        "error": None,
    }

    with (
        patch("notification_hub.operations.get_policy_config", return_value=policy),
        patch("notification_hub.operations.run_inbox", return_value=inbox_report),
    ):
        report = run_personal_ops_action_export(hours=2, limit=5)

    assert [action["signal_body"] for action in report["actions"]] == ["Real reply needed"]
    assert report["dismissed_action_count"] == 2


def test_personal_ops_action_export_preserves_mail_evidence_context(tmp_path: Path) -> None:
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
                "body": "Real reply needed",
                "latest_timestamp": "2026-05-10T15:02:00+00:00",
                "latest_event_id": "active123",
                "latest_context": {
                    "thread_id": "thread-123",
                    "draft_id": "draft-456",
                    "approval_id": "approval-789",
                },
            }
        ],
        "noise_candidates": [],
        "error": None,
    }

    with patch("notification_hub.operations.run_inbox", return_value=inbox_report):
        report = run_personal_ops_action_export(
            hours=2,
            limit=5,
            dismissals_path=tmp_path / "dismissals.jsonl",
        )

    assert report["actions"][0].get("evidence_context") == {
        "thread_id": "thread-123",
        "draft_id": "draft-456",
        "approval_id": "approval-789",
    }
    assert report["actions"][0]["evidence_quality"] == "rich"


def test_personal_ops_action_export_scans_past_policy_covered_candidates() -> None:
    policy = PolicyConfig(
        noise=NoisePolicy(
            rules=(
                NoiseRule(
                    source="personal-ops",
                    project="personal-ops",
                    title_contains="system needs attention",
                    body_contains="run personal-ops doctor",
                ),
            )
        )
    )
    doctor_echo_rollup = {
        "count": 3,
        "source": "personal-ops",
        "project": "personal-ops",
        "intent": "needs_attention",
        "level": "urgent",
        "title": "System needs attention",
        "body": "System needs attention: run personal-ops doctor",
        "latest_timestamp": "2026-05-09T00:00:00+00:00",
        "latest_event_id": "doctor123",
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
        "rollups": [doctor_echo_rollup, active_rollup],
        "noise_candidates": [],
        "error": None,
    }

    with (
        patch("notification_hub.operations.get_policy_config", return_value=policy),
        patch("notification_hub.operations.run_inbox", return_value=inbox_report) as mock_inbox,
    ):
        report = run_personal_ops_action_export(hours=2, limit=1)

    assert [action["signal_body"] for action in report["actions"]] == ["Real reply needed"]
    assert report["dismissed_action_count"] == 1
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


def test_personal_ops_action_export_keeps_repeated_titles_unique(tmp_path: Path) -> None:
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
        ],
        "noise_candidates": [],
        "error": None,
    }

    with patch("notification_hub.operations.run_inbox", return_value=inbox_report):
        report = run_personal_ops_action_export(
            hours=2,
            limit=5,
            dismissals_path=tmp_path / "dismissals.jsonl",
        )

    action_ids = [action["action_id"] for action in report["actions"]]
    assert len(action_ids) == len(set(action_ids))
    assert action_ids == [
        "notification-hub:personal-ops:mail:waiting_on_user:approval-requested:abc123",
        "notification-hub:personal-ops:mail:waiting_on_user:approval-requested:def456",
    ]
