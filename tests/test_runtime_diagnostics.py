"""Tests for runtime status, verification, and delivery diagnostics."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from _pytest.capture import CaptureFixture

from notification_hub.cli import main
from notification_hub.operations import run_delivery_check, run_status, run_verify_runtime


def _burn_in_report(
    *,
    status: str = "ok",
    slack_delivery_failure_count: int = 0,
    visible_slack_delivery_failure_count: int | None = None,
) -> dict[str, object]:
    visible_slack_delivery_failure_count = (
        slack_delivery_failure_count
        if visible_slack_delivery_failure_count is None
        else visible_slack_delivery_failure_count
    )
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
            "slack_delivery_failure_count": slack_delivery_failure_count,
            "recent_slack_delivery_failures": [],
        },
        "visible_daemon_summary": {
            "access_status_counts": {},
            "accepted_event_posts": 0,
            "rejected_event_posts": 0,
            "validation_error_count": 0,
            "recent_validation_errors": [],
            "slack_delivery_failure_count": visible_slack_delivery_failure_count,
            "recent_slack_delivery_failures": [],
        },
        "error": None,
    }


def _import_queue_health(
    *,
    queued_count: int = 0,
    promoted_pending_count: int = 0,
    promoted_pending_stale_count: int = 0,
) -> dict[str, object]:
    needs_queue_attention = queued_count > 0 or promoted_pending_count > 0
    if queued_count:
        next_action = "Review queued personal-ops handoff items."
    elif promoted_pending_stale_count:
        next_action = (
            "Resolve the matching personal-ops suggestion, record the promotion outcome, "
            "then rerun notification-hub personal-ops-queue-health."
        )
    elif promoted_pending_count:
        next_action = "Resolve promoted personal-ops handoff outcomes."
    else:
        next_action = "No queued personal-ops handoff items."
    return {
        "status": "warn" if needs_queue_attention else "ok",
        "queue_path": "/tmp/personal-ops-import-queue.jsonl",
        "total_count": queued_count + promoted_pending_count,
        "queued_count": queued_count,
        "reviewed_count": 0,
        "rejected_count": 0,
        "snoozed_count": 0,
        "superseded_count": 0,
        "promoted_count": promoted_pending_count,
        "promoted_pending_count": promoted_pending_count,
        "promoted_pending_stale_count": promoted_pending_stale_count,
        "promoted_accepted_count": 0,
        "promoted_rejected_count": 0,
        "promoted_ignored_count": 0,
        "needs_outcome_sync": promoted_pending_count > 0,
        "needs_review": queued_count > 0,
        "oldest_queued_at": "2026-05-09T10:00:00+00:00" if queued_count else None,
        "oldest_queued_age_seconds": 60.0 if queued_count else None,
        "oldest_promoted_pending_at": "2026-05-09T08:00:00+00:00"
        if promoted_pending_count
        else None,
        "oldest_promoted_pending_age_seconds": 7200.0 if promoted_pending_count else None,
        "stale_after_hours": 4.0,
        "next_action": next_action,
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


def _delivery_check_state(
    *,
    last_slack_ok_at: str | None = None,
    last_slack_event_id: str | None = None,
    last_push_ok_at: str | None = None,
    last_push_event_id: str | None = None,
) -> dict[str, str | None]:
    return {
        "last_slack_ok_at": last_slack_ok_at,
        "last_slack_event_id": last_slack_event_id,
        "last_push_ok_at": last_push_ok_at,
        "last_push_event_id": last_push_event_id,
    }


def test_run_status_summarizes_healthy_runtime() -> None:
    with (
        patch(
            "notification_hub.operations._read_delivery_check_state",
            return_value=_delivery_check_state(),
        ),
        patch(
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
                    "durable_inbox_ok": True,
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
        ),
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
        "durable_inbox": {},
        "runtime_wiring_current": True,
        "push_notifier_available": True,
        "slack_configured": True,
        "slack_delivery_failures": 0,
        "visible_slack_delivery_failures": 0,
        "latest_delivery_check": _delivery_check_state(),
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
    assert (
        report["next_action"]
        == "Refresh runtime templates from ops/, then run verify-runtime again."
    )


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


def test_run_status_surfaces_visible_stale_slack_delivery_failures() -> None:
    with (
        patch(
            "notification_hub.operations._read_delivery_check_state",
            return_value=_delivery_check_state(),
        ),
        patch(
            "notification_hub.operations.run_verify_runtime",
            return_value={
                "status": "ok",
                "health_url": "http://127.0.0.1:9199/health/details",
                "checks": {
                    "health_details_reachable": True,
                    "runtime_wiring_current": True,
                    "recent_runtime_health_ok": True,
                    "policy_check_ok": True,
                },
                "doctor": {
                    "checks": {"policy_load_ok": True},
                    "config": {"exists": True},
                    "delivery": {
                        "push_notifier_available": True,
                        "slack_webhook_configured": True,
                    },
                    "local_api": {"payload": {}},
                },
                "policy_check": {"warning_count": 0},
                "burn_in": _burn_in_report(visible_slack_delivery_failure_count=4),
                "import_queue": _import_queue_health(),
            },
        ),
    ):
        report = run_status()

    assert report["status"] == "ok"
    assert report["slack_delivery_failures"] == 0
    assert report["visible_slack_delivery_failures"] == 4
    assert (
        report["next_action"]
        == "Review visible historical Slack delivery failures, then run a Slack "
        "delivery check only with approval."
    )


def test_run_status_uses_successful_slack_delivery_check_for_historical_failures() -> None:
    latest_delivery_check = _delivery_check_state(
        last_slack_ok_at=datetime.now(UTC).isoformat(),
        last_slack_event_id="delivery123",
    )
    with (
        patch(
            "notification_hub.operations._read_delivery_check_state",
            return_value=latest_delivery_check,
        ),
        patch(
            "notification_hub.operations.run_verify_runtime",
            return_value={
                "status": "ok",
                "health_url": "http://127.0.0.1:9199/health/details",
                "checks": {
                    "health_details_reachable": True,
                    "runtime_wiring_current": True,
                    "recent_runtime_health_ok": True,
                    "policy_check_ok": True,
                },
                "doctor": {
                    "checks": {"policy_load_ok": True},
                    "config": {"exists": True},
                    "delivery": {
                        "push_notifier_available": True,
                        "slack_webhook_configured": True,
                    },
                    "local_api": {"payload": {}},
                },
                "policy_check": {"warning_count": 0},
                "burn_in": _burn_in_report(visible_slack_delivery_failure_count=4),
                "import_queue": _import_queue_health(),
            },
        ),
    ):
        report = run_status()

    assert report["latest_delivery_check"] == latest_delivery_check
    assert (
        report["next_action"]
        == "Recent Slack delivery was verified; review historical Slack failures "
        "only if root-cause detail is needed."
    )


def test_run_status_does_not_treat_stale_slack_delivery_check_as_current() -> None:
    latest_delivery_check = _delivery_check_state(
        last_slack_ok_at=(datetime.now(UTC) - timedelta(days=3)).isoformat(),
        last_slack_event_id="delivery123",
    )
    with (
        patch(
            "notification_hub.operations._read_delivery_check_state",
            return_value=latest_delivery_check,
        ),
        patch(
            "notification_hub.operations.run_verify_runtime",
            return_value={
                "status": "ok",
                "health_url": "http://127.0.0.1:9199/health/details",
                "checks": {
                    "health_details_reachable": True,
                    "runtime_wiring_current": True,
                    "recent_runtime_health_ok": True,
                    "policy_check_ok": True,
                },
                "doctor": {
                    "checks": {"policy_load_ok": True},
                    "config": {"exists": True},
                    "delivery": {
                        "push_notifier_available": True,
                        "slack_webhook_configured": True,
                    },
                    "local_api": {"payload": {}},
                },
                "policy_check": {"warning_count": 0},
                "burn_in": _burn_in_report(visible_slack_delivery_failure_count=4),
                "import_queue": _import_queue_health(),
            },
        ),
    ):
        report = run_status()

    assert report["latest_delivery_check"] == latest_delivery_check
    assert (
        report["next_action"]
        == "Review visible historical Slack delivery failures, then run a Slack "
        "delivery check only with approval."
    )


def test_run_status_suggests_import_queue_outcome_sync() -> None:
    pending_health = _import_queue_health(promoted_pending_count=1, promoted_pending_stale_count=1)
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
                "recent_runtime_health_ok": True,
                "import_queue_ok": False,
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
            "burn_in": _burn_in_report(),
            "import_queue": pending_health,
            "delivery_check": None,
            "smoke": None,
        },
    ):
        report = run_status()

    assert report["status"] == "degraded"
    assert report["import_queue"]["needs_outcome_sync"] is True
    assert report["next_action"] == pending_health["next_action"]


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
            "visible_slack_delivery_failures": 0,
            "latest_delivery_check": _delivery_check_state(),
            "next_action": "No action needed.",
        },
    ) as mock_status:
        exit_code = main(["status", "--json"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"next_action": "No action needed."' in captured.out
    mock_status.assert_called_once_with()


def test_run_delivery_check_persists_successful_transport_state(tmp_path: Path) -> None:
    state_path = tmp_path / "delivery-check-state.json"
    with (
        patch("notification_hub.operations.live_smoke_authorized", return_value=True),
        patch("notification_hub.operations.DELIVERY_CHECK_STATE", state_path),
        patch("notification_hub.operations.send_slack", return_value=True),
        patch("notification_hub.operations.send_push", return_value=False),
    ):
        report = run_delivery_check(verify_slack=True, verify_push=True)

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert report["status"] == "degraded"
    assert payload["last_slack_event_id"] == report["event_id"]
    assert payload["last_slack_ok_at"] is not None
    assert payload["last_push_event_id"] is None
    assert payload["last_push_ok_at"] is None


def test_run_delivery_check_refuses_without_separate_live_smoke_gates() -> None:
    with (
        patch("notification_hub.operations.live_smoke_authorized", return_value=False),
        patch("notification_hub.operations.send_slack") as mock_slack,
        pytest.raises(PermissionError, match="LIVE_SMOKE"),
    ):
        run_delivery_check(verify_slack=True)
    mock_slack.assert_not_called()


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
        patch(
            "notification_hub.operations.summarize_personal_ops_import_queue",
            return_value=_import_queue_health(),
        ),
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
        "durable_inbox_ok": True,
        "recent_runtime_health_ok": True,
        "import_queue_ok": True,
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
        patch(
            "notification_hub.operations.summarize_personal_ops_import_queue",
            return_value=_import_queue_health(),
        ),
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
        patch(
            "notification_hub.operations.summarize_personal_ops_import_queue",
            return_value=_import_queue_health(),
        ),
        patch("notification_hub.operations.run_delivery_check") as mock_delivery_check,
    ):
        report = run_verify_runtime()

    assert report["status"] == "degraded"
    assert report["checks"]["recent_runtime_health_ok"] is False
    assert report["burn_in"]["health"]["slack_delivery_failure_count"] == 2
    mock_delivery_check.assert_not_called()


def test_run_verify_runtime_reports_import_queue_attention() -> None:
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
            "notification_hub.operations.summarize_personal_ops_import_queue",
            return_value=_import_queue_health(
                promoted_pending_count=1, promoted_pending_stale_count=1
            ),
        ),
    ):
        report = run_verify_runtime()

    assert report["status"] == "degraded"
    assert report["checks"]["import_queue_ok"] is False
    assert report["import_queue"]["needs_outcome_sync"] is True


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
        patch(
            "notification_hub.operations.summarize_personal_ops_import_queue",
            return_value=_import_queue_health(),
        ),
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
            "notification_hub.operations.summarize_personal_ops_import_queue",
            return_value=_import_queue_health(),
        ),
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


def test_run_delivery_check_reports_transport_results(tmp_path: Path) -> None:
    state_path = tmp_path / "delivery-check-state.json"
    with (
        patch("notification_hub.operations.live_smoke_authorized", return_value=True),
        patch("notification_hub.operations.DELIVERY_CHECK_STATE", state_path),
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
