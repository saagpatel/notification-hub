"""Tests for notification-hub CLI wrapper entrypoints."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from _pytest.capture import CaptureFixture

from notification_hub.cli import (
    action_proposal_dismissals_main,
    action_proposal_undismiss_main,
    bootstrap_config_main,
    burn_in_main,
    coordination_console_main,
    coordination_readiness_main,
    coordination_snapshot_main,
    delivery_check_main,
    doctor_main,
    explain_main,
    inbox_main,
    logs_main,
    operator_daily_state_main,
    operator_handoff_drill_main,
    personal_ops_actions_main,
    personal_ops_import_main,
    personal_ops_outcome_sync_reminder_main,
    personal_ops_queue_burn_in_main,
    personal_ops_queue_health_main,
    policy_check_main,
    retention_main,
    smoke_main,
    status_main,
    validate_action_package_main,
    verify_runtime_main,
)
from tests.cli_report_fixtures import (
    burn_in_report,
    coordination_console_report,
    coordination_readiness_report,
    coordination_snapshot_report,
    delivery_check_report,
    import_queue_health,
    operator_daily_state_report,
    operator_handoff_drill_report,
    personal_ops_action_export_report,
)


def test_validate_action_package_wrapper_forwards_path(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    package_path = tmp_path / "actions.json"
    package_path.write_text(json.dumps(personal_ops_action_export_report()), encoding="utf-8")

    exit_code = validate_action_package_main([str(package_path), "--json"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert '"status": "ok"' in output.out


def test_personal_ops_import_wrapper_forwards_path(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    package_path = tmp_path / "actions.json"
    package_path.write_text(json.dumps(personal_ops_action_export_report()), encoding="utf-8")

    exit_code = personal_ops_import_main([str(package_path), "--json"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert '"applied": false' in output.out


def test_doctor_main_forwards_flags(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.collect_doctor_report",
        return_value={
            "status": "ok",
            "checks": {"local_api_healthy": True},
            "config": {
                "path": "/tmp/config.toml",
                "load_error": None,
                "routing_rule_count": 0,
                "warning_count": 0,
            },
            "retention": {
                "enabled": True,
                "interval_minutes": 60,
                "max_events": 2000,
                "keep_archives": 10,
            },
            "local_api": {"url": "http://127.0.0.1:9199/health/details"},
        },
    ):
        exit_code = doctor_main(["--json"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"status": "ok"' in captured.out


def test_smoke_and_retention_wrappers_forward_flags(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_smoke_check",
        return_value={
            "status": "ok",
            "health_url": "http://127.0.0.1:9199/health/details",
            "event_url": "http://127.0.0.1:9199/events",
            "event_id": "abc123",
            "log_verified": True,
            "response_status": 201,
            "error": None,
        },
    ):
        smoke_exit = smoke_main(["--json"])

    smoke_output = capsys.readouterr()
    assert smoke_exit == 0
    assert '"event_id": "abc123"' in smoke_output.out

    with patch(
        "notification_hub.cli.run_retention",
        return_value={
            "status": "ok",
            "rotated": False,
            "archive_path": None,
            "events_before": 3,
            "events_after": 3,
            "archived_events": 0,
            "deleted_archives": [],
        },
    ):
        retention_exit = retention_main(["--json"])

    retention_output = capsys.readouterr()
    assert retention_exit == 0
    assert '"events_before": 3' in retention_output.out


def test_status_wrapper_forwards_flags(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_status",
        return_value={
            "status": "ok",
            "health_url": "http://127.0.0.1:9199/health/details",
            "daemon_reachable": True,
            "watcher_active": True,
            "events_processed": 12,
            "uptime_seconds": 123.4,
            "policy_config_found": False,
            "policy_warning_count": 0,
            "retention_enabled": True,
            "retention_last_status": "ok",
            "runtime_wiring_current": True,
            "push_notifier_available": True,
            "slack_configured": True,
            "slack_delivery_failures": 0,
            "visible_slack_delivery_failures": 0,
            "latest_delivery_check": {
                "last_slack_ok_at": None,
                "last_slack_event_id": None,
                "last_push_ok_at": None,
                "last_push_event_id": None,
            },
            "next_action": "No action needed.",
        },
    ) as mock_status:
        exit_code = status_main(["--json"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert '"status": "ok"' in output.out
    mock_status.assert_called_once_with()


def test_logs_wrapper_forwards_flags(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_logs",
        return_value={
            "status": "ok",
            "events_log": "/tmp/events.jsonl",
            "stdout_log": "/tmp/stdout.log",
            "stderr_log": "/tmp/stderr.log",
            "recent_events": [],
            "daemon_summary": {
                "access_status_counts": {},
                "accepted_event_posts": 0,
                "rejected_event_posts": 0,
                "validation_error_count": 0,
                "recent_validation_errors": [],
                "slack_delivery_failure_count": 0,
                "recent_slack_delivery_failures": [],
            },
            "stdout_tail": [],
            "stderr_tail": [],
            "missing_paths": [],
            "error": None,
        },
    ) as mock_logs:
        exit_code = logs_main(["--json", "--events", "2", "--lines", "3"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert '"status": "ok"' in output.out
    mock_logs.assert_called_once_with(events=2, lines=3)


def test_burn_in_wrapper_forwards_flags(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_burn_in",
        return_value={
            "status": "ok",
            "minutes": 5,
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
        },
    ) as mock_burn_in:
        exit_code = burn_in_main(["--minutes", "5", "--lines", "10"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert "notification-hub burn-in: ok" in output.out
    mock_burn_in.assert_called_once_with(minutes=5, lines=10)


def test_verify_runtime_wrapper_forwards_flags(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_verify_runtime",
        return_value={
            "status": "ok",
            "read_only": False,
            "include_smoke": True,
            "health_url": "http://127.0.0.1:9199/health/details",
            "checks": {
                "doctor_ok": True,
                "policy_check_ok": True,
                "health_details_reachable": True,
                "runtime_wiring_current": True,
                "recent_runtime_health_ok": True,
                "smoke_ok": True,
                "delivery_check_ok": True,
            },
            "runtime_wiring": {"launch_agent_matches_template": True},
            "doctor": {"status": "ok"},
            "policy_check": {
                "status": "ok",
                "config_path": "/tmp/config.toml",
                "config_found": True,
                "example_path": "/tmp/example.toml",
                "load_error": None,
                "warning_count": 0,
                "suggestion_count": 0,
                "warnings": [],
                "suggestions": [],
            },
            "burn_in": burn_in_report(),
            "delivery_check": None,
            "smoke": {
                "status": "ok",
                "health_url": "http://127.0.0.1:9199/health/details",
                "event_url": "http://127.0.0.1:9199/events",
                "event_id": "abc123",
                "log_verified": True,
                "response_status": 201,
                "error": None,
            },
        },
    ) as mock_verify:
        exit_code = verify_runtime_main(["--json", "--include-smoke"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert '"include_smoke": true' in output.out
    mock_verify.assert_called_once_with(include_smoke=True, verify_slack=False, verify_push=False)


def test_delivery_check_wrapper_forwards_flags(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_delivery_check",
        return_value=delivery_check_report(verify_slack=True, verify_push=True),
    ) as mock_delivery_check:
        exit_code = delivery_check_main(["--json", "--slack", "--push"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert '"verify_push": true' in output.out
    mock_delivery_check.assert_called_once_with(verify_slack=True, verify_push=True)


def test_inbox_wrapper_forwards_flags(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_inbox",
        return_value={
            "status": "ok",
            "hours": 6,
            "events_seen": 0,
            "needs_attention": [],
            "waiting_or_blocked": [],
            "ready": [],
            "completed": [],
            "rollups": [],
            "noise_candidates": [],
            "error": None,
        },
    ) as mock_inbox:
        exit_code = inbox_main(["--json", "--hours", "6", "--limit", "2"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert '"events_seen": 0' in output.out
    mock_inbox.assert_called_once_with(hours=6, limit=2)


def test_coordination_snapshot_wrapper_forwards_flags(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_coordination_snapshot",
        return_value=coordination_snapshot_report(),
    ) as mock_snapshot:
        exit_code = coordination_snapshot_main(["--json", "--hours", "6", "--limit", "2"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert '"schema_version": "notification-hub.coordination_snapshot.v1"' in output.out
    mock_snapshot.assert_called_once_with(
        hours=6,
        limit=2,
        save_bridge_db=False,
        bridge_db_path=None,
    )


def test_coordination_readiness_wrapper_forwards_flags(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_coordination_readiness",
        return_value=coordination_readiness_report(),
    ) as mock_readiness:
        exit_code = coordination_readiness_main(["--json", "--limit", "2"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert '"savedburn_in_reports": 1' in output.out
    mock_readiness.assert_called_once_with(limit=2)


def test_coordination_console_wrapper_forwards_flags(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_coordination_console",
        return_value=coordination_console_report(),
    ) as mock_console:
        exit_code = coordination_console_main(["--json", "--hours", "6", "--limit", "2"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert '"next_action": "Save and validate a review package."' in output.out
    mock_console.assert_called_once_with(hours=6, limit=2)


def test_action_proposal_dismissals_wrapper_forwards_flags(
    capsys: CaptureFixture[str],
) -> None:
    with patch(
        "notification_hub.cli.run_action_proposal_dismissal_list",
        return_value={
            "status": "ok",
            "path": "/tmp/dismissals.jsonl",
            "dismissal_count": 0,
            "dismissals": [],
            "applied": False,
        },
    ) as mock_list:
        exit_code = action_proposal_dismissals_main(["--json", "--limit", "2"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert '"dismissal_count": 0' in output.out
    mock_list.assert_called_once_with(
        limit=2,
        dismissal_key=None,
        include_inactive=False,
    )


def test_action_proposal_undismiss_wrapper_forwards_flags(
    capsys: CaptureFixture[str],
) -> None:
    with patch(
        "notification_hub.cli.undismiss_action_proposal",
        return_value={
            "status": "ok",
            "path": "/tmp/dismissals.jsonl",
            "dismissal_key": "proposal:abc",
            "removed": True,
            "applied": False,
            "error": None,
        },
    ) as mock_undismiss:
        exit_code = action_proposal_undismiss_main(
            ["--json", "proposal:abc", "--reason", "useful again"]
        )

    output = capsys.readouterr()
    assert exit_code == 0
    assert '"removed": true' in output.out
    mock_undismiss.assert_called_once_with(
        dismissal_key="proposal:abc",
        reason="useful again",
    )


def test_operator_daily_state_wrapper_forwards_flags(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_operator_daily_state",
        return_value=operator_daily_state_report(),
    ) as mock_daily_state:
        exit_code = operator_daily_state_main(["--json", "--hours", "6", "--limit", "2"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert '"next_signal"' in output.out
    mock_daily_state.assert_called_once_with(
        hours=6,
        limit=2,
        save_report=False,
        report_dir=None,
    )


def test_operator_handoff_drill_wrapper_forwards_flags(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_operator_handoff_drill",
        return_value=operator_handoff_drill_report(),
    ) as mock_drill:
        exit_code = operator_handoff_drill_main(["--json", "--save-burn-in-report"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert '"queue_burn_in"' in output.out
    mock_drill.assert_called_once_with(save_burn_in_report=True, report_dir=None)


def test_personal_ops_actions_wrapper_forwards_flags(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_personal_ops_action_export",
        return_value=personal_ops_action_export_report(),
    ) as mock_export:
        exit_code = personal_ops_actions_main(["--json", "--hours", "6", "--limit", "2"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert '"actions"' in output.out
    mock_export.assert_called_once_with(
        hours=6,
        limit=2,
        save_review_package=False,
        review_dir=None,
    )


def test_personal_ops_queue_health_wrapper_forwards_flags(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_personal_ops_import_queue_health_check",
        return_value={
            "status": "ok",
            "health": import_queue_health(),
            "queued_items": [],
            "pending_promotion_items": [],
            "next_commands": ["uv run notification-hub personal-ops-queue-health"],
            "applied": False,
        },
    ) as mock_health:
        exit_code = personal_ops_queue_health_main(["--json", "--limit", "2"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert '"next_commands"' in output.out
    mock_health.assert_called_once()
    assert mock_health.call_args.kwargs["limit"] == 2


def test_personal_ops_outcome_sync_reminder_wrapper_forwards_flags(
    capsys: CaptureFixture[str],
) -> None:
    with patch(
        "notification_hub.cli.run_personal_ops_outcome_sync_reminder",
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
    ) as mock_reminder:
        exit_code = personal_ops_outcome_sync_reminder_main(["--json", "--limit", "2"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert '"should_remind": false' in output.out
    mock_reminder.assert_called_once()
    assert mock_reminder.call_args.kwargs["limit"] == 2


def test_personal_ops_queue_burn_in_wrapper_forwards_flags(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_personal_ops_queue_burn_in",
        return_value={
            "status": "ok",
            "queue_health": {
                "status": "ok",
                "health": import_queue_health(),
                "queued_items": [],
                "pending_promotion_items": [],
                "next_commands": ["uv run notification-hub personal-ops-queue-health"],
                "applied": False,
            },
            "scenario": {
                "status": "ok",
                "queue_path": "/tmp/scenario-queue.jsonl",
                "package_path": "/tmp/package.json",
                "queue_id": "queue123",
                "queued_count": 1,
                "review_status": "ok",
                "promotion_status": "ok",
                "promotion_outcome": "accepted",
                "final_health": import_queue_health(),
                "applied": True,
                "next_action": "Scenario passed; use the same lifecycle for real queued handoffs.",
                "error": None,
            },
            "runtime_burn_in": burn_in_report(),
            "ready_for_live_promotion": True,
            "outcome_sync_posture": "operator-mediated",
            "operator_steps": ["Queue loop is ready."],
            "next_action": "Queue loop is ready; use the operator steps when the next real handoff appears.",
            "report_file": {
                "requested": False,
                "status": "not_requested",
                "path": None,
                "error": None,
            },
            "applied": False,
        },
    ) as mock_burn_in:
        exit_code = personal_ops_queue_burn_in_main(["--json", "--minutes", "5"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert '"operator_steps"' in output.out
    mock_burn_in.assert_called_once()
    assert mock_burn_in.call_args.kwargs["minutes"] == 5
    assert mock_burn_in.call_args.kwargs["save_report"] is False


def test_policy_check_wrapper_forwards_flags(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_policy_check",
        return_value={
            "status": "warn",
            "config_path": "/tmp/config.toml",
            "config_found": True,
            "example_path": "/tmp/example.toml",
            "load_error": None,
            "warning_count": 1,
            "suggestion_count": 1,
            "warnings": ["shadowed rule"],
            "suggestions": ["move the narrower rule earlier"],
        },
    ) as mock_policy_check:
        exit_code = policy_check_main(["--json"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert '"status": "warn"' in output.out
    mock_policy_check.assert_called_once_with()


def test_explain_wrapper_forwards_flags(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.build_event_explanation_report",
        return_value={
            "event": {
                "source": "codex",
                "level": "info",
                "title": "x",
                "body": "y",
                "project": None,
            },
            "classification": {
                "input_level": "info",
                "output_level": "normal",
                "reason": "matched normal keyword",
                "matched_keyword": "session complete",
                "matched_group": "normal",
            },
            "routing": {
                "final_level": "normal",
                "allow_push": True,
                "allow_slack": True,
                "matched_rule_index": 1,
                "matched_rule": {"project": "notification-hub"},
                "matched_rule_indices": [1],
                "matched_rules": [{"project": "notification-hub"}],
                "reason": "matched routing rule 1",
            },
            "delivery": {"log": True, "push": False, "slack": True},
        },
    ) as mock_explain:
        exit_code = explain_main(
            [
                "--source",
                "codex",
                "--level",
                "info",
                "--title",
                "x",
                "--body",
                "y",
                "--json",
            ]
        )

    output = capsys.readouterr()
    assert exit_code == 0
    assert '"final_level": "normal"' in output.out
    mock_explain.assert_called_once()


def test_bootstrap_wrapper_forwards_flags(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.bootstrap_policy_config",
        return_value={
            "status": "ok",
            "copied": False,
            "config_path": "/tmp/config.toml",
            "example_path": "/tmp/example.toml",
            "error": None,
        },
    ) as mock_bootstrap:
        exit_code = bootstrap_config_main(["--json", "--force"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert '"copied": false' in output.out
    mock_bootstrap.assert_called_once_with(force=True)
