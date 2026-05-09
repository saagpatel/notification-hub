"""Tests for operator diagnostics and doctor output."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from _pytest.capture import CaptureFixture
from pytest import MonkeyPatch

from notification_hub.cli import (
    burn_in_main,
    bootstrap_config_main,
    coordination_snapshot_main,
    delivery_check_main,
    doctor_main,
    explain_main,
    inbox_main,
    logs_main,
    main,
    personal_ops_actions_main,
    personal_ops_import_main,
    policy_check_main,
    retention_main,
    smoke_main,
    status_main,
    validate_action_package_main,
    verify_runtime_main,
)
import notification_hub.config as config_mod
from notification_hub.diagnostics import (
    collect_doctor_report,
    collect_runtime_readiness,
    collect_runtime_wiring,
)
from notification_hub.operations import run_delivery_check, run_status, run_verify_runtime


def _coordination_snapshot_report() -> dict[str, object]:
    return {
        "status": "ok",
        "schema_version": "notification-hub.coordination_snapshot.v1",
        "generated_at": "2026-05-09T00:00:00+00:00",
        "bridge_target_system": "codex",
        "bridge_snapshot_date": "2026-05-09",
        "bridge_snapshot": {
            "active_projects": {},
            "coordination": {"events_seen": 1},
            "runtime": {"status": "ok"},
            "follow_up": ["No immediate operator action needed."],
        },
        "bridge_save": {
            "attempted": False,
            "status": "not_requested",
            "db_path": None,
            "snapshot_id": None,
            "snapshot_date": None,
            "error": None,
        },
        "inbox": {
            "status": "ok",
            "hours": 12,
            "events_seen": 1,
            "needs_attention": [],
            "waiting_or_blocked": [],
            "ready": [],
            "completed": [],
            "rollups": [],
            "noise_candidates": [],
            "error": None,
        },
        "runtime_status": {
            "status": "ok",
            "health_url": "http://127.0.0.1:9199/health/details",
            "daemon_reachable": True,
            "watcher_active": True,
            "events_processed": 12,
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
        },
        "follow_up": ["No immediate operator action needed."],
        "error": None,
    }


def _personal_ops_action_export_report() -> dict[str, object]:
    return {
        "status": "ok",
        "schema_version": "notification-hub.personal_ops_action_export.v1",
        "generated_at": "2026-05-09T00:00:00+00:00",
        "hours": 12,
        "actions": [
            {
                "action_id": "notification-hub:personal-ops:mail:waiting_on_user:approval-requested",
                "source": "personal-ops",
                "project": "mail",
                "intent": "waiting_on_user",
                "priority": "high",
                "state": "waiting",
                "title": "Approval Requested",
                "summary": "2 repeated personal-ops events: Console reply needed",
                "suggested_next_action": "Review the waiting item and approve, reply, or dismiss it.",
                "evidence_event_id": "abc123",
                "evidence_timestamp": "2026-05-09T00:00:00+00:00",
                "count": 2,
            }
        ],
        "review_package": {
            "requested": False,
            "status": "not_requested",
            "path": None,
            "error": None,
        },
        "inbox": {
            "status": "ok",
            "hours": 12,
            "events_seen": 2,
            "needs_attention": [],
            "waiting_or_blocked": [],
            "ready": [],
            "completed": [],
            "rollups": [],
            "noise_candidates": [],
            "error": None,
        },
        "error": None,
    }


def _burn_in_report(
    *,
    status: str = "ok",
    slack_delivery_failure_count: int = 0,
) -> dict[str, object]:
    return {
        "status": "ok",
        "minutes": 10,
        "events_seen": 0,
        "accepted_event_posts": 0,
        "rejected_event_posts": 0,
        "validation_error_count": 0,
        "health": {
            "accepted_event_posts": 0,
            "rejected_event_posts": 0,
            "validation_error_count": 0,
            "slack_delivery_failure_count": slack_delivery_failure_count,
            "status": status,
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
            "slack_delivery_failure_count": slack_delivery_failure_count,
            "recent_slack_delivery_failures": [],
        },
        "error": None,
    }


def _import_queue_health(*, queued_count: int = 0) -> dict[str, object]:
    return {
        "status": "warn" if queued_count else "ok",
        "queue_path": "/tmp/personal-ops-import-queue.jsonl",
        "total_count": queued_count,
        "queued_count": queued_count,
        "reviewed_count": 0,
        "rejected_count": 0,
        "snoozed_count": 0,
        "superseded_count": 0,
        "promoted_count": 0,
        "promoted_pending_count": 0,
        "promoted_accepted_count": 0,
        "promoted_rejected_count": 0,
        "promoted_ignored_count": 0,
        "needs_review": queued_count > 0,
        "oldest_queued_at": "2026-05-09T10:00:00+00:00" if queued_count else None,
        "oldest_queued_age_seconds": 60.0 if queued_count else None,
        "next_action": "Review queued personal-ops handoff items." if queued_count else "No queued personal-ops handoff items.",
    }


def _delivery_check_report(
    *,
    status: str = "ok",
    verify_slack: bool = False,
    verify_push: bool = False,
) -> dict[str, object]:
    return {
        "status": status,
        "verify_slack": verify_slack,
        "verify_push": verify_push,
        "slack_ok": status == "ok" if verify_slack else None,
        "push_ok": status == "ok" if verify_push else None,
        "event_id": "delivery123",
        "error": None if status == "ok" else "delivery failed",
    }


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
                "recent_runtime_health_ok": True,
                "smoke_ok": True,
                "delivery_check_ok": True,
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
            "burn_in": _burn_in_report(),
            "import_queue": _import_queue_health(),
            "delivery_check": None,
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
        "slack_delivery_failures": 0,
        "import_queue": _import_queue_health(),
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
                "recent_runtime_health_ok": True,
                "smoke_ok": True,
                "delivery_check_ok": True,
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
            "burn_in": _burn_in_report(),
            "import_queue": _import_queue_health(),
            "delivery_check": None,
            "smoke": None,
        },
    ):
        report = run_status()

    assert report["status"] == "degraded"
    assert report["runtime_wiring_current"] is False
    assert report["next_action"] == "Refresh runtime templates from ops/, then run verify-runtime again."


def test_run_status_suggests_slack_delivery_investigation() -> None:
    with patch(
        "notification_hub.operations.run_verify_runtime",
        return_value={
            "status": "degraded",
            "read_only": True,
            "include_smoke": False,
            "health_url": "http://127.0.0.1:9199/health/details",
            "checks": {
                "doctor_ok": True,
                "policy_check_ok": True,
                "health_details_reachable": True,
                "runtime_wiring_current": True,
                "recent_runtime_health_ok": False,
                "smoke_ok": True,
                "delivery_check_ok": True,
            },
            "runtime_wiring": {"launch_agent_matches_template": True},
            "doctor": {
                "status": "ok",
                "checks": {"policy_load_ok": True, "runtime_wiring_current": True},
                "config": {"exists": True},
                "delivery": {"slack_webhook_configured": True},
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
            "burn_in": _burn_in_report(status="degraded", slack_delivery_failure_count=3),
            "import_queue": _import_queue_health(),
            "smoke": None,
        },
    ):
        report = run_status()

    assert report["status"] == "degraded"
    assert report["slack_delivery_failures"] == 3
    assert (
        report["next_action"]
        == "Inspect notification-hub logs for Slack delivery failures, then verify Slack transport."
    )


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
            "slack_delivery_failures": 0,
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
                "slack_delivery_failure_count": 0,
                "recent_slack_delivery_failures": [],
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
        patch("notification_hub.operations.run_burn_in", return_value=_burn_in_report()),
        patch("notification_hub.operations.run_delivery_check") as mock_delivery_check,
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
        "recent_runtime_health_ok": True,
        "smoke_ok": True,
        "delivery_check_ok": True,
    }
    assert report["delivery_check"] is None
    mock_delivery_check.assert_not_called()
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
        patch("notification_hub.operations.run_burn_in", return_value=_burn_in_report()),
        patch("notification_hub.operations.run_delivery_check") as mock_delivery_check,
    ):
        report = run_verify_runtime()

    assert report["status"] == "degraded"
    assert report["checks"]["policy_check_ok"] is False
    mock_delivery_check.assert_not_called()


def test_run_verify_runtime_reports_degraded_recent_runtime_health() -> None:
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
            "notification_hub.operations.run_burn_in",
            return_value=_burn_in_report(status="degraded", slack_delivery_failure_count=2),
        ),
        patch("notification_hub.operations.run_delivery_check") as mock_delivery_check,
    ):
        report = run_verify_runtime()

    assert report["status"] == "degraded"
    assert report["checks"]["recent_runtime_health_ok"] is False
    assert report["burn_in"]["health"]["slack_delivery_failure_count"] == 2
    mock_delivery_check.assert_not_called()


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
        patch("notification_hub.operations.run_burn_in", return_value=_burn_in_report()),
        patch("notification_hub.operations.run_delivery_check") as mock_delivery_check,
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
    mock_delivery_check.assert_not_called()
    mock_smoke.assert_called_once_with()


def test_run_verify_runtime_delivery_check_is_opt_in() -> None:
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
        patch("notification_hub.operations.run_burn_in", return_value=_burn_in_report()),
        patch(
            "notification_hub.operations.run_delivery_check",
            return_value=_delivery_check_report(status="degraded", verify_slack=True),
        ) as mock_delivery_check,
    ):
        report = run_verify_runtime(verify_slack=True)

    assert report["status"] == "degraded"
    assert report["read_only"] is False
    assert report["checks"]["delivery_check_ok"] is False
    assert report["delivery_check"] is not None
    mock_delivery_check.assert_called_once_with(verify_slack=True, verify_push=False)


def test_run_delivery_check_reports_transport_results() -> None:
    with (
        patch("notification_hub.operations.send_slack", return_value=True) as mock_slack,
        patch("notification_hub.operations.send_push", return_value=False) as mock_push,
    ):
        report = run_delivery_check(verify_slack=True, verify_push=True)

    assert report["status"] == "degraded"
    assert report["verify_slack"] is True
    assert report["verify_push"] is True
    assert report["slack_ok"] is True
    assert report["push_ok"] is False
    assert report["event_id"] is not None
    assert report["error"] == "Push delivery check failed"
    mock_slack.assert_called_once()
    mock_push.assert_called_once()


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
            "burn_in": _burn_in_report(),
            "delivery_check": None,
            "smoke": None,
        },
    ) as mock_verify:
        exit_code = main(["verify-runtime", "--json"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"read_only": true' in captured.out
    mock_verify.assert_called_once_with(include_smoke=False, verify_slack=False, verify_push=False)


def test_cli_verify_runtime_forwards_delivery_flags(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_verify_runtime",
        return_value={
            "status": "ok",
            "read_only": False,
            "include_smoke": False,
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
            "burn_in": _burn_in_report(),
            "delivery_check": _delivery_check_report(verify_slack=True, verify_push=True),
            "smoke": None,
        },
    ) as mock_verify:
        exit_code = main(["verify-runtime", "--json", "--verify-slack", "--verify-push"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"delivery_check"' in captured.out
    mock_verify.assert_called_once_with(include_smoke=False, verify_slack=True, verify_push=True)


def test_cli_delivery_check_requires_channel(capsys: CaptureFixture[str]) -> None:
    with patch("notification_hub.cli.run_delivery_check") as mock_delivery_check:
        try:
            main(["delivery-check"])
        except SystemExit as exc:
            exit_code = exc.code
        else:
            exit_code = 0

    output = capsys.readouterr()
    assert exit_code == 2
    assert "requires --slack and/or --push" in output.err
    mock_delivery_check.assert_not_called()


def test_cli_delivery_check_json_output(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_delivery_check",
        return_value=_delivery_check_report(verify_slack=True),
    ) as mock_delivery_check:
        exit_code = main(["delivery-check", "--json", "--slack"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"slack_ok": true' in captured.out
    mock_delivery_check.assert_called_once_with(verify_slack=True, verify_push=False)


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
        return_value=_coordination_snapshot_report(),
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


def test_cli_coordination_snapshot_writes_output(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    output_path = tmp_path / "snapshot.json"
    with patch(
        "notification_hub.cli.run_coordination_snapshot",
        return_value=_coordination_snapshot_report(),
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
        return_value=_coordination_snapshot_report(),
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
        return_value=_personal_ops_action_export_report(),
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


def test_cli_personal_ops_actions_writes_output(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    output_path = tmp_path / "actions.json"
    with patch(
        "notification_hub.cli.run_personal_ops_action_export",
        return_value=_personal_ops_action_export_report(),
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
        return_value=_personal_ops_action_export_report(),
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


def test_cli_validate_action_package_json_output(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    package_path = tmp_path / "actions.json"
    package_path.write_text(json.dumps(_personal_ops_action_export_report()), encoding="utf-8")

    exit_code = main(["validate-action-package", str(package_path), "--json"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"valid_action_count": 1' in captured.out


def test_validate_action_package_wrapper_forwards_path(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    package_path = tmp_path / "actions.json"
    package_path.write_text(json.dumps(_personal_ops_action_export_report()), encoding="utf-8")

    exit_code = validate_action_package_main([str(package_path), "--json"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert '"status": "ok"' in output.out


def test_cli_personal_ops_import_json_output(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    package_path = tmp_path / "actions.json"
    package_path.write_text(json.dumps(_personal_ops_action_export_report()), encoding="utf-8")

    exit_code = main(["personal-ops-import", str(package_path), "--json"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"applied": false' in captured.out
    assert '"valid_action_count": 1' in captured.out


def test_personal_ops_import_wrapper_forwards_path(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    package_path = tmp_path / "actions.json"
    package_path.write_text(json.dumps(_personal_ops_action_export_report()), encoding="utf-8")

    exit_code = personal_ops_import_main([str(package_path), "--json"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert '"applied": false' in output.out


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
            "slack_delivery_failures": 0,
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
            "burn_in": _burn_in_report(),
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
        return_value=_delivery_check_report(verify_slack=True, verify_push=True),
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
        return_value=_coordination_snapshot_report(),
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


def test_personal_ops_actions_wrapper_forwards_flags(capsys: CaptureFixture[str]) -> None:
    with patch(
        "notification_hub.cli.run_personal_ops_action_export",
        return_value=_personal_ops_action_export_report(),
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
