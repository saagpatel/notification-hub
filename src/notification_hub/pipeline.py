"""Event processing pipeline: classify → suppress → route → deliver."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import cast

from notification_hub.channels import send_push, send_slack, send_slack_digest, write_jsonl
from notification_hub.classifier import ClassificationDecision, explain_classification
from notification_hub.config import RoutingRule, get_policy_config, has_slack_webhook_configured
from notification_hub.models import Event, Level, StoredEvent
from notification_hub.suppression import SuppressionEngine

logger = logging.getLogger(__name__)

# Module-level singleton — lives for the server's lifetime
_suppression = SuppressionEngine()
_slack_unconfigured_logged = False


@dataclass(frozen=True)
class RoutingDecision:
    level: Level
    allow_push: bool
    allow_slack: bool
    matched_rule_index: int | None
    matched_rule: RoutingRule | None
    reason: str


@dataclass(frozen=True)
class EventExplanation:
    classification: ClassificationDecision
    routing: RoutingDecision
    log_delivery: bool
    push_delivery: bool
    slack_delivery: bool


def _resolve_routing(event: Event, classified_level: Level) -> RoutingDecision:
    """Apply the first matching routing rule, if any, to the classified event."""
    policy = get_policy_config().routing
    title_text = event.title.lower()
    body_text = event.body.lower()
    combined_text = f"{title_text} {body_text}"
    for index, rule in enumerate(policy.rules, start=1):
        if rule.source is not None and rule.source != event.source:
            continue
        if rule.project is not None and rule.project != event.project:
            continue
        if rule.project_prefix is not None:
            if event.project is None or not event.project.startswith(rule.project_prefix):
                continue
        if rule.title_contains is not None and rule.title_contains not in title_text:
            continue
        if rule.body_contains is not None and rule.body_contains not in body_text:
            continue
        if rule.text_contains is not None and rule.text_contains not in combined_text:
            continue

        effective_level = (
            classified_level if rule.force_level is None else cast(Level, rule.force_level)
        )
        return RoutingDecision(
            level=effective_level,
            allow_push=not rule.disable_push,
            allow_slack=not rule.disable_slack,
            matched_rule_index=index,
            matched_rule=rule,
            reason=f"matched routing rule {index}",
        )

    return RoutingDecision(
        level=classified_level,
        allow_push=True,
        allow_slack=True,
        matched_rule_index=None,
        matched_rule=None,
        reason="no routing rule matched",
    )


def explain_event(event: Event) -> EventExplanation:
    """Explain how an event would classify, route, and deliver without side effects."""
    classification = explain_classification(event)
    routing = _resolve_routing(event, classification.output_level)
    push_delivery = routing.level == "urgent" and routing.allow_push
    slack_delivery = routing.level in ("urgent", "normal") and routing.allow_slack
    return EventExplanation(
        classification=classification,
        routing=routing,
        log_delivery=True,
        push_delivery=push_delivery,
        slack_delivery=slack_delivery,
    )


def _routing_rule_to_dict(rule: RoutingRule | None) -> dict[str, object] | None:
    """Convert a routing rule to a JSON-ready dictionary."""
    if rule is None:
        return None
    return {
        "source": rule.source,
        "project": rule.project,
        "project_prefix": rule.project_prefix,
        "title_contains": rule.title_contains,
        "body_contains": rule.body_contains,
        "text_contains": rule.text_contains,
        "force_level": rule.force_level,
        "disable_push": rule.disable_push,
        "disable_slack": rule.disable_slack,
    }


def build_event_explanation_report(event: Event) -> dict[str, object]:
    """Build a JSON-ready explanation report for an event."""
    explanation = explain_event(event)
    return {
        "event": event.model_dump(mode="json"),
        "classification": {
            "input_level": explanation.classification.input_level,
            "output_level": explanation.classification.output_level,
            "reason": explanation.classification.reason,
            "matched_keyword": explanation.classification.matched_keyword,
            "matched_group": explanation.classification.matched_group,
        },
        "routing": {
            "final_level": explanation.routing.level,
            "allow_push": explanation.routing.allow_push,
            "allow_slack": explanation.routing.allow_slack,
            "matched_rule_index": explanation.routing.matched_rule_index,
            "matched_rule": _routing_rule_to_dict(explanation.routing.matched_rule),
            "reason": explanation.routing.reason,
        },
        "delivery": {
            "log": explanation.log_delivery,
            "push": explanation.push_delivery,
            "slack": explanation.slack_delivery,
        },
    }


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
    explanation = explain_event(event)
    routing = explanation.routing
    stored = StoredEvent(
        **event.model_dump(),
        classified_level=routing.level,
    )

    # Always log to JSONL regardless of suppression
    write_jsonl(stored)
    logger.info(
        "Event %s: %s [source=%s, classified=%s]",
        stored.event_id,
        stored.title,
        stored.level,
        routing.level,
    )

    # Check for morning delivery of queued events
    _drain_quiet_queue_if_needed()

    # Dedup: stop delivery if duplicate (project, level) within window
    if _suppression.is_duplicate(stored):
        logger.debug("Event %s suppressed by dedup", stored.event_id)
        return stored

    # Route based on classified level
    if routing.level == "urgent":
        # Push: respect quiet hours
        if routing.allow_push and _suppression.is_quiet_hours():
            _suppression.queue_for_morning(stored)
        elif routing.allow_push:
            _deliver_push(stored)
        # Slack: always (not affected by quiet hours)
        if routing.allow_slack:
            _deliver_slack(stored)

    elif routing.level == "normal":
        if routing.allow_slack:
            _deliver_slack(stored)

    # info: JSONL only (already logged above)

    return stored
