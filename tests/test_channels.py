"""Tests for delivery channels: JSONL logging, terminal-notifier push, and Slack."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

import notification_hub.channels as channels_mod
from notification_hub.channels import (
    ChannelDeliveryResult,
    format_slack_digest,
    format_slack_message,
    read_jsonl,
    redact_for_external_delivery,
    send_push,
    send_push_with_result,
    send_slack,
    send_slack_digest,
    send_slack_with_result,
    write_jsonl,
)
from notification_hub.models import Level, Source, StoredEvent


@pytest.fixture(autouse=True)
def allow_isolated_channel_doubles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTIFICATION_HUB_TEST_ALLOW_ISOLATED_TRANSPORT", "1")


@pytest.fixture
def tmp_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log_dir = tmp_path / "notification-hub"
    log_file = log_dir / "events.jsonl"
    monkeypatch.setattr(channels_mod, "EVENTS_DIR", log_dir)
    monkeypatch.setattr(channels_mod, "EVENTS_LOG", log_file)
    return log_file


def _as_source(value: object) -> Source:
    if value in ("cc", "codex", "claude_ai", "bridge_watcher", "personal-ops", "notion-os"):
        return value
    return "cc"


def _as_level(value: object) -> Level:
    if value in ("urgent", "normal", "info"):
        return value
    return "info"


def _as_project(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _make_event(**overrides: object) -> StoredEvent:
    defaults: dict[str, object] = {
        "source": "cc",
        "level": "info",
        "title": "Test",
        "body": "Test body",
    }
    defaults.update(overrides)
    return StoredEvent(
        source=_as_source(defaults["source"]),
        level=_as_level(defaults["level"]),
        title=str(defaults["title"]),
        body=str(defaults["body"]),
        project=_as_project(defaults.get("project")),
        classified_level=_as_level(defaults["classified_level"])
        if defaults.get("classified_level") is not None
        else None,
    )


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


class TestPushNotifierDiscovery:
    def test_finds_notifier_from_path(self) -> None:
        with patch(
            "notification_hub.channels.shutil.which",
            return_value="/usr/local/bin/terminal-notifier",
        ):
            assert channels_mod.find_push_notifier() == "/usr/local/bin/terminal-notifier"

    def test_finds_notifier_from_common_location(self) -> None:
        with (
            patch("notification_hub.channels.shutil.which", return_value=None),
            patch("notification_hub.channels.Path.exists", return_value=True),
        ):
            assert channels_mod.find_push_notifier() == "/opt/homebrew/bin/terminal-notifier"


class TestSendPush:
    def test_test_mode_blocks_push_without_isolated_transport_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("NOTIFICATION_HUB_TEST_ALLOW_ISOLATED_TRANSPORT")
        with patch("notification_hub.channels.subprocess.run") as mock_run:
            assert send_push(_make_event()) is False
        mock_run.assert_not_called()

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
        with patch("notification_hub.channels.find_push_notifier", return_value=None):
            result = send_push(event)
        assert result is False

    def test_returns_false_on_nonzero_exit(self) -> None:
        event = _make_event()
        with (
            patch("notification_hub.channels.find_push_notifier", return_value="/usr/bin/tn"),
            patch(
                "notification_hub.channels.subprocess.run",
                return_value=subprocess.CompletedProcess(["/usr/bin/tn"], 7),
            ),
        ):
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

    @pytest.mark.parametrize(
        ("side_effect", "return_value", "category"),
        [
            (subprocess.TimeoutExpired("tn", 5), None, "push_notifier_timeout"),
            (OSError("private path must not persist"), None, "push_os_error"),
            (
                None,
                subprocess.CompletedProcess(["/usr/bin/tn"], 7),
                "push_notifier_nonzero_exit",
            ),
        ],
    )
    def test_detailed_result_has_secret_safe_failure_category(
        self,
        side_effect: Exception | None,
        return_value: subprocess.CompletedProcess[str] | None,
        category: str,
    ) -> None:
        with (
            patch("notification_hub.channels.find_push_notifier", return_value="/usr/bin/tn"),
            patch(
                "notification_hub.channels.subprocess.run",
                side_effect=side_effect,
                return_value=return_value,
            ),
        ):
            result = send_push_with_result(_make_event())

        assert result == ChannelDeliveryResult(False, error_category=category)

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
    def test_external_redaction_removes_local_paths_and_secret_assignments(self) -> None:
        event = _make_event(
            title="token=title-secret",
            body="Inspect /Users/d/.codex/session.json token=super-secret",
            project="/Users/d/private-project",
        )

        redacted = redact_for_external_delivery(event)

        assert "/Users/d" not in redacted.body
        assert "super-secret" not in redacted.body
        assert "title-secret" not in redacted.title
        assert redacted.project is not None and "/Users/d" not in redacted.project
        assert "[local-path-redacted]" in redacted.body

    def test_secret_event_external_copy_contains_no_original_content(self) -> None:
        event = _make_event(title="Password leaked", body="password=hunter2").model_copy(
            update={"privacy_class": "secret", "context": {"token": "abc"}}
        )

        redacted = redact_for_external_delivery(event)

        assert redacted.title == "Sensitive notification"
        assert "hunter2" not in redacted.body
        assert redacted.context == {}

    def test_message_includes_level_emoji(self) -> None:
        event = _make_event(classified_level="urgent")
        payload = format_slack_message(event)
        text = payload["blocks"][0]["text"]["text"]
        assert ":red_circle:" in text

    def test_message_includes_project(self) -> None:
        event = _make_event(project="ink", classified_level="normal")
        payload = format_slack_message(event)
        text = payload["blocks"][0]["text"]["text"]
        assert "`ink`" in text

    def test_message_includes_source_label(self) -> None:
        event = _make_event(source="codex", classified_level="info")
        payload = format_slack_message(event)
        text = payload["blocks"][0]["text"]["text"]
        assert "Codex" in text
        assert ":gear:" in text

    def test_message_has_fallback_text(self) -> None:
        event = _make_event(title="Test Alert", classified_level="urgent")
        payload = format_slack_message(event)
        assert "Test Alert" in payload["text"]
        assert "URGENT" in payload["text"]

    def test_message_without_project(self) -> None:
        event = _make_event(project=None, classified_level="info")
        payload = format_slack_message(event)
        text = payload["blocks"][0]["text"]["text"]
        assert "` —" not in text  # no project tag

    def test_digest_format(self) -> None:
        events = [
            _make_event(title="Event 1", project="ink", classified_level="urgent"),
            _make_event(title="Event 2", project="codec", classified_level="normal"),
        ]
        payload = format_slack_digest(events)
        text = payload["blocks"][0]["text"]["text"]
        assert "Notification Digest" in text
        assert "2 events" in text
        assert "`ink`" in text
        assert "`codec`" in text

    def test_digest_empty_list(self) -> None:
        payload = format_slack_digest([])
        text = payload["blocks"][0]["text"]["text"]
        assert "0 events" in text


class TestSendSlack:
    def test_test_mode_blocks_slack_before_keychain_or_http(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("NOTIFICATION_HUB_TEST_ALLOW_ISOLATED_TRANSPORT")
        with (
            patch("notification_hub.channels.get_slack_webhook_url") as mock_keychain,
            patch("notification_hub.channels.httpx.post") as mock_post,
        ):
            assert send_slack(_make_event()) is False
        mock_keychain.assert_not_called()
        mock_post.assert_not_called()

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
            patch("notification_hub.channels.httpx.post", return_value=mock_resp) as mock_post,
            patch("notification_hub.channels.time.sleep"),
        ):
            result = send_slack(event)
        assert result is False
        assert mock_post.call_count == channels_mod._SLACK_MAX_ATTEMPTS

    def test_returns_false_on_http_error(self) -> None:
        event = _make_event()
        with (
            patch(
                "notification_hub.channels.get_slack_webhook_url",
                return_value="https://hooks.slack.com/test",
            ),
            patch(
                "notification_hub.channels.httpx.post", side_effect=httpx.ConnectError("refused")
            ) as mock_post,
            patch("notification_hub.channels.time.sleep"),
        ):
            result = send_slack(event)
        assert result is False
        assert mock_post.call_count == channels_mod._SLACK_MAX_ATTEMPTS

    @pytest.mark.parametrize(
        ("status_code", "category", "attempts"),
        [
            (400, "slack_http_4xx", 1),
            (429, "slack_http_429", channels_mod._SLACK_MAX_ATTEMPTS),
            (503, "slack_http_5xx", channels_mod._SLACK_MAX_ATTEMPTS),
        ],
    )
    def test_detailed_result_categorizes_http_failure_without_response_content(
        self, status_code: int, category: str, attempts: int
    ) -> None:
        response = MagicMock(status_code=status_code)
        response.headers = {}
        response.text = "secret response body"
        with (
            patch(
                "notification_hub.channels.get_slack_webhook_url",
                return_value="https://hooks.slack.com/services/secret-token",
            ),
            patch("notification_hub.channels.httpx.post", return_value=response) as mock_post,
            patch("notification_hub.channels.time.sleep"),
        ):
            result = send_slack_with_result(_make_event())

        assert result == ChannelDeliveryResult(False, error_category=category)
        assert "secret" not in (result.error_category or "")
        assert mock_post.call_count == attempts

    @pytest.mark.parametrize(
        ("error", "category"),
        [
            (httpx.ReadTimeout("slow"), "slack_timeout"),
            (httpx.ConnectError("refused"), "slack_network_error"),
        ],
    )
    def test_detailed_result_categorizes_transport_failure(
        self, error: httpx.TransportError, category: str
    ) -> None:
        with (
            patch(
                "notification_hub.channels.get_slack_webhook_url",
                return_value="https://hooks.slack.com/services/secret-token",
            ),
            patch("notification_hub.channels.httpx.post", side_effect=error),
            patch("notification_hub.channels.time.sleep"),
        ):
            result = send_slack_with_result(_make_event())

        assert result == ChannelDeliveryResult(False, error_category=category)

    @pytest.mark.parametrize(
        "error",
        [
            FileNotFoundError("missing cert bundle"),
            OSError("socket setup failed"),
            RuntimeError("unexpected transport setup failure"),
        ],
    )
    def test_returns_false_on_transport_boundary_error(self, error: Exception) -> None:
        event = _make_event()
        with (
            patch(
                "notification_hub.channels.get_slack_webhook_url",
                return_value="https://hooks.slack.com/test",
            ),
            patch("notification_hub.channels.httpx.post", side_effect=error),
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

    @pytest.mark.parametrize(
        "error",
        [
            FileNotFoundError("missing cert bundle"),
            OSError("socket setup failed"),
            RuntimeError("unexpected transport setup failure"),
            httpx.ConnectError("refused"),
        ],
    )
    def test_digest_returns_false_on_transport_boundary_error(self, error: Exception) -> None:
        events = [_make_event(title="E1"), _make_event(title="E2")]
        with (
            patch(
                "notification_hub.channels.get_slack_webhook_url",
                return_value="https://hooks.slack.com/test",
            ),
            patch("notification_hub.channels.httpx.post", side_effect=error),
            patch("notification_hub.channels.time.sleep"),
        ):
            result = send_slack_digest(events)
        assert result is False

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


class TestSendSlackRetry:
    """Transient Slack failures (network, timeout, 429, 5xx) must retry with backoff."""

    _WEBHOOK = "notification_hub.channels.get_slack_webhook_url"
    _POST = "notification_hub.channels.httpx.post"
    _SLEEP = "notification_hub.channels.time.sleep"
    _URL = "https://hooks.slack.com/test"

    @staticmethod
    def _resp(status_code: int, retry_after: str | None = None) -> MagicMock:
        resp = MagicMock()
        resp.status_code = status_code
        resp.headers = {} if retry_after is None else {"Retry-After": retry_after}
        return resp

    def test_retries_on_429_then_succeeds(self) -> None:
        event = _make_event()
        with (
            patch(self._WEBHOOK, return_value=self._URL),
            patch(self._POST, side_effect=[self._resp(429), self._resp(200)]) as mock_post,
            patch(self._SLEEP) as mock_sleep,
        ):
            result = send_slack(event)
        assert result is True
        assert mock_post.call_count == 2
        assert mock_sleep.call_count == 1

    def test_retries_on_5xx_then_succeeds(self) -> None:
        event = _make_event()
        with (
            patch(self._WEBHOOK, return_value=self._URL),
            patch(self._POST, side_effect=[self._resp(503), self._resp(200)]) as mock_post,
            patch(self._SLEEP),
        ):
            result = send_slack(event)
        assert result is True
        assert mock_post.call_count == 2

    def test_gives_up_after_max_attempts_on_persistent_5xx(self) -> None:
        event = _make_event()
        with (
            patch(self._WEBHOOK, return_value=self._URL),
            patch(self._POST, return_value=self._resp(500)) as mock_post,
            patch(self._SLEEP) as mock_sleep,
        ):
            result = send_slack(event)
        assert result is False
        assert mock_post.call_count == channels_mod._SLACK_MAX_ATTEMPTS
        assert mock_sleep.call_count == channels_mod._SLACK_MAX_ATTEMPTS - 1

    def test_no_retry_on_permanent_4xx(self) -> None:
        event = _make_event()
        with (
            patch(self._WEBHOOK, return_value=self._URL),
            patch(self._POST, return_value=self._resp(404)) as mock_post,
            patch(self._SLEEP) as mock_sleep,
        ):
            result = send_slack(event)
        assert result is False
        assert mock_post.call_count == 1
        mock_sleep.assert_not_called()

    def test_retries_on_transient_network_error_then_succeeds(self) -> None:
        event = _make_event()
        side_effects = [httpx.ConnectError("refused"), self._resp(200)]
        with (
            patch(self._WEBHOOK, return_value=self._URL),
            patch(self._POST, side_effect=side_effects) as mock_post,
            patch(self._SLEEP),
        ):
            result = send_slack(event)
        assert result is True
        assert mock_post.call_count == 2

    def test_success_on_first_attempt_does_not_sleep(self) -> None:
        event = _make_event()
        with (
            patch(self._WEBHOOK, return_value=self._URL),
            patch(self._POST, return_value=self._resp(200)) as mock_post,
            patch(self._SLEEP) as mock_sleep,
        ):
            result = send_slack(event)
        assert result is True
        assert mock_post.call_count == 1
        mock_sleep.assert_not_called()

    def test_honors_retry_after_header_on_429(self) -> None:
        event = _make_event()
        responses = [self._resp(429, retry_after="2"), self._resp(200)]
        with (
            patch(self._WEBHOOK, return_value=self._URL),
            patch(self._POST, side_effect=responses),
            patch(self._SLEEP) as mock_sleep,
        ):
            result = send_slack(event)
        assert result is True
        mock_sleep.assert_called_once_with(2.0)

    def test_digest_retries_on_429_then_succeeds(self) -> None:
        events = [_make_event(title="E1"), _make_event(title="E2")]
        with (
            patch(self._WEBHOOK, return_value=self._URL),
            patch(self._POST, side_effect=[self._resp(429), self._resp(200)]) as mock_post,
            patch(self._SLEEP),
        ):
            result = send_slack_digest(events)
        assert result is True
        assert mock_post.call_count == 2
