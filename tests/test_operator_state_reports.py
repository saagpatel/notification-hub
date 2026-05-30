"""Tests for operator state reports and burn-in report review."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from notification_hub.operations import (
    list_personal_ops_queue_burn_in_reports,
    load_personal_ops_queue_burn_in_report_detail,
    review_latest_noise_candidates,
    run_operator_daily_state,
    run_operator_handoff_drill,
    run_personal_ops_import_stub,
    summarize_personal_ops_import_queue,
    update_personal_ops_import_queue_item,
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
    assert payload["report"]["outcome_quality_summary"] == (
        "No promoted handoff outcomes are recorded yet."
    )


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


def test_review_latest_noise_candidates_marks_mail_approvals_as_operator_decisions(
    tmp_path: Path,
) -> None:
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
                    "ready_for_live_promotion": False,
                    "next_action": "Review noise.",
                    "queue_health": {"health": {}},
                    "runtime_burn_in": {
                        "health": {"status": "ok"},
                        "noise_candidates": [
                            {
                                "count": 4,
                                "source": "personal-ops",
                                "project": "mail",
                                "level": "urgent",
                                "title": "Approval Requested",
                                "body": "Initial reply needed",
                            },
                            {
                                "count": 2,
                                "source": "codex",
                                "project": "notification-hub",
                                "level": "normal",
                                "title": "Codex finished a turn",
                                "body": "A Codex turn completed.",
                            },
                        ],
                        "noise_rule_suggestions": [
                            "Review noise rule candidate: source='personal-ops'",
                            "Review noise rule candidate: source='codex'",
                        ],
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = review_latest_noise_candidates(report_dir=report_dir)

    assert report["status"] == "warn"
    assert report["report_name"] == report_path.name
    assert report["noise_candidate_count"] == 2
    assert report["candidates"][0]["decision_hint"] == "operator_decision_required"
    assert report["candidates"][0]["suggested_rule"] == (
        "Review noise rule candidate: source='personal-ops'"
    )
    assert report["candidates"][1]["decision_hint"] == "likely_policy_chatter"
    assert "do not suppress" in report["next_action"]


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
