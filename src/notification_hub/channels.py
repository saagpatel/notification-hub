"""Delivery channels for notification events."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from notification_hub.config import EVENTS_DIR, EVENTS_LOG
from notification_hub.models import StoredEvent

logger = logging.getLogger(__name__)

# Source labels for push notification subtitles
_SOURCE_LABELS: dict[str, str] = {
    "cc": "Claude Code",
    "codex": "Codex",
    "claude_ai": "Claude.ai",
    "bridge_watcher": "Bridge",
}


def ensure_log_dir() -> None:
    """Create the events log directory if it doesn't exist."""
    EVENTS_DIR.mkdir(parents=True, exist_ok=True)


def write_jsonl(event: StoredEvent) -> None:
    """Append an event to the JSONL log file."""
    ensure_log_dir()
    line = event.model_dump_json() + "\n"
    with open(EVENTS_LOG, "a", encoding="utf-8") as f:
        f.write(line)
    logger.debug("Logged event %s to JSONL", event.event_id)


def read_jsonl(path: Path | None = None) -> list[StoredEvent]:
    """Read all events from the JSONL log. Used for testing and diagnostics."""
    target = path or EVENTS_LOG
    if not target.exists():
        return []
    events: list[StoredEvent] = []
    with open(target, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                events.append(StoredEvent.model_validate(json.loads(stripped)))
    return events


def send_push(event: StoredEvent) -> bool:
    """Send a macOS push notification via terminal-notifier. Returns True if sent."""
    notifier = shutil.which("terminal-notifier")
    if notifier is None:
        logger.warning("terminal-notifier not found, skipping push for %s", event.event_id)
        return False

    subtitle = _SOURCE_LABELS.get(event.source, event.source)
    if event.project:
        subtitle = f"{subtitle} — {event.project}"

    body = event.body
    if len(body) > 200:
        body = body[:197] + "..."

    cmd = [
        notifier,
        "-title",
        "Notification Hub",
        "-subtitle",
        subtitle,
        "-message",
        body,
        "-sound",
        "Hero",
        "-group",
        "notification-hub",
    ]

    try:
        subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        logger.info("Push sent for event %s", event.event_id)
        return True
    except subprocess.TimeoutExpired:
        logger.warning("terminal-notifier timed out for %s", event.event_id)
        return False
    except OSError as exc:
        logger.warning("Push failed for %s: %s", event.event_id, exc)
        return False
