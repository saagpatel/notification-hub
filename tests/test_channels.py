"""Tests for delivery channels: JSONL logging, terminal-notifier push, and Slack."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import httpx
import pytest

from notification_hub.channels import (
    _format_slack_digest,
    _format_slack_message,
    read_jsonl,
    send_push,
    send_slack,
    send_slack_digest,
    write_jsonl,
)
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


class TestSlackFormatting:
    def test_message_includes_level_emoji(self) -> None:
        event = _make_event(classified_level="urgent")
        payload = _format_slack_message(event)
        text = payload["blocks"][0]["text"]["text"]  # type: ignore[index]
        assert ":red_circle:" in text

    def test_message_includes_project(self) -> None:
        event = _make_event(project="ink", classified_level="normal")
        payload = _format_slack_message(event)
        text = payload["blocks"][0]["text"]["text"]  # type: ignore[index]
        assert "`ink`" in text

    def test_message_includes_source_label(self) -> None:
        event = _make_event(source="codex", classified_level="info")
        payload = _format_slack_message(event)
        text = payload["blocks"][0]["text"]["text"]  # type: ignore[index]
        assert "Codex" in text
        assert ":gear:" in text

    def test_message_has_fallback_text(self) -> None:
        event = _make_event(title="Test Alert", classified_level="urgent")
        payload = _format_slack_message(event)
        assert "Test Alert" in payload["text"]  # type: ignore[operator]
        assert "URGENT" in payload["text"]  # type: ignore[operator]

    def test_message_without_project(self) -> None:
        event = _make_event(project=None, classified_level="info")
        payload = _format_slack_message(event)
        text = payload["blocks"][0]["text"]["text"]  # type: ignore[index]
        assert "` —" not in text  # no project tag

    def test_digest_format(self) -> None:
        events = [
            _make_event(title="Event 1", project="ink", classified_level="urgent"),
            _make_event(title="Event 2", project="codec", classified_level="normal"),
        ]
        payload = _format_slack_digest(events)
        text = payload["blocks"][0]["text"]["text"]  # type: ignore[index]
        assert "Notification Digest" in text
        assert "2 events" in text
        assert "`ink`" in text
        assert "`codec`" in text

    def test_digest_empty_list(self) -> None:
        payload = _format_slack_digest([])
        text = payload["blocks"][0]["text"]["text"]  # type: ignore[index]
        assert "0 events" in text


class TestSendSlack:
    def test_sends_when_webhook_configured(self) -> None:
        event = _make_event(classified_level="urgent")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with (
            patch(
                "notification_hub.channels.get_slack_webhook_url",
                return_value="https://hooks.slack.com/test",
            ),
            patch("notification_hub.channels.httpx.post", return_value=mock_resp) as mock_post,
        ):
            result = send_slack(event)
        assert result is True
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[0][0] == "https://hooks.slack.com/test"
        assert "blocks" in call_args[1]["json"]

    def test_returns_false_when_no_webhook(self) -> None:
        event = _make_event()
        with patch("notification_hub.channels.get_slack_webhook_url", return_value=None):
            result = send_slack(event)
        assert result is False

    def test_returns_false_on_non_200(self) -> None:
        event = _make_event()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with (
            patch(
                "notification_hub.channels.get_slack_webhook_url",
                return_value="https://hooks.slack.com/test",
            ),
            patch("notification_hub.channels.httpx.post", return_value=mock_resp),
        ):
            result = send_slack(event)
        assert result is False

    def test_returns_false_on_http_error(self) -> None:
        event = _make_event()
        with (
            patch(
                "notification_hub.channels.get_slack_webhook_url",
                return_value="https://hooks.slack.com/test",
            ),
            patch(
                "notification_hub.channels.httpx.post", side_effect=httpx.ConnectError("refused")
            ),
        ):
            result = send_slack(event)
        assert result is False

    def test_digest_sends_when_webhook_configured(self) -> None:
        events = [_make_event(title="E1"), _make_event(title="E2")]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with (
            patch(
                "notification_hub.channels.get_slack_webhook_url",
                return_value="https://hooks.slack.com/test",
            ),
            patch("notification_hub.channels.httpx.post", return_value=mock_resp),
        ):
            result = send_slack_digest(events)
        assert result is True

    def test_digest_empty_returns_true(self) -> None:
        result = send_slack_digest([])
        assert result is True

    def test_webhook_url_never_in_payload(self) -> None:
        event = _make_event(classified_level="urgent")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with (
            patch(
                "notification_hub.channels.get_slack_webhook_url",
                return_value="https://hooks.slack.com/secret",
            ),
            patch("notification_hub.channels.httpx.post", return_value=mock_resp) as mock_post,
        ):
            send_slack(event)
        payload_str = json.dumps(mock_post.call_args[1]["json"])
        assert "secret" not in payload_str
        assert "hooks.slack.com" not in payload_str
