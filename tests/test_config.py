"""Tests for configuration and Keychain integration."""

from __future__ import annotations

import subprocess
from unittest.mock import patch, MagicMock

import pytest

from notification_hub.config import get_slack_webhook_url, clear_webhook_cache


@pytest.fixture(autouse=True)
def fresh_cache() -> None:
    """Clear webhook cache between tests."""
    clear_webhook_cache()


class TestKeychainWebhook:
    def test_reads_from_keychain(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "https://hooks.slack.com/services/T/B/X\n"
        with patch("notification_hub.config.subprocess.run", return_value=mock_result):
            url = get_slack_webhook_url()
        assert url == "https://hooks.slack.com/services/T/B/X"

    def test_caches_after_first_read(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "https://hooks.slack.com/cached\n"
        with patch("notification_hub.config.subprocess.run", return_value=mock_result) as mock_run:
            url1 = get_slack_webhook_url()
            url2 = get_slack_webhook_url()
        assert url1 == url2
        mock_run.assert_called_once()  # Only one subprocess call

    def test_returns_none_when_not_found(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 44  # security command not-found exit code
        mock_result.stdout = ""
        with patch("notification_hub.config.subprocess.run", return_value=mock_result):
            url = get_slack_webhook_url()
        assert url is None

    def test_returns_none_on_timeout(self) -> None:
        with patch(
            "notification_hub.config.subprocess.run",
            side_effect=subprocess.TimeoutExpired("security", 5),
        ):
            url = get_slack_webhook_url()
        assert url is None

    def test_returns_none_on_os_error(self) -> None:
        with patch(
            "notification_hub.config.subprocess.run",
            side_effect=OSError("not found"),
        ):
            url = get_slack_webhook_url()
        assert url is None

    def test_retries_missing_webhook_after_ttl(self) -> None:
        missing = MagicMock()
        missing.returncode = 44
        missing.stdout = ""

        found = MagicMock()
        found.returncode = 0
        found.stdout = "https://hooks.slack.com/recovered\n"

        with (
            patch(
                "notification_hub.config.subprocess.run",
                side_effect=[missing, found],
            ) as mock_run,
            patch("notification_hub.config.time.monotonic", side_effect=[100.0, 161.0, 161.0]),
        ):
            first = get_slack_webhook_url()
            second = get_slack_webhook_url()

        assert first is None
        assert second == "https://hooks.slack.com/recovered"
        assert mock_run.call_count == 2

    def test_clear_cache_allows_reread(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "https://first\n"
        with patch("notification_hub.config.subprocess.run", return_value=mock_result):
            get_slack_webhook_url()

        clear_webhook_cache()

        mock_result2 = MagicMock()
        mock_result2.returncode = 0
        mock_result2.stdout = "https://second\n"
        with patch("notification_hub.config.subprocess.run", return_value=mock_result2):
            url = get_slack_webhook_url()
        assert url == "https://second"

    def test_uses_correct_keychain_args(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "https://test\n"
        with patch("notification_hub.config.subprocess.run", return_value=mock_result) as mock_run:
            get_slack_webhook_url()
        cmd = mock_run.call_args[0][0]
        assert "/usr/bin/security" in cmd
        assert "find-generic-password" in cmd
        assert "-a" in cmd
        assert "notification-hub" in cmd
        assert "-s" in cmd
        assert "slack-webhook" in cmd
        assert "-w" in cmd
