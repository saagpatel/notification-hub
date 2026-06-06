"""Tests for notification-hub CLI command output."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from _pytest.capture import CaptureFixture

from notification_hub.cli import main
from tests.cli_report_fixtures import (
    burn_in_report,
    coordination_console_report,
    coordination_readiness_report,
    coordination_snapshot_report,
    import_queue_health,
    operator_daily_state_report,
    operator_handoff_drill_report,
    personal_ops_action_export_report,
)


def test_cli_json_output(capsys: CaptureFixture[str]) -> None:
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
        exit_code = main(["doctor", "--json"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"status": "ok"' in captured.out


def test_cli_smoke_json_output(capsys: CaptureFixture[str]) -> None:
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
        exit_code = main(["smoke", "--json"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"event_id": "abc123"' in captured.out


def test_cli_inbox_json_output(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_inbox",
        return_value={
            "status": "ok",
            "hours": 12,
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
        },
    ) as mock_inbox:
        exit_code = main(["inbox", "--json", "--hours", "12", "--limit", "3"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"ready_to_review"' in captured.out
    mock_inbox.assert_called_once_with(hours=12, limit=3)


def test_cli_coordination_snapshot_json_output(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_coordination_snapshot",
        return_value=coordination_snapshot_report(),
    ) as mock_snapshot:
        exit_code = main(["coordination-snapshot", "--json", "--hours", "12", "--limit", "3"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"bridge_target_system": "codex"' in captured.out
    mock_snapshot.assert_called_once_with(
        hours=12,
        limit=3,
        save_bridge_db=False,
        bridge_db_path=None,
    )


def test_cli_coordination_readiness_json_output(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_coordination_readiness",
        return_value=coordination_readiness_report(),
    ) as mock_readiness:
        exit_code = main(["coordination-readiness", "--json", "--limit", "3"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"decision": "ready_to_expand"' in captured.out
    mock_readiness.assert_called_once_with(limit=3)


def test_cli_coordination_console_json_output(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_coordination_console",
        return_value=coordination_console_report(),
    ) as mock_console:
        exit_code = main(["coordination-console", "--json", "--hours", "4", "--limit", "3"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"action_count": 1' in captured.out
    assert '"guide_stage": "package_review"' in captured.out
    mock_console.assert_called_once_with(hours=4, limit=3)


def test_cli_action_proposal_dismissals_json_output(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_action_proposal_dismissal_list",
        return_value={
            "status": "ok",
            "path": "/tmp/dismissals.jsonl",
            "dismissal_count": 1,
            "dismissals": [
                {
                    "dismissal_key": "proposal:abc",
                    "dismissed_at": "2026-05-10T04:41:00+00:00",
                    "deleted_at": None,
                    "active": True,
                    "reason": "known noise",
                    "source": "personal-ops",
                    "project": "mail",
                    "intent": "waiting_on_user",
                    "title": "Approval Requested",
                    "body": "Test draft",
                    "evidence_event_id": "event-1",
                }
            ],
            "applied": False,
        },
    ) as mock_list:
        exit_code = main(
            [
                "action-proposal-dismissals",
                "--json",
                "--limit",
                "3",
                "--dismissal-key",
                "proposal:abc",
                "--include-inactive",
            ]
        )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"dismissal_key": "proposal:abc"' in captured.out
    mock_list.assert_called_once_with(
        limit=3,
        dismissal_key="proposal:abc",
        include_inactive=True,
    )


def test_cli_action_proposal_group_outcome_uses_review_window_by_default(
    capsys: CaptureFixture[str],
) -> None:
    with patch(
        "notification_hub.cli.record_action_proposal_group_outcome",
        return_value={
            "status": "ok",
            "group_key": "personal-ops:mail:waiting_on_user:high:waiting",
            "outcome": "needs_follow_up",
            "group_history": {"event_type": "outcome", "outcome": "needs_follow_up"},
            "next_action": "Group outcome recorded locally.",
            "applied": False,
            "error": None,
        },
    ) as mock_outcome:
        exit_code = main(
            [
                "action-proposal-group-outcome",
                "personal-ops:mail:waiting_on_user:high:waiting",
                "--outcome",
                "needs_follow_up",
                "--reason",
                "follow up",
                "--json",
            ]
        )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"outcome": "needs_follow_up"' in captured.out
    mock_outcome.assert_called_once_with(
        group_key="personal-ops:mail:waiting_on_user:high:waiting",
        outcome="needs_follow_up",
        reason="follow up",
        hours=24,
        limit=25,
    )


def test_cli_operator_daily_state_json_output(capsys: CaptureFixture[str]) -> None:
    report_dir = Path("/tmp/operator-state")
    with patch(
        "notification_hub.cli.run_operator_daily_state",
        return_value=operator_daily_state_report(),
    ) as mock_daily_state:
        exit_code = main(
            [
                "operator-daily-state",
                "--json",
                "--hours",
                "6",
                "--limit",
                "3",
                "--save-report",
                "--report-dir",
                str(report_dir),
            ]
        )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"generated_at": "2026-05-10T04:50:00+00:00"' in captured.out
    mock_daily_state.assert_called_once_with(
        hours=6,
        limit=3,
        save_report=True,
        report_dir=report_dir,
    )


def test_cli_operator_handoff_drill_json_output(capsys: CaptureFixture[str]) -> None:
    report_dir = Path("/tmp/burn-in")
    with patch(
        "notification_hub.cli.run_operator_handoff_drill",
        return_value=operator_handoff_drill_report(),
    ) as mock_drill:
        exit_code = main(
            [
                "operator-handoff-drill",
                "--json",
                "--save-burn-in-report",
                "--report-dir",
                str(report_dir),
            ]
        )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"review_steps"' in captured.out
    mock_drill.assert_called_once_with(save_burn_in_report=True, report_dir=report_dir)


def test_cli_coordination_snapshot_writes_output(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    output_path = tmp_path / "snapshot.json"
    with patch(
        "notification_hub.cli.run_coordination_snapshot",
        return_value=coordination_snapshot_report(),
    ):
        exit_code = main(["coordination-snapshot", "--output", str(output_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert str(output_path) in captured.out
    assert json.loads(output_path.read_text(encoding="utf-8"))["bridge_target_system"] == "codex"


def test_cli_coordination_snapshot_can_request_bridge_save(tmp_path: Path) -> None:
    bridge_db_path = tmp_path / "bridge.db"
    with patch(
        "notification_hub.cli.run_coordination_snapshot",
        return_value=coordination_snapshot_report(),
    ) as mock_snapshot:
        exit_code = main(
            [
                "coordination-snapshot",
                "--json",
                "--save-bridge-db",
                "--bridge-db-path",
                str(bridge_db_path),
            ]
        )

    assert exit_code == 0
    mock_snapshot.assert_called_once_with(
        hours=24,
        limit=10,
        save_bridge_db=True,
        bridge_db_path=bridge_db_path,
    )


def test_cli_personal_ops_actions_json_output(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_personal_ops_action_export",
        return_value=personal_ops_action_export_report(),
    ) as mock_export:
        exit_code = main(["personal-ops-actions", "--json", "--hours", "12", "--limit", "3"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"schema_version": "notification-hub.personal_ops_action_export.v1"' in captured.out
    mock_export.assert_called_once_with(
        hours=12,
        limit=3,
        save_review_package=False,
        review_dir=None,
    )


def test_cli_personal_ops_actions_writes_output(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    output_path = tmp_path / "actions.json"
    with patch(
        "notification_hub.cli.run_personal_ops_action_export",
        return_value=personal_ops_action_export_report(),
    ):
        exit_code = main(["personal-ops-actions", "--output", str(output_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert str(output_path) in captured.out
    assert json.loads(output_path.read_text(encoding="utf-8"))["actions"][0]["state"] == "waiting"


def test_cli_personal_ops_actions_can_save_review_package(tmp_path: Path) -> None:
    review_dir = tmp_path / "review"
    with patch(
        "notification_hub.cli.run_personal_ops_action_export",
        return_value=personal_ops_action_export_report(),
    ) as mock_export:
        exit_code = main(
            [
                "personal-ops-actions",
                "--json",
                "--save-review-package",
                "--review-dir",
                str(review_dir),
            ]
        )

    assert exit_code == 0
    mock_export.assert_called_once_with(
        hours=24,
        limit=10,
        save_review_package=True,
        review_dir=review_dir,
    )


def test_cli_validate_action_package_json_output(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    package_path = tmp_path / "actions.json"
    package_path.write_text(json.dumps(personal_ops_action_export_report()), encoding="utf-8")

    exit_code = main(["validate-action-package", str(package_path), "--json"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"valid_action_count": 1' in captured.out


def test_cli_personal_ops_import_json_output(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    package_path = tmp_path / "actions.json"
    package_path.write_text(json.dumps(personal_ops_action_export_report()), encoding="utf-8")

    exit_code = main(["personal-ops-import", str(package_path), "--json"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"applied": false' in captured.out
    assert '"valid_action_count": 1' in captured.out


def test_cli_personal_ops_queue_health_json_output(capsys: CaptureFixture[str]) -> None:
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
        exit_code = main(
            ["personal-ops-queue-health", "--json", "--limit", "3", "--stale-after-hours", "2"]
        )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"applied": false' in captured.out
    assert '"status": "ok"' in captured.out
    mock_health.assert_called_once()
    assert mock_health.call_args.kwargs["limit"] == 3
    assert mock_health.call_args.kwargs["stale_after_hours"] == 2.0


def test_cli_personal_ops_queue_review_json_output(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_personal_ops_queue_review",
        return_value={
            "status": "warn",
            "queue_status": "warn",
            "queued_count": 2,
            "pending_count": 0,
            "stale_count": 0,
            "operator_decision_count": 2,
            "batch_count": 1,
            "batches": [],
            "next_action": "Review queued handoff batches before expanding coordination.",
            "next_commands": ["uv run notification-hub personal-ops-queue"],
            "applied": False,
        },
    ) as mock_review:
        exit_code = main(
            ["personal-ops-queue-review", "--json", "--limit", "3", "--stale-after-hours", "2"]
        )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert '"operator_decision_count": 2' in captured.out
    assert '"applied": false' in captured.out
    mock_review.assert_called_once()
    assert mock_review.call_args.kwargs["limit"] == 3
    assert mock_review.call_args.kwargs["stale_after_hours"] == 2.0


def test_cli_personal_ops_outcome_sync_reminder_json_output(
    capsys: CaptureFixture[str],
) -> None:
    with patch(
        "notification_hub.cli.run_personal_ops_outcome_sync_reminder",
        return_value={
            "status": "warn",
            "should_remind": True,
            "pending_count": 1,
            "stale_count": 1,
            "reminders": [],
            "next_commands": [
                'personal-ops suggestion accept|reject SUGGESTION_ID --note "..."',
                "uv run notification-hub personal-ops-queue --queue-id QUEUE_ID "
                "--status promoted --promotion-target-id SUGGESTION_ID "
                '--promotion-outcome accepted|rejected --promotion-outcome-note "..."',
            ],
            "next_action": "Resolve stale promoted personal-ops handoff outcomes before promoting more work.",
            "applied": False,
        },
    ) as mock_reminder:
        exit_code = main(
            [
                "personal-ops-outcome-sync-reminder",
                "--json",
                "--limit",
                "3",
                "--stale-after-hours",
                "2",
            ]
        )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert '"should_remind": true' in captured.out
    assert '"applied": false' in captured.out
    mock_reminder.assert_called_once()
    assert mock_reminder.call_args.kwargs["limit"] == 3
    assert mock_reminder.call_args.kwargs["stale_after_hours"] == 2.0


def test_cli_personal_ops_queue_burn_in_json_output(capsys: CaptureFixture[str]) -> None:
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
                "requested": True,
                "status": "ok",
                "path": "/tmp/reports/personal-ops-queue-burn-in.json",
                "error": None,
            },
            "applied": False,
        },
    ) as mock_burn_in:
        exit_code = main(
            [
                "personal-ops-queue-burn-in",
                "--json",
                "--minutes",
                "5",
                "--lines",
                "20",
                "--limit",
                "3",
                "--save-report",
                "--report-dir",
                "/tmp/reports",
            ]
        )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"ready_for_live_promotion": true' in captured.out
    mock_burn_in.assert_called_once_with(
        minutes=5,
        lines=20,
        limit=3,
        save_report=True,
        report_dir=Path("/tmp/reports"),
    )


def test_cli_policy_check_json_output(capsys: CaptureFixture[str]) -> None:
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
    ):
        exit_code = main(["policy-check", "--json"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"warning_count": 1' in captured.out
    assert '"suggestion_count": 1' in captured.out


def test_cli_explain_json_output(capsys: CaptureFixture[str]) -> None:
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
                "output_level": "urgent",
                "reason": "matched urgent keyword",
                "matched_keyword": "approval needed",
                "matched_group": "urgent",
            },
            "routing": {
                "final_level": "urgent",
                "allow_push": True,
                "allow_slack": True,
                "matched_rule_index": None,
                "matched_rule": None,
                "matched_rule_indices": [],
                "matched_rules": [],
                "reason": "no routing rule matched",
            },
            "delivery": {"log": True, "push": True, "slack": True},
        },
    ):
        exit_code = main(
            [
                "explain",
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

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"matched_keyword": "approval needed"' in captured.out


def test_cli_burn_in_json_output(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_burn_in",
        return_value={
            "status": "ok",
            "minutes": 10,
            "events_seen": 3,
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
            "noise_candidates": [
                {
                    "count": 2,
                    "source": "personal-ops",
                    "project": "personal-ops",
                    "level": "info",
                    "title": "Approval expires soon",
                    "body": "Approval expires soon: review or cancel",
                }
            ],
            "noise_rule_suggestions": [
                "Review noise rule candidate: source='personal-ops', project='personal-ops', title_contains='Approval expires soon', level='info', window_minutes=10"
            ],
            "repeated_signatures": [
                {
                    "count": 2,
                    "source": "personal-ops",
                    "project": "personal-ops",
                    "level": "info",
                    "title": "Approval expires soon",
                    "body": "Approval expires soon: review or cancel",
                }
            ],
            "slack_eligible_events": 0,
            "slack_volume": [],
            "daemon_summary": {
                "access_status_counts": {"201": 2},
                "accepted_event_posts": 2,
                "rejected_event_posts": 0,
                "validation_error_count": 0,
                "recent_validation_errors": [],
                "slack_delivery_failure_count": 0,
                "recent_slack_delivery_failures": [],
            },
            "error": None,
        },
    ) as mock_burn_in:
        exit_code = main(["burn-in", "--json", "--minutes", "10", "--lines", "20"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"events_seen": 3' in captured.out
    mock_burn_in.assert_called_once_with(minutes=10, lines=20)


def test_cli_burn_in_json_output_exits_nonzero_when_degraded(
    capsys: CaptureFixture[str],
) -> None:
    with patch(
        "notification_hub.cli.run_burn_in",
        return_value={
            "status": "degraded",
            "minutes": 10,
            "events_seen": 0,
            "accepted_event_posts": 0,
            "rejected_event_posts": 0,
            "validation_error_count": 0,
            "health": {
                "accepted_event_posts": 0,
                "rejected_event_posts": 0,
                "validation_error_count": 0,
                "slack_delivery_failure_count": 1,
                "status": "degraded",
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
                "slack_delivery_failure_count": 1,
                "recent_slack_delivery_failures": ["Slack send failed"],
            },
            "error": None,
        },
    ) as mock_burn_in:
        exit_code = main(["burn-in", "--json", "--minutes", "10", "--lines", "20"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert '"status": "degraded"' in captured.out
    mock_burn_in.assert_called_once_with(minutes=10, lines=20)


def test_cli_retention_json_output(capsys: CaptureFixture[str]) -> None:
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
        exit_code = main(["retention", "--json"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"events_before": 3' in captured.out


def test_cli_bootstrap_json_output(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.bootstrap_policy_config",
        return_value={
            "status": "ok",
            "copied": True,
            "config_path": "/tmp/config.toml",
            "example_path": "/tmp/example.toml",
            "error": None,
        },
    ):
        exit_code = main(["bootstrap-config", "--json"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"copied": true' in captured.out
