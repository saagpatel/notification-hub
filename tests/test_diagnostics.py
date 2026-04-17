"""Tests for operator diagnostics and doctor output."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
from _pytest.capture import CaptureFixture

from notification_hub.cli import (
    bootstrap_config_main,
    doctor_main,
    explain_main,
    main,
    policy_check_main,
    retention_main,
    smoke_main,
)
from notification_hub.diagnostics import collect_doctor_report, collect_runtime_readiness


def test_collect_runtime_readiness_reports_config_and_paths() -> None:
    with (
        patch("notification_hub.diagnostics.channels_mod.has_push_notifier", return_value=True),
        patch("notification_hub.diagnostics.config_mod.has_slack_webhook_configured", return_value=False),
        patch(
            "notification_hub.diagnostics.config_mod.get_policy_config",
            return_value=MagicMock(
                path="/tmp/config.toml",
                config_found=True,
                load_error=None,
                routing=MagicMock(rules=("a", "b")),
            ),
        ),
        patch("notification_hub.diagnostics.config_mod.analyze_policy_config", return_value=("w1", "w2", "w3")),
        patch("notification_hub.diagnostics._path_exists", side_effect=[True, True, False, True]),
    ):
        data = collect_runtime_readiness()

    assert data["delivery"] == {
        "push_notifier_available": True,
        "slack_webhook_configured": False,
    }
    assert data["paths"] == {
        "bridge_file_exists": True,
        "events_dir_exists": True,
        "events_log_exists": False,
        "launch_agent_exists": True,
    }
    assert data["config"] == {
        "path": "/tmp/config.toml",
        "exists": True,
        "load_error": None,
        "routing_rule_count": 2,
        "warning_count": 3,
    }


def test_collect_doctor_report_handles_local_api_failure() -> None:
    with (
        patch(
            "notification_hub.diagnostics.collect_runtime_readiness",
            return_value={
                "delivery": {
                    "push_notifier_available": True,
                    "slack_webhook_configured": True,
                },
                "paths": {
                    "bridge_file_exists": True,
                    "events_dir_exists": True,
                    "events_log_exists": True,
                    "launch_agent_exists": True,
                },
                "config": {
                    "path": "/tmp/config.toml",
                    "exists": False,
                    "load_error": None,
                    "routing_rule_count": 0,
                    "warning_count": 0,
                },
            },
        ),
        patch(
            "notification_hub.diagnostics.httpx.get",
            side_effect=httpx.ConnectError("boom"),
        ),
    ):
        report = collect_doctor_report()

    checks = report["checks"]
    assert isinstance(checks, dict)
    assert report["status"] == "degraded"
    assert checks["local_api_healthy"] is False


def test_cli_json_output(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.collect_doctor_report",
        return_value={
            "status": "ok",
            "checks": {"local_api_healthy": True},
            "config": {"path": "/tmp/config.toml", "load_error": None, "routing_rule_count": 0, "warning_count": 0},
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
            "event": {"source": "codex", "level": "info", "title": "x", "body": "y", "project": None},
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


def test_doctor_main_forwards_flags(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.collect_doctor_report",
        return_value={
            "status": "ok",
            "checks": {"local_api_healthy": True},
            "config": {"path": "/tmp/config.toml", "load_error": None, "routing_rule_count": 0, "warning_count": 0},
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
            "event": {"source": "codex", "level": "info", "title": "x", "body": "y", "project": None},
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
