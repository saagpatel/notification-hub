"""Tests for logs and burn-in diagnostics reports."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import notification_hub.operations as ops_mod
from notification_hub.config import NoisePolicy, NoiseRule, PolicyConfig
from notification_hub.models import StoredEvent
from notification_hub.operations import run_burn_in, run_logs


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

    assert report["status"] == "degraded"
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
    assert report["error"] == "operation failed; inspect local logs for details"


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

    assert report["status"] == "degraded"
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

    assert report["status"] == "degraded"
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

    assert report["status"] == "degraded"
    assert report["health"] == {
        "accepted_event_posts": 1,
        "rejected_event_posts": 0,
        "validation_error_count": 0,
        "slack_delivery_failure_count": 1,
        "status": "degraded",
    }


def test_burn_in_ignores_stale_daemon_log_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events_log = tmp_path / "events.jsonl"
    events_log.write_text("", encoding="utf-8")
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    stdout_log.write_text(
        'INFO:     127.0.0.1:1 - "POST /events HTTP/1.1" 422 Unprocessable Entity\n',
        encoding="utf-8",
    )
    stderr_log.write_text(
        "\n".join(
            [
                "Rejected event payload from 127.0.0.1: [{'type': 'old_error'}]",
                "Slack send failed for old: The read operation timed out",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    old_timestamp = 1
    os.utime(stdout_log, (old_timestamp, old_timestamp))
    os.utime(stderr_log, (old_timestamp, old_timestamp))

    monkeypatch.setattr(ops_mod, "EVENTS_LOG", events_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDOUT_LOG", stdout_log)
    monkeypatch.setattr(ops_mod, "DAEMON_STDERR_LOG", stderr_log)

    report = run_burn_in(minutes=10, lines=20)

    assert report["accepted_event_posts"] == 0
    assert report["rejected_event_posts"] == 0
    assert report["validation_error_count"] == 0
    assert report["health"] == {
        "accepted_event_posts": 0,
        "rejected_event_posts": 0,
        "validation_error_count": 0,
        "slack_delivery_failure_count": 0,
        "status": "ok",
    }
