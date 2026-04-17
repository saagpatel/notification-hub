"""Tests for operator diagnostics and doctor output."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
from _pytest.capture import CaptureFixture

from notification_hub.cli import main
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
            ),
        ),
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
            "config": {"path": "/tmp/config.toml", "load_error": None},
            "local_api": {"url": "http://127.0.0.1:9199/health/details"},
        },
    ):
        exit_code = main(["--json"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"status": "ok"' in captured.out
