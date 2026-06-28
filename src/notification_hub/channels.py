"""Delivery channels for notification events."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import TypedDict

import httpx

from notification_hub.config import EVENTS_DIR, EVENTS_LOG, get_slack_webhook_url
from notification_hub.models import StoredEvent

logger = logging.getLogger(__name__)

# Slack delivery retry policy. Transient failures (network errors, timeouts,
# HTTP 429, and 5xx) are retried with exponential backoff; permanent client
# errors (other 4xx) and success short-circuit immediately.
_SLACK_TIMEOUT_SECONDS = 10.0
_SLACK_MAX_ATTEMPTS = 3
_SLACK_RETRY_BASE_SECONDS = 0.5
_SLACK_RETRY_MAX_SECONDS = 30.0

# Source labels for push notification subtitles
_SOURCE_LABELS: dict[str, str] = {
    "cc": "Claude Code",
    "codex": "Codex",
    "claude_ai": "Claude.ai",
    "bridge_watcher": "Bridge",
    "personal-ops": "Personal Ops",
    "notion-os": "Notion OS",
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
    "personal-ops": ":clipboard:",
    "notion-os": ":memo:",
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


def _parse_retry_after(resp: httpx.Response) -> float | None:
    """Return the Retry-After header in seconds (capped), or None if absent/invalid."""
    raw = resp.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    return min(max(seconds, 0.0), _SLACK_RETRY_MAX_SECONDS)


def _post_to_slack(webhook_url: str, payload: SlackPayload, description: str) -> bool:
    """POST a payload to a Slack webhook with bounded retry on transient failures.

    Retries network errors, timeouts, HTTP 429, and 5xx with exponential backoff
    (honoring Retry-After on 429). Returns immediately on a 200 or a permanent
    client error (other 4xx). Returns True only when Slack accepts the payload.
    """
    for attempt in range(1, _SLACK_MAX_ATTEMPTS + 1):
        retry_after: float | None = None
        try:
            resp = httpx.post(webhook_url, json=payload, timeout=_SLACK_TIMEOUT_SECONDS)
        except httpx.TransportError as exc:
            # Covers all timeout + network variants; retry these.
            transient = True
            reason = type(exc).__name__
        except Exception as exc:
            # Permanent/setup error (bad URL, unsupported protocol). Log the type
            # only — exception messages can embed the webhook URL (a bearer token).
            logger.warning("Slack %s failed (%s); not retrying", description, type(exc).__name__)
            return False
        else:
            if resp.status_code == 200:
                logger.info("Slack %s sent (attempt %d)", description, attempt)
                return True
            transient = resp.status_code == 429 or resp.status_code >= 500
            reason = f"HTTP {resp.status_code}"
            if resp.status_code == 429:
                retry_after = _parse_retry_after(resp)

        if not transient or attempt == _SLACK_MAX_ATTEMPTS:
            logger.warning("Slack %s failed (%s) after %d attempt(s)", description, reason, attempt)
            return False

        delay = (
            retry_after
            if retry_after is not None
            else min(_SLACK_RETRY_BASE_SECONDS * (2 ** (attempt - 1)), _SLACK_RETRY_MAX_SECONDS)
        )
        logger.warning(
            "Slack %s transient failure (%s); retrying in %.1fs (attempt %d/%d)",
            description,
            reason,
            delay,
            attempt,
            _SLACK_MAX_ATTEMPTS,
        )
        time.sleep(delay)
    return False  # defensive: the loop always returns on the final attempt


def send_slack(event: StoredEvent) -> bool:
    """Send a single event to Slack via webhook. Returns True if sent."""
    webhook_url = get_slack_webhook_url()
    if webhook_url is None:
        logger.warning("No Slack webhook configured, skipping event %s", event.event_id)
        return False
    return _post_to_slack(webhook_url, format_slack_message(event), f"event {event.event_id}")


def send_slack_digest(events: list[StoredEvent]) -> bool:
    """Send a digest of multiple events to Slack. Returns True if sent."""
    if not events:
        return True

    webhook_url = get_slack_webhook_url()
    if webhook_url is None:
        logger.warning("No Slack webhook configured, skipping digest of %d events", len(events))
        return False

    return _post_to_slack(
        webhook_url, format_slack_digest(events), f"digest of {len(events)} events"
    )
