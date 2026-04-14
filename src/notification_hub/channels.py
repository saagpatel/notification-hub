"""Delivery channels for notification events."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from notification_hub.config import EVENTS_DIR, EVENTS_LOG
from notification_hub.models import StoredEvent

logger = logging.getLogger(__name__)


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
