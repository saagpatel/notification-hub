"""Tests for operator diagnostics and doctor output."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from pytest import MonkeyPatch

import notification_hub.config as config_mod
from notification_hub.diagnostics import (
    collect_doctor_report,
    collect_runtime_readiness,
    collect_runtime_wiring,
)
from notification_hub.durable_inbox import (
    claim_next_due_event,
    enqueue_event,
    record_processing_failure,
)
from notification_hub.models import StoredEvent


def test_collect_runtime_readiness_reports_config_and_paths() -> None:
    with (
        patch("notification_hub.diagnostics.channels_mod.has_push_notifier", return_value=True),
        patch(
            "notification_hub.diagnostics.config_mod.has_slack_webhook_configured",
            return_value=False,
        ),
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
        patch(
            "notification_hub.diagnostics.config_mod.analyze_policy_config",
            return_value=("w1", "w2", "w3"),
        ),
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
    durable_inbox = data["durable_inbox"]
    assert isinstance(durable_inbox, dict)
    assert durable_inbox["status"] == "ok"


def test_collect_doctor_report_degrades_on_dead_lettered_events() -> None:
    enqueue_event(
        StoredEvent(
            event_id="dead1",
            source="codex",
            level="info",
            title="Dead letter",
            body="This event exhausted retries.",
            project="notification-hub",
            classified_level="info",
        ),
        max_attempts=1,
    )
    claimed = claim_next_due_event()
    assert claimed is not None
    record_processing_failure(claimed, RuntimeError("boom"))

    with patch(
        "notification_hub.diagnostics.httpx.get",
        side_effect=httpx.ConnectError("offline"),
    ):
        report = collect_doctor_report()

    checks = report["checks"]
    durable_inbox = report["durable_inbox"]
    assert isinstance(checks, dict)
    assert isinstance(durable_inbox, dict)
    assert report["status"] == "degraded"
    assert checks["durable_inbox_ok"] is False
    assert durable_inbox["dead_letter_count"] == 1


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

    assert isinstance(report, dict)
    local_api = report["local_api"]
    checks = report["checks"]
    assert isinstance(local_api, dict)
    assert isinstance(checks, dict)
    assert report["status"] == "degraded"
    assert checks["local_api_healthy"] is False
    assert local_api["error"] == "diagnostic check failed; inspect local logs for details"


def test_collect_doctor_report_handles_local_api_os_failure() -> None:
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
                    "exists": True,
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
            side_effect=OSError("missing cert file"),
        ),
    ):
        report = collect_doctor_report()

    local_api = report["local_api"]
    checks = report["checks"]
    assert isinstance(local_api, dict)
    assert isinstance(checks, dict)
    assert report["status"] == "degraded"
    assert local_api["reachable"] is False
    assert local_api["error"] == "diagnostic check failed; inspect local logs for details"
    assert checks["local_api_healthy"] is False
