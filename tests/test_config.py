"""Tests for configuration and Keychain integration."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import notification_hub.config as config_mod
from notification_hub.config import (
    ClassificationPolicy,
    RoutingRule,
    clear_policy_cache,
    clear_webhook_cache,
    get_policy_config,
    get_slack_webhook_url,
)


@pytest.fixture(autouse=True)
def fresh_cache() -> None:
    """Clear webhook cache between tests."""
    clear_webhook_cache()
    clear_policy_cache()


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


class TestPolicyConfig:
    def test_defaults_when_config_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(config_mod, "POLICY_CONFIG", tmp_path / "missing.toml")

        policy = get_policy_config()

        assert policy.config_found is False
        assert policy.load_error is None
        assert "verification fail" in policy.classification.urgent_keywords
        assert policy.suppression.max_slack_per_hour == 20

    def test_loads_classifier_and_suppression_overrides(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            """
[classifier]
urgent_keywords = ["database down"]
normal_keywords = ["ship it"]
info_keywords = ["routine ping"]

[suppression]
quiet_start_hour = 22
quiet_end_hour = 6
dedup_window_minutes = 45
max_push_per_hour = 2
max_slack_per_hour = 7
max_overflow_buffer = 42
max_quiet_queue = 12
""".strip(),
            encoding="utf-8",
        )
        monkeypatch.setattr(config_mod, "POLICY_CONFIG", config_path)

        policy = get_policy_config()

        assert policy.config_found is True
        assert policy.load_error is None
        assert policy.classification == ClassificationPolicy(
            urgent_keywords=("database down",),
            normal_keywords=("ship it",),
            info_keywords=("routine ping",),
        )
        assert policy.suppression.quiet_start_hour == 22
        assert policy.suppression.max_slack_per_hour == 7

    def test_invalid_toml_falls_back_with_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text("[classifier\nbroken = true\n", encoding="utf-8")
        monkeypatch.setattr(config_mod, "POLICY_CONFIG", config_path)

        policy = get_policy_config()

        assert policy.config_found is True
        assert policy.load_error is not None
        assert "session complete" in policy.classification.normal_keywords

    def test_loads_routing_rules(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            """
[[routing.rules]]
project = "notification-hub"
force_level = "normal"
disable_push = true

[[routing.rules]]
source = "bridge_watcher"
disable_slack = true
""".strip(),
            encoding="utf-8",
        )
        monkeypatch.setattr(config_mod, "POLICY_CONFIG", config_path)

        policy = get_policy_config()

        assert policy.routing.rules == (
            RoutingRule(
                project="notification-hub",
                force_level="normal",
                disable_push=True,
            ),
            RoutingRule(
                source="bridge_watcher",
                disable_slack=True,
            ),
        )

    def test_cache_reload_when_config_changes(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text("[classifier]\nurgent_keywords = [\"alpha\"]\n", encoding="utf-8")
        monkeypatch.setattr(config_mod, "POLICY_CONFIG", config_path)

        first = get_policy_config()
        config_path.write_text("[classifier]\nurgent_keywords = [\"beta\"]\n", encoding="utf-8")
        second = get_policy_config()

        assert first.classification.urgent_keywords == ("alpha",)
        assert second.classification.urgent_keywords == ("beta",)
