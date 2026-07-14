"""Event processing pipeline: classify → suppress → route → deliver."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

from notification_hub.channels import (
    ChannelDeliveryResult,
    send_push,
    send_push_with_result,
    send_slack,
    send_slack_digest,
    send_slack_with_result,
    write_jsonl,
)
from notification_hub.classifier import ClassificationDecision, explain_classification
from notification_hub.config import (
    RoutingRule,
    get_policy_config,
    has_slack_webhook_configured,
    iter_routing_rules_in_evaluation_order,
)
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
    matched_rule_indices: tuple[int, ...]
    matched_rules: tuple[RoutingRule, ...]
    reason: str


@dataclass(frozen=True)
class EventExplanation:
    classification: ClassificationDecision
    routing: RoutingDecision
    log_delivery: bool
    push_delivery: bool
    slack_delivery: bool


@dataclass(frozen=True)
class PipelineProcessResult:
    event: StoredEvent
    outcome: Literal["processed", "suppressed"]


class DeliveryError(RuntimeError):
    """Raised when required channel delivery fails in durable-worker mode."""


class DeliveryDeferred(RuntimeError):
    """Raised when a durable delivery must wait without consuming retry budget."""

    def __init__(self, retry_at: datetime, channel: str) -> None:
        super().__init__(f"{channel} delivery deferred until {retry_at.isoformat()}")
        self.retry_at = retry_at
        self.channel = channel


class QueueCapacityError(RuntimeError):
    """Raised when a legacy in-memory buffer cannot accept an event."""


def _resolve_routing(event: Event, classified_level: Level) -> RoutingDecision:
    """Apply routing rules to the classified event, stopping unless a rule opts to continue."""
    policy = get_policy_config().routing
    title_text = event.title.lower()
    body_text = event.body.lower()
    combined_text = f"{title_text} {body_text}"
    effective_level = classified_level
    allow_push = True
    allow_slack = True
    matched_indices: list[int] = []
    matched_rules: list[RoutingRule] = []
    for index, rule in iter_routing_rules_in_evaluation_order(policy.rules):
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

        if rule.force_level is not None:
            effective_level = cast(Level, rule.force_level)
        if rule.disable_push:
            allow_push = False
        if rule.disable_slack:
            allow_slack = False
        matched_indices.append(index)
        matched_rules.append(rule)

        if not rule.continue_matching:
            if len(matched_indices) == 1:
                reason = f"matched routing rule {index}"
            else:
                reason = (
                    f"matched routing rules {', '.join(str(value) for value in matched_indices)}; "
                    f"stopped at rule {index}"
                )
            return RoutingDecision(
                level=effective_level,
                allow_push=allow_push,
                allow_slack=allow_slack,
                matched_rule_index=index,
                matched_rule=rule,
                matched_rule_indices=tuple(matched_indices),
                matched_rules=tuple(matched_rules),
                reason=reason,
            )

    if matched_indices:
        return RoutingDecision(
            level=effective_level,
            allow_push=allow_push,
            allow_slack=allow_slack,
            matched_rule_index=matched_indices[-1],
            matched_rule=matched_rules[-1],
            matched_rule_indices=tuple(matched_indices),
            matched_rules=tuple(matched_rules),
            reason=f"matched routing rules {', '.join(str(value) for value in matched_indices)}",
        )

    return RoutingDecision(
        level=classified_level,
        allow_push=True,
        allow_slack=True,
        matched_rule_index=None,
        matched_rule=None,
        matched_rule_indices=(),
        matched_rules=(),
        reason="no routing rule matched",
    )


def explain_event(event: Event) -> EventExplanation:
    """Explain how an event would classify, route, and deliver without side effects."""
    classification = explain_classification(event)
    routing = _resolve_routing(event, classification.output_level)
    required_destinations = frozenset(event.required_destinations)
    has_explicit_destination_contract = bool(required_destinations)
    push_requested = (
        "push" in required_destinations
        if has_explicit_destination_contract
        else routing.level == "urgent"
    )
    slack_requested = (
        "slack" in required_destinations
        if has_explicit_destination_contract
        else routing.level in ("urgent", "normal")
    )
    push_delivery = push_requested and routing.allow_push
    slack_delivery = slack_requested and routing.allow_slack
    return EventExplanation(
        classification=classification,
        routing=routing,
        # JSONL is the mandatory local audit boundary. A non-empty producer
        # destination contract constrains external channels; it cannot disable
        # durable local evidence.
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
        "continue_matching": rule.continue_matching,
        "priority": rule.priority,
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
            "matched_rule_indices": list(explanation.routing.matched_rule_indices),
            "matched_rules": [
                _routing_rule_to_dict(rule) for rule in explanation.routing.matched_rules
            ],
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


def build_stored_event(event: Event) -> StoredEvent:
    """Assign server metadata and the current classified delivery level."""
    explanation = explain_event(event)
    payload = event.model_dump()
    requested_event_id = payload.pop("event_id", None)
    payload["producer"] = payload.get("producer") or event.source
    # `timestamp` defaults at validation time, so excluding it keeps a producer retry
    # idempotent even when the producer does not supply a logical timestamp.
    digest_payload = event.model_dump(mode="json", exclude={"event_id", "timestamp"})
    # Preserve the pre-producer-field digest contract for legacy callers while retaining an
    # explicit producer in the canonical stored envelope.
    if digest_payload.get("producer") is None:
        digest_payload.pop("producer", None)
    payload_digest = hashlib.sha256(
        json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return StoredEvent(
        **payload,
        event_id=requested_event_id or uuid.uuid4().hex[:12],
        classified_level=explanation.routing.level,
        payload_digest=payload_digest,
    )


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
            if not _suppression.add_to_overflow(event):
                raise QueueCapacityError("overflow queue full while restoring failed digest")
        logger.warning(
            "Failed to flush overflow digest; returning %d events to buffer", len(overflow)
        )


def _drain_quiet_queue_if_needed() -> None:
    """If quiet hours just ended and there are queued events, deliver them."""
    if _suppression.is_quiet_hours():
        return
    queued = _suppression.drain_quiet_queue()
    for event in queued:
        _deliver_push(event)
        logger.info("Morning delivery: push for %s", event.event_id)


def _deliver_push(event: StoredEvent, *, allow_memory_buffer: bool = True) -> bool:
    """Send a push notification with rate limiting. Overflow goes to buffer."""
    if _suppression.check_push_rate():
        if send_push(event):
            _suppression.record_push()
            return True
        logger.warning("Push delivery failed for %s", event.event_id)
        return False
    else:
        if not allow_memory_buffer:
            logger.info("Push rate-limited; durable event %s remains pending", event.event_id)
            return False
        if not _suppression.add_to_overflow(event):
            raise QueueCapacityError("push overflow queue full")
        logger.debug("Push rate-limited, event %s added to overflow", event.event_id)
        return True


def _deliver_slack(event: StoredEvent, *, allow_memory_buffer: bool = True) -> bool:
    """Send a Slack message with rate limiting. Overflow goes to buffer."""
    if not _slack_delivery_enabled():
        return True

    # Try to flush any pending overflow first
    if allow_memory_buffer:
        _flush_overflow()

    if _suppression.check_slack_rate():
        if send_slack(event):
            _suppression.record_slack()
            return True
        logger.warning("Slack delivery failed for %s", event.event_id)
        return False
    else:
        if not allow_memory_buffer:
            logger.info("Slack rate-limited; durable event %s remains pending", event.event_id)
            return False
        if not _suppression.add_to_overflow(event):
            raise QueueCapacityError("slack overflow queue full")
        logger.debug("Slack rate-limited, event %s added to overflow", event.event_id)
        return True


def _deliver_push_durable(event: StoredEvent) -> ChannelDeliveryResult:
    """Attempt push without memory buffering and preserve transport evidence."""
    if not _suppression.check_push_rate():
        logger.info("Push rate-limited; durable event %s remains pending", event.event_id)
        return ChannelDeliveryResult(False, error_category="push_rate_limited")
    result = send_push_with_result(event)
    if result.accepted:
        _suppression.record_push()
    else:
        logger.warning("Push delivery failed for %s", event.event_id)
    return result


def _deliver_slack_durable(event: StoredEvent) -> ChannelDeliveryResult:
    """Attempt Slack without memory buffering and preserve transport evidence."""
    if not _suppression.check_slack_rate():
        logger.info("Slack rate-limited; durable event %s remains pending", event.event_id)
        return ChannelDeliveryResult(False, error_category="slack_rate_limited")
    result = send_slack_with_result(event)
    if result.accepted:
        _suppression.record_slack()
    else:
        logger.warning("Slack delivery failed for %s", event.event_id)
    return result


def process_stored_event_with_result(
    event: StoredEvent,
    *,
    raise_on_delivery_failure: bool = False,
    skip_duplicate_suppression: bool = False,
    skip_channels: frozenset[str] = frozenset(),
    channel_state_recorder: Callable[[str, str, str | None], None] | None = None,
    durable_mode: bool = False,
) -> PipelineProcessResult:
    """Full pipeline for a durable event, returning the persistence outcome.

    Accepted non-burst events are written to JSONL. Routing by classified level:
    - urgent: JSONL + push (with sound) + Slack
    - normal: JSONL + Slack
    - info: JSONL only

    Suppression layers:
    - Burst dedup: exact repeated local producer fan-out = skip storage/delivery
    - Dedup: same (project, classified_level) within 30 min = skip delivery
    - Quiet hours: push suppressed 11 PM-7 AM Pacific, queued for morning
    - Rate limit: max 5 push/hr, max 20 Slack/hr, overflow batched into digest
    """
    explanation = explain_event(event)
    routing = explanation.routing
    payload = event.model_dump()
    payload["classified_level"] = routing.level
    stored = StoredEvent.model_validate(payload)

    # Exact producer burst suppression happens before JSONL audit logging so
    # repeated local reminder fan-out does not flood processed-event history.
    burst_predecessor = (
        None if skip_duplicate_suppression else _suppression.burst_duplicate_predecessor(stored)
    )
    if burst_predecessor is not None:
        stored = stored.model_copy(
            update={
                "suppression_predecessor_id": burst_predecessor,
                "suppression_policy": "explicit-noise-rule-exact-burst",
            }
        )
        logger.debug("Event %s suppressed by burst dedup before storage", stored.event_id)
        return PipelineProcessResult(event=stored, outcome="suppressed")

    # Check for morning delivery of queued events
    _drain_quiet_queue_if_needed()

    semantic_predecessor = (
        None if skip_duplicate_suppression else _suppression.semantic_duplicate_predecessor(stored)
    )
    if semantic_predecessor is not None:
        stored = stored.model_copy(
            update={
                "suppression_predecessor_id": semantic_predecessor,
                "suppression_policy": "producer-semantic-key-window",
            }
        )
        logger.debug("Event %s suppressed by dedup", stored.event_id)
        write_jsonl(stored)
        logger.info(
            "Event %s: %s [source=%s, classified=%s]",
            stored.event_id,
            stored.title,
            stored.level,
            routing.level,
        )
        return PipelineProcessResult(event=stored, outcome="suppressed")

    delivery_failed = False
    deferred: DeliveryDeferred | None = None

    def deliver(
        channel: str, sender: Callable[[StoredEvent], bool | ChannelDeliveryResult]
    ) -> bool:
        if channel in skip_channels:
            return True
        if channel_state_recorder is not None:
            channel_state_recorder(channel, "attempted", None)
        raw_result = sender(stored)
        result = (
            raw_result
            if isinstance(raw_result, ChannelDeliveryResult)
            else ChannelDeliveryResult(
                raw_result,
                receipt=(
                    "terminal-notifier:exit:0"
                    if raw_result and channel == "push"
                    else "slack:webhook:http:2xx"
                    if raw_result and channel == "slack"
                    else None
                ),
                error_category=None if raw_result else f"{channel}_transport_failed",
            )
        )
        if channel_state_recorder is not None:
            evidence = result.receipt if result.accepted else result.error_category
            channel_state_recorder(channel, "accepted" if result.accepted else "failed", evidence)
        return result.accepted

    # A non-empty required_destinations list is an exact external-channel
    # contract. Empty lists retain legacy severity routing. Local routing rules
    # can still disable a requested channel, and JSONL audit remains mandatory.
    if explanation.push_delivery and _suppression.is_quiet_hours():
        if durable_mode:
            if channel_state_recorder is not None:
                channel_state_recorder("push", "buffered", "quiet_hours")
            deferred = DeliveryDeferred(_suppression.next_quiet_end(), "push")
        else:
            if not _suppression.queue_for_morning(stored):
                raise QueueCapacityError("quiet-hours queue full")
    elif explanation.push_delivery:
        delivery_failed = (
            not deliver(
                "push",
                _deliver_push_durable
                if durable_mode
                else lambda value: _deliver_push(value, allow_memory_buffer=True),
            )
            or delivery_failed
        )

    if explanation.slack_delivery and (not durable_mode or _slack_delivery_enabled()):
        delivery_failed = (
            not deliver(
                "slack",
                _deliver_slack_durable
                if durable_mode
                else lambda value: _deliver_slack(value, allow_memory_buffer=True),
            )
            or delivery_failed
        )

    if deferred is not None:
        raise deferred
    if delivery_failed and raise_on_delivery_failure:
        raise DeliveryError(f"delivery failed for event {stored.event_id}")

    write_jsonl(stored)
    logger.info(
        "Event %s: %s [source=%s, classified=%s]",
        stored.event_id,
        stored.title,
        stored.level,
        routing.level,
    )

    return PipelineProcessResult(event=stored, outcome="processed")


def process_stored_event(event: StoredEvent) -> StoredEvent:
    """Process a stored event through the delivery pipeline."""
    return process_stored_event_with_result(event).event


def process_event(event: Event) -> StoredEvent:
    """Assign metadata and process an event through the delivery pipeline."""
    return process_stored_event(build_stored_event(event))
