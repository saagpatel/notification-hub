"""Event processing pipeline: classify → route → deliver."""

from __future__ import annotations

import logging

from notification_hub.channels import send_push, write_jsonl
from notification_hub.classifier import classify
from notification_hub.models import Event, StoredEvent

logger = logging.getLogger(__name__)


def process_event(event: Event) -> StoredEvent:
    """Full pipeline: create stored event, classify, log, and route to channels.

    All events are written to JSONL. Routing by classified level:
    - urgent: JSONL + terminal-notifier push with sound
    - normal: JSONL only (Slack added in Phase 2)
    - info: JSONL only
    """
    classified_level = classify(event)
    stored = StoredEvent(
        **event.model_dump(),
        classified_level=classified_level,
    )

    # Always log
    write_jsonl(stored)
    logger.info(
        "Event %s: %s [source=%s, classified=%s]",
        stored.event_id,
        stored.title,
        stored.level,
        classified_level,
    )

    # Route based on classified level
    if classified_level == "urgent":
        send_push(stored)

    return stored
