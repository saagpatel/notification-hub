"""Event processing pipeline: classify → suppress → route → deliver."""

from __future__ import annotations

import logging

from notification_hub.channels import send_push, send_slack, send_slack_digest, write_jsonl
from notification_hub.classifier import classify
from notification_hub.config import has_slack_webhook_configured
from notification_hub.models import Event, StoredEvent
from notification_hub.suppression import SuppressionEngine

logger = logging.getLogger(__name__)

# Module-level singleton — lives for the server's lifetime
_suppression = SuppressionEngine()
_slack_unconfigured_logged = False


def get_suppression_engine() -> SuppressionEngine:
    """Access the suppression engine. Exposed for testing."""
    return _suppression


def reset_suppression_engine() -> None:
    """Replace the suppression engine with a fresh instance. Used in tests."""
    global _suppression, _slack_unconfigured_logged
    _suppression = SuppressionEngine()
    _slack_unconfigured_logged = False


def _slack_delivery_enabled() -> bool:
    """Return whether Slack delivery is configured, logging the disabled state only once."""
    global _slack_unconfigured_logged
    if has_slack_webhook_configured():
        _slack_unconfigured_logged = False
        return True

    if not _slack_unconfigured_logged:
        logger.info("Slack webhook not configured; Slack delivery disabled")
        _slack_unconfigured_logged = True
    return False


def _flush_overflow() -> None:
    """If the overflow buffer has events and Slack rate allows, send a digest."""
    if not _slack_delivery_enabled():
        return
    if not _suppression.has_overflow():
        return
    if not _suppression.check_slack_rate():
        return
    overflow = _suppression.drain_overflow()
    if overflow:
        if send_slack_digest(overflow):
            _suppression.record_slack()
            logger.info("Flushed overflow digest: %d events", len(overflow))
            return

        # Preserve overflow when digest delivery fails so a later event can retry.
        for event in overflow:
            _suppression.add_to_overflow(event)
        logger.warning("Failed to flush overflow digest; returning %d events to buffer", len(overflow))


def _drain_quiet_queue_if_needed() -> None:
    """If quiet hours just ended and there are queued events, deliver them."""
    if _suppression.is_quiet_hours():
        return
    queued = _suppression.drain_quiet_queue()
    for event in queued:
        _deliver_push(event)
        logger.info("Morning delivery: push for %s", event.event_id)


def _deliver_push(event: StoredEvent) -> None:
    """Send a push notification with rate limiting. Overflow goes to buffer."""
    if _suppression.check_push_rate():
        if send_push(event):
            _suppression.record_push()
            return
        logger.warning("Push delivery failed for %s", event.event_id)
    else:
        _suppression.add_to_overflow(event)
        logger.debug("Push rate-limited, event %s added to overflow", event.event_id)


def _deliver_slack(event: StoredEvent) -> None:
    """Send a Slack message with rate limiting. Overflow goes to buffer."""
    if not _slack_delivery_enabled():
        return

    # Try to flush any pending overflow first
    _flush_overflow()

    if _suppression.check_slack_rate():
        if send_slack(event):
            _suppression.record_slack()
            return
        logger.warning("Slack delivery failed for %s", event.event_id)
    else:
        _suppression.add_to_overflow(event)
        logger.debug("Slack rate-limited, event %s added to overflow", event.event_id)


def process_event(event: Event) -> StoredEvent:
    """Full pipeline: classify, log, suppress, and route to channels.

    All events are written to JSONL. Routing by classified level:
    - urgent: JSONL + push (with sound) + Slack
    - normal: JSONL + Slack
    - info: JSONL only

    Suppression layers:
    - Dedup: same (project, classified_level) within 30 min = skip delivery
    - Quiet hours: push suppressed 11 PM-7 AM Pacific, queued for morning
    - Rate limit: max 5 push/hr, max 20 Slack/hr, overflow batched into digest
    """
    classified_level = classify(event)
    stored = StoredEvent(
        **event.model_dump(),
        classified_level=classified_level,
    )

    # Always log to JSONL regardless of suppression
    write_jsonl(stored)
    logger.info(
        "Event %s: %s [source=%s, classified=%s]",
        stored.event_id,
        stored.title,
        stored.level,
        classified_level,
    )

    # Check for morning delivery of queued events
    _drain_quiet_queue_if_needed()

    # Dedup: stop delivery if duplicate (project, level) within window
    if _suppression.is_duplicate(stored):
        logger.debug("Event %s suppressed by dedup", stored.event_id)
        return stored

    # Route based on classified level
    if classified_level == "urgent":
        # Push: respect quiet hours
        if _suppression.is_quiet_hours():
            _suppression.queue_for_morning(stored)
        else:
            _deliver_push(stored)
        # Slack: always (not affected by quiet hours)
        _deliver_slack(stored)

    elif classified_level == "normal":
        _deliver_slack(stored)

    # info: JSONL only (already logged above)

    return stored
