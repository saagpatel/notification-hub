"""Delivery channels for notification events."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import TypedDict

import httpx

from notification_hub.config import EVENTS_DIR, EVENTS_LOG, get_slack_webhook_url
from notification_hub.models import StoredEvent

logger = logging.getLogger(__name__)

# Source labels for push notification subtitles
_SOURCE_LABELS: dict[str, str] = {
    "cc": "Claude Code",
    "codex": "Codex",
    "claude_ai": "Claude.ai",
    "bridge_watcher": "Bridge",
}

# Slack emoji for level badges
_LEVEL_EMOJI: dict[str, str] = {
    "urgent": ":red_circle:",
    "normal": ":large_blue_circle:",
    "info": ":white_circle:",
}

# Slack emoji for source icons
_SOURCE_EMOJI: dict[str, str] = {
    "cc": ":robot_face:",
    "codex": ":gear:",
    "claude_ai": ":brain:",
    "bridge_watcher": ":bridge_at_night:",
}

_PUSH_NOTIFIER_CANDIDATES: tuple[str, ...] = (
    "/opt/homebrew/bin/terminal-notifier",
    "/usr/local/bin/terminal-notifier",
)


class SlackTextObject(TypedDict):
    """Minimal mrkdwn text object used in Slack Block Kit payloads."""

    type: str
    text: str


class SlackSectionBlock(TypedDict):
    """Slack section block used for one-message payloads."""

    type: str
    text: SlackTextObject


class SlackPayload(TypedDict):
    """Typed Slack payload used by channel formatters and tests."""

    blocks: list[SlackSectionBlock]
    text: str


def ensure_log_dir() -> None:
    """Create the events log directory with restricted permissions."""
    EVENTS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)


def find_push_notifier() -> str | None:
    """Find terminal-notifier from PATH or common macOS install locations."""
    if notifier := shutil.which("terminal-notifier"):
        return notifier

    for candidate in _PUSH_NOTIFIER_CANDIDATES:
        if Path(candidate).exists():
            return candidate

    return None


def has_push_notifier() -> bool:
    """Return whether terminal-notifier is available on this machine."""
    return find_push_notifier() is not None


def write_jsonl(event: StoredEvent) -> None:
    """Append an event to the JSONL log file."""
    ensure_log_dir()
    line = event.model_dump_json() + "\n"
    fd = os.open(str(EVENTS_LOG), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
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
    notifier = find_push_notifier()
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


def format_slack_message(event: StoredEvent) -> SlackPayload:
    """Build a Slack Block Kit message payload for an event."""
    level = event.classified_level or event.level
    level_emoji = _LEVEL_EMOJI.get(level, ":white_circle:")
    source_emoji = _SOURCE_EMOJI.get(event.source, ":question:")
    source_label = _SOURCE_LABELS.get(event.source, event.source)

    project_tag = f" — `{event.project}`" if event.project else ""
    ts = event.timestamp.strftime("%Y-%m-%d %H:%M UTC")

    text = (
        f"{level_emoji} *{event.title}*{project_tag}\n"
        f"{event.body}\n"
        f"_{source_emoji} {source_label} • {ts}_"
    )

    return {
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text},
            }
        ],
        "text": f"[{level.upper()}] {event.title}",  # fallback for plain-text clients
    }


def format_slack_digest(events: list[StoredEvent]) -> SlackPayload:
    """Build a Slack digest message for multiple batched events."""
    lines: list[str] = []
    for event in events:
        level = event.classified_level or event.level
        emoji = _LEVEL_EMOJI.get(level, ":white_circle:")
        project = f"`{event.project}` " if event.project else ""
        lines.append(f"{emoji} {project}*{event.title}*: {event.body[:80]}")

    text = f":package: *Notification Digest* ({len(events)} events)\n\n" + "\n".join(lines)

    return {
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text},
            }
        ],
        "text": f"Notification digest: {len(events)} events",
    }


def send_slack(event: StoredEvent) -> bool:
    """Send a single event to Slack via webhook. Returns True if sent."""
    webhook_url = get_slack_webhook_url()
    if webhook_url is None:
        logger.warning("No Slack webhook configured, skipping event %s", event.event_id)
        return False

    payload = format_slack_message(event)
    try:
        resp = httpx.post(webhook_url, json=payload, timeout=10)
        if resp.status_code == 200:
            logger.info("Slack message sent for event %s", event.event_id)
            return True
        logger.warning("Slack webhook returned %d for %s", resp.status_code, event.event_id)
        return False
    except Exception as exc:
        logger.warning("Slack send failed for %s: %s", event.event_id, exc)
        return False


def send_slack_digest(events: list[StoredEvent]) -> bool:
    """Send a digest of multiple events to Slack. Returns True if sent."""
    if not events:
        return True

    webhook_url = get_slack_webhook_url()
    if webhook_url is None:
        logger.warning("No Slack webhook configured, skipping digest of %d events", len(events))
        return False

    payload = format_slack_digest(events)
    try:
        resp = httpx.post(webhook_url, json=payload, timeout=10)
        if resp.status_code == 200:
            logger.info("Slack digest sent: %d events", len(events))
            return True
        logger.warning("Slack digest webhook returned %d", resp.status_code)
        return False
    except Exception as exc:
        logger.warning("Slack digest failed: %s", exc)
        return False
