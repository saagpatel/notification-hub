"""Tests for operator diagnostics and doctor output."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from _pytest.capture import CaptureFixture
from pytest import MonkeyPatch

from notification_hub.cli import (
    bootstrap_config_main,
    doctor_main,
    explain_main,
    logs_main,
    main,
    policy_check_main,
    retention_main,
    smoke_main,
    status_main,
    verify_runtime_main,
)
import notification_hub.config as config_mod
from notification_hub.diagnostics import (
    collect_doctor_report,
    collect_runtime_readiness,
    collect_runtime_wiring,
)
from notification_hub.operations import run_status, run_verify_runtime


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
                retention=MagicMock(
                    enabled=True,
                    interval_minutes=60,
                    max_events=2000,
                    keep_archives=10,
                ),
            ),
        ),
        patch("notification_hub.diagnostics.config_mod.analyze_policy_config", return_value=("w1", "w2", "w3")),
        patch("notification_hub.diagnostics._path_exists", side_effect=[True, True, False, True]),
        patch(
            "notification_hub.diagnostics.collect_runtime_wiring",
            return_value={
                "launch_agent_matches_template": True,
                "claude_hook_matches_template": True,
                "codex_hook_matches_template": True,
                "launch_agent_uses_frozen": True,
                "claude_hook_uses_safe_json": True,
                "hook_timeout_configured": True,
                "codex_hook_executable": True,
            },
        ),
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
    assert data["retention"] == {
        "enabled": True,
        "interval_minutes": 60,
        "max_events": 2000,
        "keep_archives": 10,
    }
    assert data["runtime_wiring"] == {
        "launch_agent_matches_template": True,
        "claude_hook_matches_template": True,
        "codex_hook_matches_template": True,
        "launch_agent_uses_frozen": True,
        "claude_hook_uses_safe_json": True,
        "hook_timeout_configured": True,
        "codex_hook_executable": True,
    }


def test_collect_runtime_wiring_compares_installed_files_to_templates(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    launch_agent = tmp_path / "com.saagar.notification-hub.plist"
    launch_agent_template = tmp_path / "template.plist"
    claude_hook = tmp_path / "notify.sh"
    claude_hook_template = tmp_path / "claude-template.sh"
    codex_hook = tmp_path / "notify_local.py"
    codex_hook_template = tmp_path / "codex-template.py"

    launch_agent.write_text("uv run --frozen uvicorn\n", encoding="utf-8")
    launch_agent_template.write_text("uv run --frozen uvicorn\n", encoding="utf-8")
    claude_hook.write_text("jq -n --arg repo x\ncurl --max-time 2\n", encoding="utf-8")
    claude_hook_template.write_text("jq -n --arg repo x\ncurl --max-time 2\n", encoding="utf-8")
    codex_hook.write_text("urllib.request.urlopen(req, timeout=2)\n", encoding="utf-8")
    codex_hook_template.write_text("urllib.request.urlopen(req, timeout=2)\n", encoding="utf-8")
    codex_hook.chmod(0o755)

    monkeypatch.setattr(config_mod, "LAUNCH_AGENT_PLIST", launch_agent)
    monkeypatch.setattr(config_mod, "LAUNCH_AGENT_TEMPLATE", launch_agent_template)
    monkeypatch.setattr(config_mod, "CLAUDE_HOOK", claude_hook)
    monkeypatch.setattr(config_mod, "CLAUDE_HOOK_TEMPLATE", claude_hook_template)
    monkeypatch.setattr(config_mod, "CODEX_HOOK", codex_hook)
    monkeypatch.setattr(config_mod, "CODEX_HOOK_TEMPLATE", codex_hook_template)

    assert collect_runtime_wiring() == {
        "launch_agent_matches_template": True,
        "claude_hook_matches_template": True,
        "codex_hook_matches_template": True,
        "launch_agent_uses_frozen": True,
        "claude_hook_uses_safe_json": True,
        "hook_timeout_configured": True,
        "codex_hook_executable": True,
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
                "retention": {
                    "enabled": True,
                    "interval_minutes": 60,
                    "max_events": 2000,
                    "keep_archives": 10,
                },
                "runtime_wiring": {
                    "launch_agent_matches_template": True,
                    "claude_hook_matches_template": True,
                    "codex_hook_matches_template": True,
                    "launch_agent_uses_frozen": True,
                    "claude_hook_uses_safe_json": True,
                    "hook_timeout_configured": True,
                    "codex_hook_executable": True,
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
            "retention": {"enabled": True, "interval_minutes": 60, "max_events": 2000, "keep_archives": 10},
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


def test_run_status_summarizes_healthy_runtime() -> None:
    with patch(
        "notification_hub.operations.run_verify_runtime",
        return_value={
            "status": "ok",
            "read_only": True,
            "include_smoke": False,
            "health_url": "http://127.0.0.1:9199/health/details",
            "checks": {
                "doctor_ok": True,
                "policy_check_ok": True,
                "health_details_reachable": True,
                "runtime_wiring_current": True,
                "smoke_ok": True,
            },
            "runtime_wiring": {"launch_agent_matches_template": True},
            "doctor": {
                "status": "ok",
                "checks": {"policy_load_ok": True, "runtime_wiring_current": True},
                "config": {"exists": False},
                "delivery": {
                    "push_notifier_available": True,
                    "slack_webhook_configured": True,
                },
                "local_api": {
                    "payload": {
                        "events_processed": 12,
                        "watcher_active": True,
                        "uptime_seconds": 123.4,
                        "retention": {"enabled": True, "last_status": "ok"},
                    }
                },
            },
            "policy_check": {
                "status": "ok",
                "config_path": "/tmp/config.toml",
                "config_found": False,
                "example_path": "/tmp/example.toml",
                "load_error": None,
                "warning_count": 0,
                "suggestion_count": 0,
                "warnings": [],
                "suggestions": [],
            },
            "smoke": None,
        },
    ):
        report = run_status()

    assert report == {
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
        "next_action": "No action needed.",
    }


def test_run_status_suggests_runtime_wiring_repair() -> None:
    with patch(
        "notification_hub.operations.run_verify_runtime",
        return_value={
            "status": "degraded",
            "read_only": True,
            "include_smoke": False,
            "health_url": "http://127.0.0.1:9199/health/details",
            "checks": {
                "doctor_ok": False,
                "policy_check_ok": True,
                "health_details_reachable": True,
                "runtime_wiring_current": False,
                "smoke_ok": True,
            },
            "runtime_wiring": {"launch_agent_matches_template": False},
            "doctor": {
                "status": "degraded",
                "checks": {"policy_load_ok": True, "runtime_wiring_current": False},
                "config": {"exists": True},
                "delivery": {},
                "local_api": {"payload": {}},
            },
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
            "smoke": None,
        },
    ):
        report = run_status()

    assert report["status"] == "degraded"
    assert report["runtime_wiring_current"] is False
    assert report["next_action"] == "Refresh runtime templates from ops/, then run verify-runtime again."


def test_cli_status_json_output(capsys: CaptureFixture[str]) -> None:
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
            "next_action": "No action needed.",
        },
    ) as mock_status:
        exit_code = main(["status", "--json"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"next_action": "No action needed."' in captured.out
    mock_status.assert_called_once_with()


def test_cli_logs_json_output(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_logs",
        return_value={
            "status": "ok",
            "events_log": "/tmp/events.jsonl",
            "stdout_log": "/tmp/stdout.log",
            "stderr_log": "/tmp/stderr.log",
            "recent_events": [
                {
                    "event_id": "abc123",
                    "timestamp": "2026-04-24T00:00:00+00:00",
                    "source": "codex",
                    "level": "info",
                    "classified_level": "info",
                    "project": "notification-hub",
                    "title": "done",
                    "body": "finished",
                }
            ],
            "daemon_summary": {
                "access_status_counts": {"201": 1},
                "accepted_event_posts": 1,
                "rejected_event_posts": 0,
                "validation_error_count": 0,
                "recent_validation_errors": [],
            },
            "stdout_tail": ["out"],
            "stderr_tail": ["err"],
            "missing_paths": [],
            "error": None,
        },
    ) as mock_logs:
        exit_code = main(["logs", "--json", "--events", "1", "--lines", "1"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"event_id": "abc123"' in captured.out
    mock_logs.assert_called_once_with(events=1, lines=1)


def test_run_verify_runtime_is_read_only_by_default() -> None:
    with (
        patch(
            "notification_hub.operations.collect_doctor_report",
            return_value={
                "status": "ok",
                "checks": {"runtime_wiring_current": True},
                "local_api": {"reachable": True, "url": "http://127.0.0.1:9199/health/details"},
                "runtime_wiring": {
                    "launch_agent_matches_template": True,
                    "claude_hook_matches_template": True,
                    "codex_hook_matches_template": True,
                },
            },
        ),
        patch(
            "notification_hub.operations.run_policy_check",
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
        ),
        patch("notification_hub.operations.run_smoke_check") as mock_smoke,
    ):
        report = run_verify_runtime()

    assert report["status"] == "ok"
    assert report["read_only"] is True
    assert report["include_smoke"] is False
    assert report["smoke"] is None
    assert report["checks"] == {
        "doctor_ok": True,
        "policy_check_ok": True,
        "health_details_reachable": True,
        "runtime_wiring_current": True,
        "smoke_ok": True,
    }
    mock_smoke.assert_not_called()


def test_run_verify_runtime_reports_degraded_policy() -> None:
    with (
        patch(
            "notification_hub.operations.collect_doctor_report",
            return_value={
                "status": "ok",
                "checks": {"runtime_wiring_current": True},
                "local_api": {"reachable": True, "url": "http://127.0.0.1:9199/health/details"},
                "runtime_wiring": {"launch_agent_matches_template": True},
            },
        ),
        patch(
            "notification_hub.operations.run_policy_check",
            return_value={
                "status": "degraded",
                "config_path": "/tmp/config.toml",
                "config_found": True,
                "example_path": "/tmp/example.toml",
                "load_error": "invalid TOML",
                "warning_count": 0,
                "suggestion_count": 0,
                "warnings": [],
                "suggestions": [],
            },
        ),
    ):
        report = run_verify_runtime()

    assert report["status"] == "degraded"
    assert report["checks"]["policy_check_ok"] is False


def test_run_verify_runtime_smoke_is_opt_in() -> None:
    with (
        patch(
            "notification_hub.operations.collect_doctor_report",
            return_value={
                "status": "ok",
                "checks": {"runtime_wiring_current": True},
                "local_api": {"reachable": True, "url": "http://127.0.0.1:9199/health/details"},
                "runtime_wiring": {"launch_agent_matches_template": True},
            },
        ),
        patch(
            "notification_hub.operations.run_policy_check",
            return_value={
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
        ),
        patch(
            "notification_hub.operations.run_smoke_check",
            return_value={
                "status": "degraded",
                "health_url": "http://127.0.0.1:9199/health/details",
                "event_url": "http://127.0.0.1:9199/events",
                "event_id": None,
                "log_verified": False,
                "response_status": 500,
                "error": "unexpected status 500",
            },
        ) as mock_smoke,
    ):
        report = run_verify_runtime(include_smoke=True)

    assert report["status"] == "degraded"
    assert report["read_only"] is False
    assert report["include_smoke"] is True
    assert report["checks"]["smoke_ok"] is False
    mock_smoke.assert_called_once_with()


def test_cli_verify_runtime_json_output(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_verify_runtime",
        return_value={
            "status": "ok",
            "read_only": True,
            "include_smoke": False,
            "health_url": "http://127.0.0.1:9199/health/details",
            "checks": {
                "doctor_ok": True,
                "policy_check_ok": True,
                "health_details_reachable": True,
                "runtime_wiring_current": True,
                "smoke_ok": True,
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
            "smoke": None,
        },
    ) as mock_verify:
        exit_code = main(["verify-runtime", "--json"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"read_only": true' in captured.out
    mock_verify.assert_called_once_with(include_smoke=False)


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
            "retention": {"enabled": True, "interval_minutes": 60, "max_events": 2000, "keep_archives": 10},
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
                "smoke_ok": True,
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
    mock_verify.assert_called_once_with(include_smoke=True)


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
