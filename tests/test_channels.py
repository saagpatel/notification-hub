"""Tests for delivery channels: JSONL logging and terminal-notifier push."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from notification_hub.channels import read_jsonl, send_push, write_jsonl
from notification_hub.models import StoredEvent
import notification_hub.channels as channels_mod


@pytest.fixture
def tmp_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log_dir = tmp_path / "notification-hub"
    log_file = log_dir / "events.jsonl"
    monkeypatch.setattr(channels_mod, "EVENTS_DIR", log_dir)
    monkeypatch.setattr(channels_mod, "EVENTS_LOG", log_file)
    return log_file


def _make_event(**overrides: object) -> StoredEvent:
    defaults = {
        "source": "cc",
        "level": "info",
        "title": "Test",
        "body": "Test body",
    }
    defaults.update(overrides)
    return StoredEvent(**defaults)  # type: ignore[arg-type]


def test_write_creates_directory(tmp_log: Path) -> None:
    assert not tmp_log.parent.exists()
    event = _make_event()
    write_jsonl(event)
    assert tmp_log.parent.exists()
    assert tmp_log.exists()


def test_write_appends_valid_json(tmp_log: Path) -> None:
    e1 = _make_event(title="First")
    e2 = _make_event(title="Second")
    write_jsonl(e1)
    write_jsonl(e2)
    lines = tmp_log.read_text().strip().split("\n")
    assert len(lines) == 2
    parsed = json.loads(lines[0])
    assert parsed["title"] == "First"
    parsed2 = json.loads(lines[1])
    assert parsed2["title"] == "Second"


def test_read_jsonl_empty(tmp_log: Path) -> None:
    events = read_jsonl(tmp_log)
    assert events == []


def test_read_jsonl_roundtrip(tmp_log: Path) -> None:
    original = _make_event(title="Roundtrip", project="test-proj")
    write_jsonl(original)
    events = read_jsonl(tmp_log)
    assert len(events) == 1
    assert events[0].title == "Roundtrip"
    assert events[0].project == "test-proj"
    assert events[0].event_id == original.event_id


def test_stored_event_has_ids() -> None:
    event = _make_event()
    assert len(event.event_id) == 12
    assert event.received_at is not None


class TestSendPush:
    def test_sends_notification_when_notifier_available(self) -> None:
        event = _make_event(title="Alert", source="codex", project="ink")
        with (
            patch(
                "notification_hub.channels.shutil.which",
                return_value="/usr/local/bin/terminal-notifier",
            ),
            patch("notification_hub.channels.subprocess.run") as mock_run,
        ):
            result = send_push(event)
        assert result is True
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/usr/local/bin/terminal-notifier"
        assert "-title" in cmd
        assert "Notification Hub" in cmd
        assert "-sound" in cmd
        assert "Hero" in cmd

    def test_subtitle_includes_source_label(self) -> None:
        event = _make_event(source="cc")
        with (
            patch("notification_hub.channels.shutil.which", return_value="/usr/bin/tn"),
            patch("notification_hub.channels.subprocess.run") as mock_run,
        ):
            send_push(event)
        cmd = mock_run.call_args[0][0]
        subtitle_idx = cmd.index("-subtitle") + 1
        assert "Claude Code" in cmd[subtitle_idx]

    def test_subtitle_includes_project_name(self) -> None:
        event = _make_event(source="codex", project="notification-hub")
        with (
            patch("notification_hub.channels.shutil.which", return_value="/usr/bin/tn"),
            patch("notification_hub.channels.subprocess.run") as mock_run,
        ):
            send_push(event)
        cmd = mock_run.call_args[0][0]
        subtitle_idx = cmd.index("-subtitle") + 1
        assert "notification-hub" in cmd[subtitle_idx]
        assert "Codex" in cmd[subtitle_idx]

    def test_returns_false_when_notifier_missing(self) -> None:
        event = _make_event()
        with patch("notification_hub.channels.shutil.which", return_value=None):
            result = send_push(event)
        assert result is False

    def test_returns_false_on_timeout(self) -> None:
        import subprocess as sp

        event = _make_event()
        with (
            patch("notification_hub.channels.shutil.which", return_value="/usr/bin/tn"),
            patch(
                "notification_hub.channels.subprocess.run", side_effect=sp.TimeoutExpired("tn", 5)
            ),
        ):
            result = send_push(event)
        assert result is False

    def test_returns_false_on_os_error(self) -> None:
        event = _make_event()
        with (
            patch("notification_hub.channels.shutil.which", return_value="/usr/bin/tn"),
            patch("notification_hub.channels.subprocess.run", side_effect=OSError("no such file")),
        ):
            result = send_push(event)
        assert result is False

    def test_truncates_long_body(self) -> None:
        long_body = "x" * 300
        event = _make_event(body=long_body)
        with (
            patch("notification_hub.channels.shutil.which", return_value="/usr/bin/tn"),
            patch("notification_hub.channels.subprocess.run") as mock_run,
        ):
            send_push(event)
        cmd = mock_run.call_args[0][0]
        msg_idx = cmd.index("-message") + 1
        assert len(cmd[msg_idx]) == 200
        assert cmd[msg_idx].endswith("...")
