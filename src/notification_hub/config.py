"""Configuration constants and paths."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

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

_cached_webhook_url: str | None = None


def get_slack_webhook_url() -> str | None:
    """Read Slack webhook URL from macOS Keychain. Cached after first successful read."""
    global _cached_webhook_url
    if _cached_webhook_url is not None:
        return _cached_webhook_url

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
        if result.returncode == 0 and result.stdout.strip():
            _cached_webhook_url = result.stdout.strip()
            logger.info("Slack webhook URL loaded from Keychain")
            return _cached_webhook_url
        logger.warning(
            "Slack webhook not found in Keychain (service=%s, account=%s)",
            KEYCHAIN_SERVICE,
            KEYCHAIN_ACCOUNT,
        )
        return None
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Failed to read Keychain: %s", exc)
        return None


def clear_webhook_cache() -> None:
    """Clear cached webhook URL. Used for testing."""
    global _cached_webhook_url
    _cached_webhook_url = None
