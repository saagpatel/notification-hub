"""Tests for smoke and retention operator actions."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

import notification_hub.operations as ops_mod
from notification_hub.config import (
    ClassificationPolicy,
    NoisePolicy,
    NoiseRule,
    PolicyConfig,
    RetentionPolicy,
    RoutingPolicy,
    RoutingRule,
    SuppressionPolicy,
)
from notification_hub.models import StoredEvent
from notification_hub.operations import (
    bootstrap_policy_config,
    run_burn_in,
    run_coordination_snapshot,
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
            return_value=[
                StoredEvent(source="codex", level="info", title="x", body="y", event_id="abc123")
            ],
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


def test_coordination_snapshot_can_save_to_bridge_db(tmp_path: Path) -> None:
    db_path = tmp_path / "bridge.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE system_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                system TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            );
            CREATE VIRTUAL TABLE content_index USING fts5(
                source_type UNINDEXED,
                source_id UNINDEXED,
                text
            );
            """
        )

    inbox_report: dict[str, object] = {
        "status": "ok",
        "hours": 2,
        "events_seen": 0,
        "needs_attention": [],
        "waiting_or_blocked": [],
        "ready": [],
        "completed": [],
        "rollups": [],
        "noise_candidates": [],
        "error": None,
    }
    status_report = {
        "status": "ok",
        "health_url": "http://127.0.0.1:9199/health/details",
        "daemon_reachable": True,
        "watcher_active": True,
        "events_processed": 10,
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
    }

    with (
        patch("notification_hub.operations.run_inbox", return_value=inbox_report),
        patch("notification_hub.operations.run_status", return_value=status_report),
    ):
        report = run_coordination_snapshot(
            hours=2,
            limit=3,
            save_bridge_db=True,
            bridge_db_path=db_path,
        )

    assert report["status"] == "ok"
    assert report["bridge_save"]["status"] == "ok"
    assert report["bridge_save"]["snapshot_id"] == 1
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT system, snapshot_date, data FROM system_snapshots").fetchone()
    assert row is not None
    assert row[0] == "codex"
    assert row[1] == report["bridge_snapshot_date"]
    assert json.loads(row[2])["runtime"]["status"] == "ok"


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
    events_log.write_text(
        "\n".join(event.model_dump_json() for event in events) + "\n", encoding="utf-8"
    )
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    stdout_log.write_text(
        "\n".join(
            [
                'INFO:     127.0.0.1:1 - "POST /events HTTP/1.1" 201 Created',
                'INFO:     127.0.0.1:2 - "POST /events HTTP/1.1" 422 Unprocessable Entity',
                'INFO:     127.0.0.1:3 - "GET /health/details HTTP/1.1" 200 OK',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    stderr_log.write_text(
        "\n".join(
            [
                "err 1",
                "Rejected event payload from 127.0.0.1: [{'type': 'literal_error'}]",
                "err 3",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(ops_mod, "EVENTS_LOG", events_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDOUT_LOG", stdout_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDERR_LOG", stderr_log)

    report = run_logs(events=2, lines=2)

    assert report["status"] == "ok"
    assert [event["event_id"] for event in report["recent_events"]] == ["id-1", "id-2"]
    assert report["daemon_summary"]["accepted_event_posts"] == 0
    assert report["daemon_summary"]["rejected_event_posts"] == 1
    assert report["daemon_summary"]["validation_error_count"] == 1
    assert report["daemon_summary"]["slack_delivery_failure_count"] == 0
    assert report["stdout_tail"] == [
        'INFO:     127.0.0.1:2 - "POST /events HTTP/1.1" 422 Unprocessable Entity',
        'INFO:     127.0.0.1:3 - "GET /health/details HTTP/1.1" 200 OK',
    ]
    assert report["stderr_tail"] == [
        "Rejected event payload from 127.0.0.1: [{'type': 'literal_error'}]",
        "err 3",
    ]
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


def test_logs_report_counts_validation_errors_since_latest_daemon_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events_log = tmp_path / "events.jsonl"
    events_log.write_text("", encoding="utf-8")
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    stdout_log.write_text("", encoding="utf-8")
    stderr_log.write_text(
        "\n".join(
            [
                "Rejected event payload from 127.0.0.1: [{'type': 'old_error'}]",
                "INFO:     Started server process [123]",
                "INFO:     Waiting for application startup.",
                "INFO:     Application startup complete.",
                "INFO:     Uvicorn running on http://127.0.0.1:9199 (Press CTRL+C to quit)",
                "Rejected event payload from 127.0.0.1: [{'type': 'current_error'}]",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(ops_mod, "EVENTS_LOG", events_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDOUT_LOG", stdout_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDERR_LOG", stderr_log)

    report = run_logs(events=0, lines=20)

    assert report["daemon_summary"]["validation_error_count"] == 1
    assert report["daemon_summary"]["recent_validation_errors"] == [
        "Rejected event payload from 127.0.0.1: [{'type': 'current_error'}]"
    ]
    assert report["daemon_summary"]["slack_delivery_failure_count"] == 0


def test_logs_report_counts_slack_delivery_failures_since_latest_daemon_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events_log = tmp_path / "events.jsonl"
    events_log.write_text("", encoding="utf-8")
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    stdout_log.write_text("", encoding="utf-8")
    stderr_log.write_text(
        "\n".join(
            [
                "Slack send failed for old: [Errno 2] No such file or directory",
                "INFO:     Started server process [123]",
                "INFO:     Waiting for application startup.",
                "INFO:     Application startup complete.",
                "Slack digest failed: [Errno 2] No such file or directory",
                "Failed to flush overflow digest; returning 11 events to buffer",
                "Slack send failed for current: [Errno 2] No such file or directory",
                "Slack delivery failed for current",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(ops_mod, "EVENTS_LOG", events_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDOUT_LOG", stdout_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDERR_LOG", stderr_log)

    report = run_logs(events=0, lines=20)

    assert report["daemon_summary"]["slack_delivery_failure_count"] == 2
    assert report["daemon_summary"]["recent_slack_delivery_failures"] == [
        "Slack digest failed: [Errno 2] No such file or directory",
        "Slack send failed for current: [Errno 2] No such file or directory",
    ]


def test_logs_report_handles_zero_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events_log = tmp_path / "events.jsonl"
    events_log.write_text(
        StoredEvent(source="codex", level="info", title="title", body="body").model_dump_json()
        + "\n",
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


def test_burn_in_reports_repeated_signatures_and_daemon_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events_log = tmp_path / "events.jsonl"
    events = [
        StoredEvent(
            source="personal-ops",
            level="info",
            classified_level="info",
            title="Approval expires soon",
            body="Approval expires soon: review or cancel",
            project="personal-ops",
        ),
        StoredEvent(
            source="personal-ops",
            level="info",
            classified_level="info",
            title="Approval expires soon",
            body="Approval expires soon: review or cancel",
            project="personal-ops",
        ),
        StoredEvent(
            source="codex",
            level="normal",
            classified_level="normal",
            title="Codex finished a turn",
            body="A Codex turn completed.",
            project="notification-hub",
        ),
    ]
    events_log.write_text(
        "\n".join(event.model_dump_json() for event in events) + "\n", encoding="utf-8"
    )
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    stdout_log.write_text(
        "\n".join(
            [
                'INFO:     127.0.0.1:1 - "POST /events HTTP/1.1" 201 Created',
                'INFO:     127.0.0.1:2 - "POST /events HTTP/1.1" 422 Unprocessable Entity',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    stderr_log.write_text(
        "Rejected event payload from 127.0.0.1: [{'type': 'literal_error'}]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ops_mod, "EVENTS_LOG", events_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDOUT_LOG", stdout_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDERR_LOG", stderr_log)
    monkeypatch.setattr(
        ops_mod,
        "get_policy_config",
        lambda: PolicyConfig(noise=NoisePolicy(rules=())),
    )

    report = run_burn_in(minutes=10, lines=10)

    assert report["status"] == "ok"
    assert report["events_seen"] == 3
    assert report["accepted_event_posts"] == 1
    assert report["rejected_event_posts"] == 1
    assert report["validation_error_count"] == 1
    assert report["health"] == {
        "accepted_event_posts": 1,
        "rejected_event_posts": 1,
        "validation_error_count": 1,
        "slack_delivery_failure_count": 0,
        "status": "degraded",
    }
    assert report["noise_candidates"] == report["repeated_signatures"]
    assert report["noise_rule_suggestions"] == [
        "Review noise rule candidate: source='personal-ops', project='personal-ops', title_contains='Approval expires soon', level='info', window_minutes=10"
    ]
    assert report["repeated_signatures"][0]["count"] == 2
    assert report["repeated_signatures"][0]["source"] == "personal-ops"
    assert report["slack_eligible_events"] == 1
    assert report["slack_volume"] == [
        {
            "count": 1,
            "source": "codex",
            "level": "normal",
        }
    ]


def test_burn_in_filters_policy_covered_noise_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events_log = tmp_path / "events.jsonl"
    events = [
        StoredEvent(
            source="codex",
            level="normal",
            classified_level="normal",
            title="Codex finished a turn",
            body="A Codex turn completed.",
            project="personal-ops",
        ),
        StoredEvent(
            source="codex",
            level="normal",
            classified_level="normal",
            title="Codex finished a turn",
            body="A Codex turn completed.",
            project="personal-ops",
        ),
    ]
    events_log.write_text(
        "\n".join(event.model_dump_json() for event in events) + "\n", encoding="utf-8"
    )
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    stdout_log.write_text("", encoding="utf-8")
    stderr_log.write_text("", encoding="utf-8")
    monkeypatch.setattr(ops_mod, "EVENTS_LOG", events_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDOUT_LOG", stdout_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDERR_LOG", stderr_log)
    monkeypatch.setattr(
        ops_mod,
        "get_policy_config",
        lambda: PolicyConfig(
            noise=NoisePolicy(
                rules=(
                    NoiseRule(
                        source="codex",
                        title_contains="codex finished a turn",
                        body_contains="a codex turn completed.",
                        level="normal",
                    ),
                )
            )
        ),
    )

    report = run_burn_in(minutes=10, lines=10)

    assert report["noise_candidates"] == []
    assert report["noise_rule_suggestions"] == []
    assert report["repeated_signatures"][0]["title"] == "Codex finished a turn"


def test_burn_in_filters_phase_34_mail_echo_noise_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events_log = tmp_path / "events.jsonl"
    events = [
        StoredEvent(
            source="personal-ops",
            level="urgent",
            classified_level="urgent",
            title="Approval Requested",
            body="Phase 34 secondary approval",
            project="mail",
        ),
        StoredEvent(
            source="personal-ops",
            level="urgent",
            classified_level="urgent",
            title="Approval Requested",
            body="Phase 34 secondary approval",
            project="mail",
        ),
        StoredEvent(
            source="personal-ops",
            level="info",
            classified_level="info",
            title="Draft Ready",
            body="Phase 34 secondary approval",
            project="mail",
        ),
        StoredEvent(
            source="personal-ops",
            level="info",
            classified_level="info",
            title="Draft Ready",
            body="Phase 34 secondary approval",
            project="mail",
        ),
    ]
    events_log.write_text(
        "\n".join(event.model_dump_json() for event in events) + "\n", encoding="utf-8"
    )
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    stdout_log.write_text("", encoding="utf-8")
    stderr_log.write_text("", encoding="utf-8")
    monkeypatch.setattr(ops_mod, "EVENTS_LOG", events_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDOUT_LOG", stdout_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDERR_LOG", stderr_log)
    monkeypatch.setattr(
        ops_mod,
        "get_policy_config",
        lambda: PolicyConfig(
            noise=NoisePolicy(
                rules=(
                    NoiseRule(
                        source="personal-ops",
                        project="mail",
                        title_contains="approval requested",
                        body_contains="phase 34 secondary approval",
                        level="urgent",
                    ),
                    NoiseRule(
                        source="personal-ops",
                        project="mail",
                        title_contains="draft ready",
                        body_contains="phase 34 secondary approval",
                        level="info",
                    ),
                )
            )
        ),
    )

    report = run_burn_in(minutes=10, lines=10)

    assert report["noise_candidates"] == []
    assert report["noise_rule_suggestions"] == []
    assert {item["title"] for item in report["repeated_signatures"]} == {
        "Approval Requested",
        "Draft Ready",
    }


def test_burn_in_degrades_on_slack_delivery_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events_log = tmp_path / "events.jsonl"
    events_log.write_text("", encoding="utf-8")
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    stdout_log.write_text(
        'INFO:     127.0.0.1:1 - "POST /events HTTP/1.1" 201 Created\n',
        encoding="utf-8",
    )
    stderr_log.write_text(
        "Slack send failed for abc123: [Errno 2] No such file or directory\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(ops_mod, "EVENTS_LOG", events_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDOUT_LOG", stdout_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDERR_LOG", stderr_log)

    report = run_burn_in(minutes=10, lines=20)

    assert report["status"] == "ok"
    assert report["health"] == {
        "accepted_event_posts": 1,
        "rejected_event_posts": 0,
        "validation_error_count": 0,
        "slack_delivery_failure_count": 1,
        "status": "degraded",
    }


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
        json.dumps(
            {"event_id": f"id-{i}", "source": "codex", "level": "info", "title": "t", "body": "b"}
        )
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
    example_path.write_text('[classifier]\nurgent_keywords = ["database down"]\n', encoding="utf-8")

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
    example_path.write_text('[classifier]\nurgent_keywords = ["database down"]\n', encoding="utf-8")
    config_path.write_text('[classifier]\nurgent_keywords = ["keep me"]\n', encoding="utf-8")

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
        routing=RoutingPolicy(rules=(RoutingRule(source="codex"),)),
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
    assert "policy_drift" in report


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
    assert "policy_drift" in report


def test_policy_check_reports_missing_sample_noise_rules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    example_path = tmp_path / "policy.example.toml"
    example_path.write_text(
        """
[[noise.rules]]
source = "codex"
text_contains = "session complete"
level = "info"
window_minutes = 10

[[noise.rules]]
source = "personal-ops"
project = "personal-ops"
title_contains = "system needs attention"
body_contains = "run personal-ops doctor"
window_minutes = 30
""".strip()
        + "\n",
        encoding="utf-8",
    )
    policy = PolicyConfig(
        config_found=True,
        noise=NoisePolicy(
            rules=(
                NoiseRule(
                    source="codex",
                    text_contains="session complete",
                    level="info",
                    window_minutes=10,
                ),
            )
        ),
    )

    monkeypatch.setattr(ops_mod, "EXAMPLE_POLICY_CONFIG", example_path)
    monkeypatch.setattr(ops_mod, "get_policy_config", lambda: policy)

    def _no_warnings(_policy: PolicyConfig) -> tuple[str, ...]:
        return ()

    monkeypatch.setattr(ops_mod, "analyze_policy_config", _no_warnings)

    report = run_policy_check()

    assert report["status"] == "warn"
    assert report["policy_drift"]["status"] == "warn"
    assert report["policy_drift"]["missing_sample_noise_rule_count"] == 1
    assert report["policy_drift"]["missing_sample_noise_rules"] == [
        {
            "source": "personal-ops",
            "project": "personal-ops",
            "title_contains": "system needs attention",
            "body_contains": "run personal-ops doctor",
            "window_minutes": 30,
        }
    ]


def test_policy_check_passes_when_live_noise_rules_include_sample(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    example_path = tmp_path / "policy.example.toml"
    example_path.write_text(
        """
[[noise.rules]]
source = "codex"
text_contains = "session complete"
level = "info"
window_minutes = 10
""".strip()
        + "\n",
        encoding="utf-8",
    )
    policy = PolicyConfig(
        config_found=True,
        noise=NoisePolicy(
            rules=(
                NoiseRule(
                    source="codex",
                    text_contains="session complete",
                    level="info",
                    window_minutes=10,
                ),
                NoiseRule(source="personal-ops", window_minutes=5),
            )
        ),
    )

    monkeypatch.setattr(ops_mod, "EXAMPLE_POLICY_CONFIG", example_path)
    monkeypatch.setattr(ops_mod, "get_policy_config", lambda: policy)

    def _no_warnings(_policy: PolicyConfig) -> tuple[str, ...]:
        return ()

    monkeypatch.setattr(ops_mod, "analyze_policy_config", _no_warnings)

    report = run_policy_check()

    assert report["status"] == "ok"
    assert report["policy_drift"]["status"] == "ok"
    assert report["policy_drift"]["missing_sample_noise_rule_count"] == 0
    assert report["policy_drift"]["extra_live_noise_rule_count"] == 1


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
    assert any(
        "Move the narrower rule earlier" in suggestion for suggestion in report["suggestions"]
    )


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
    assert any(
        "Give the more important rule a higher `priority`" in suggestion
        for suggestion in report["suggestions"]
    )


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
    assert any(
        "Remove `continue_matching` on the final rule" in suggestion
        for suggestion in report["suggestions"]
    )


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
