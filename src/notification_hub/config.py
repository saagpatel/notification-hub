"""Configuration constants and paths."""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import TypeGuard

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 9199

EVENTS_DIR = Path.home() / ".local" / "share" / "notification-hub"
EVENTS_LOG = EVENTS_DIR / "events.jsonl"

BRIDGE_FILE = Path.home() / ".claude" / "projects" / "-Users-d" / "memory" / "claude_ai_context.md"

# Sections in the bridge file that trigger events when changed
WATCHED_SECTIONS = (
    "## Recent Claude Code Activity",
    "## Recent Codex Activity",
)

# Keychain service/account for Slack webhook URL
KEYCHAIN_SERVICE = "slack-webhook"
KEYCHAIN_ACCOUNT = "notification-hub"
MISSING_WEBHOOK_RECHECK_SECONDS = 60.0

_UNSET = object()
_cached_webhook_url: str | None | object = _UNSET
_cached_webhook_checked_at: float | None = None


def _is_cached_webhook_url(value: str | None | object) -> TypeGuard[str | None]:
    """Narrow the cache sentinel away for static type checkers."""
    return isinstance(value, str) or value is None


def get_slack_webhook_url() -> str | None:
    """Read Slack webhook URL from macOS Keychain with a short retry window for missing values."""
    global _cached_webhook_url, _cached_webhook_checked_at
    if isinstance(_cached_webhook_url, str):
        return _cached_webhook_url

    if _cached_webhook_url is None and _cached_webhook_checked_at is not None:
        if (time.monotonic() - _cached_webhook_checked_at) < MISSING_WEBHOOK_RECHECK_SECONDS:
            return None

    if _cached_webhook_url is not _UNSET:
        assert _is_cached_webhook_url(_cached_webhook_url)

    try:
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-a",
                KEYCHAIN_ACCOUNT,
                "-s",
                KEYCHAIN_SERVICE,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        _cached_webhook_checked_at = time.monotonic()
        if result.returncode == 0 and result.stdout.strip():
            _cached_webhook_url = result.stdout.strip()
            logger.info("Slack webhook URL loaded from Keychain")
            assert _is_cached_webhook_url(_cached_webhook_url)
            return _cached_webhook_url
        logger.warning(
            "Slack webhook not found in Keychain (service=%s, account=%s)",
            KEYCHAIN_SERVICE,
            KEYCHAIN_ACCOUNT,
        )
        _cached_webhook_url = None
        return None
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Failed to read Keychain: %s", exc)
        _cached_webhook_url = None
        return None


def clear_webhook_cache() -> None:
    """Clear cached webhook URL. Used for testing."""
    global _cached_webhook_url, _cached_webhook_checked_at
    _cached_webhook_url = _UNSET
    _cached_webhook_checked_at = None


def has_slack_webhook_configured() -> bool:
    """Return whether a Slack webhook is available via Keychain."""
    return get_slack_webhook_url() is not None
