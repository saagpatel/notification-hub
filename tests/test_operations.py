"""Tests for smoke and retention operator actions."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

import notification_hub.operations as ops_mod
from notification_hub.config import (
    ClassificationPolicy,
    PolicyConfig,
    RetentionPolicy,
    RoutingPolicy,
    RoutingRule,
    SuppressionPolicy,
)
from notification_hub.models import StoredEvent
from notification_hub.operations import (
    bootstrap_policy_config,
    run_logs,
    run_policy_check,
    run_retention,
    run_smoke_check,
)


def test_smoke_check_reports_success_when_event_hits_log() -> None:
    response = MagicMock()
    response.status_code = 201
    response.json.return_value = {"event_id": "abc123"}

    with (
        patch("notification_hub.operations.httpx.post", return_value=response),
        patch(
            "notification_hub.operations.read_jsonl",
            return_value=[StoredEvent(source="codex", level="info", title="x", body="y", event_id="abc123")],
        ),
    ):
        report = run_smoke_check()

    assert report["status"] == "ok"
    assert report["event_id"] == "abc123"
    assert report["log_verified"] is True


def test_smoke_check_reports_http_failure() -> None:
    with patch(
        "notification_hub.operations.httpx.post",
        side_effect=httpx.ConnectError("boom"),
    ):
        report = run_smoke_check()

    assert report["status"] == "degraded"
    assert report["response_status"] is None
    assert report["event_id"] is None


def test_logs_report_tails_events_and_daemon_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events_dir = tmp_path / "notification-hub"
    events_dir.mkdir()
    events_log = events_dir / "events.jsonl"
    events = [
        StoredEvent(
            event_id=f"id-{i}",
            source="codex",
            level="info",
            title=f"title {i}",
            body=f"body {i}",
            project="notification-hub",
        )
        for i in range(3)
    ]
    events_log.write_text("\n".join(event.model_dump_json() for event in events) + "\n", encoding="utf-8")
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    stdout_log.write_text("out 1\nout 2\nout 3\n", encoding="utf-8")
    stderr_log.write_text("err 1\nerr 2\nerr 3\n", encoding="utf-8")

    monkeypatch.setattr(ops_mod, "EVENTS_LOG", events_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDOUT_LOG", stdout_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDERR_LOG", stderr_log)

    report = run_logs(events=2, lines=2)

    assert report["status"] == "ok"
    assert [event["event_id"] for event in report["recent_events"]] == ["id-1", "id-2"]
    assert report["stdout_tail"] == ["out 2", "out 3"]
    assert report["stderr_tail"] == ["err 2", "err 3"]
    assert report["missing_paths"] == []


def test_logs_report_degrades_on_invalid_event_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events_log = tmp_path / "events.jsonl"
    events_log.write_text("{not-json}\n", encoding="utf-8")
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    stdout_log.write_text("", encoding="utf-8")
    stderr_log.write_text("", encoding="utf-8")

    monkeypatch.setattr(ops_mod, "EVENTS_LOG", events_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDOUT_LOG", stdout_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDERR_LOG", stderr_log)

    report = run_logs()

    assert report["status"] == "degraded"
    assert report["recent_events"] == []
    assert report["error"] is not None


def test_logs_report_handles_zero_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events_log = tmp_path / "events.jsonl"
    events_log.write_text(
        StoredEvent(source="codex", level="info", title="title", body="body").model_dump_json() + "\n",
        encoding="utf-8",
    )
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    stdout_log.write_text("out\n", encoding="utf-8")
    stderr_log.write_text("err\n", encoding="utf-8")

    monkeypatch.setattr(ops_mod, "EVENTS_LOG", events_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDOUT_LOG", stdout_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDERR_LOG", stderr_log)

    report = run_logs(events=0, lines=0)

    assert report["status"] == "ok"
    assert report["recent_events"] == []
    assert report["stdout_tail"] == []
    assert report["stderr_tail"] == []


def test_retention_noop_when_log_missing() -> None:
    report = run_retention(max_events=10, keep_archives=2)

    assert report["status"] == "ok"
    assert report["rotated"] is False
    assert report["events_before"] == 0


def test_retention_archives_older_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events_dir = tmp_path / "notification-hub"
    events_dir.mkdir()
    events_log = events_dir / "events.jsonl"
    lines = [
        json.dumps({"event_id": f"id-{i}", "source": "codex", "level": "info", "title": "t", "body": "b"})
        + "\n"
        for i in range(5)
    ]
    events_log.write_text("".join(lines), encoding="utf-8")

    monkeypatch.setattr(ops_mod, "EVENTS_DIR", events_dir)
    monkeypatch.setattr(ops_mod, "EVENTS_LOG", events_log)

    report = run_retention(max_events=2, keep_archives=5)

    assert report["rotated"] is True
    assert report["events_before"] == 5
    assert report["events_after"] == 2
    assert report["archived_events"] == 3
    archive_path = report["archive_path"]
    assert isinstance(archive_path, str)
    assert Path(archive_path).exists()
    assert len(events_log.read_text(encoding="utf-8").splitlines()) == 2


def test_bootstrap_policy_config_copies_example(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    example_path = tmp_path / "policy.example.toml"
    config_path = tmp_path / "config" / "config.toml"
    example_path.write_text("[classifier]\nurgent_keywords = [\"database down\"]\n", encoding="utf-8")

    monkeypatch.setattr(ops_mod, "EXAMPLE_POLICY_CONFIG", example_path)
    monkeypatch.setattr(ops_mod, "POLICY_CONFIG", config_path)

    report = bootstrap_policy_config()

    assert report["status"] == "ok"
    assert report["copied"] is True
    assert config_path.read_text(encoding="utf-8") == example_path.read_text(encoding="utf-8")
    assert oct(config_path.stat().st_mode & 0o777) == "0o600"


def test_bootstrap_policy_config_noop_without_force(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    example_path = tmp_path / "policy.example.toml"
    config_path = tmp_path / "config" / "config.toml"
    config_path.parent.mkdir(parents=True)
    example_path.write_text("[classifier]\nurgent_keywords = [\"database down\"]\n", encoding="utf-8")
    config_path.write_text("[classifier]\nurgent_keywords = [\"keep me\"]\n", encoding="utf-8")

    monkeypatch.setattr(ops_mod, "EXAMPLE_POLICY_CONFIG", example_path)
    monkeypatch.setattr(ops_mod, "POLICY_CONFIG", config_path)

    report = bootstrap_policy_config()

    assert report["status"] == "ok"
    assert report["copied"] is False
    assert "keep me" in config_path.read_text(encoding="utf-8")


def test_policy_check_reports_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = PolicyConfig(
        config_found=True,
        classification=ClassificationPolicy(
            urgent_keywords=("ship it",),
            normal_keywords=("ship it",),
            info_keywords=(),
        ),
        suppression=SuppressionPolicy(),
        routing=RoutingPolicy(
            rules=(
                RoutingRule(source="codex"),
            )
        ),
    )

    monkeypatch.setattr(ops_mod, "get_policy_config", lambda: policy)
    def _warnings_for_policy(_policy: PolicyConfig) -> tuple[str, ...]:
        return ("warning one", "warning two")

    monkeypatch.setattr(ops_mod, "analyze_policy_config", _warnings_for_policy)

    report = run_policy_check()

    assert report["status"] == "warn"
    assert report["warning_count"] == 2
    assert report["suggestion_count"] == 2
    assert report["warnings"] == ["warning one", "warning two"]
    assert len(report["suggestions"]) == 2
    assert all(isinstance(suggestion, str) and suggestion for suggestion in report["suggestions"])


def test_policy_check_reports_degraded_on_load_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = PolicyConfig(
        config_found=True,
        load_error="bad toml",
        classification=ClassificationPolicy(),
        suppression=SuppressionPolicy(),
        routing=RoutingPolicy(),
    )

    monkeypatch.setattr(ops_mod, "get_policy_config", lambda: policy)
    def _no_warnings(_policy: PolicyConfig) -> tuple[str, ...]:
        return ()

    monkeypatch.setattr(ops_mod, "analyze_policy_config", _no_warnings)

    report = run_policy_check()

    assert report["status"] == "degraded"
    assert report["load_error"] == "bad toml"
    assert report["suggestion_count"] == 0
    assert report["suggestions"] == []


def test_policy_check_maps_shadowed_rule_to_fix_suggestion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = PolicyConfig(
        config_found=True,
        classification=ClassificationPolicy(),
        suppression=SuppressionPolicy(),
        routing=RoutingPolicy(
            rules=(
                RoutingRule(source="codex"),
                RoutingRule(source="codex", project="notification-hub", disable_slack=True),
            )
        ),
    )

    monkeypatch.setattr(ops_mod, "get_policy_config", lambda: policy)

    report = run_policy_check()

    assert report["status"] == "warn"
    assert any("Move the narrower rule earlier" in suggestion for suggestion in report["suggestions"])


def test_policy_check_maps_shared_priority_warning_to_fix_suggestion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = PolicyConfig(
        config_found=True,
        classification=ClassificationPolicy(),
        suppression=SuppressionPolicy(),
        routing=RoutingPolicy(
            rules=(
                RoutingRule(project="notification-hub", disable_slack=True, priority=10),
                RoutingRule(project_prefix="notification-", force_level="normal", priority=10),
            )
        ),
    )

    monkeypatch.setattr(ops_mod, "get_policy_config", lambda: policy)

    report = run_policy_check()

    assert report["status"] == "warn"
    assert any("Give the more important rule a higher `priority`" in suggestion for suggestion in report["suggestions"])


def test_policy_check_maps_retention_and_continue_warnings_to_fix_suggestions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = PolicyConfig(
        config_found=True,
        classification=ClassificationPolicy(),
        suppression=SuppressionPolicy(),
        retention=RetentionPolicy(enabled=False),
        routing=RoutingPolicy(
            rules=(
                RoutingRule(
                    project="notification-hub",
                    disable_slack=True,
                    continue_matching=True,
                ),
            )
        ),
    )

    monkeypatch.setattr(ops_mod, "get_policy_config", lambda: policy)

    report = run_policy_check()

    assert report["status"] == "warn"
    assert any("Re-enable retention" in suggestion for suggestion in report["suggestions"])
    assert any("Remove `continue_matching` on the final rule" in suggestion for suggestion in report["suggestions"])


def test_policy_check_maps_redundant_continue_chain_warning_to_fix_suggestion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = PolicyConfig(
        config_found=True,
        classification=ClassificationPolicy(),
        suppression=SuppressionPolicy(),
        routing=RoutingPolicy(
            rules=(
                RoutingRule(
                    project_prefix="notification-",
                    disable_slack=True,
                    continue_matching=True,
                ),
                RoutingRule(
                    project="notification-hub",
                    disable_slack=True,
                ),
            )
        ),
    )

    monkeypatch.setattr(ops_mod, "get_policy_config", lambda: policy)

    report = run_policy_check()

    assert report["status"] == "warn"
    assert any("Delete the redundant rule" in suggestion for suggestion in report["suggestions"])
