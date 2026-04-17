"""Deterministic urgency classification rules engine."""

from __future__ import annotations

from notification_hub.config import (
    DEFAULT_INFO_KEYWORDS,
    DEFAULT_NORMAL_KEYWORDS,
    DEFAULT_URGENT_KEYWORDS,
    get_policy_config,
)
from notification_hub.models import Event, Level

# Public defaults preserved for tests and diagnostics.
URGENT_KEYWORDS: tuple[str, ...] = DEFAULT_URGENT_KEYWORDS
NORMAL_KEYWORDS: tuple[str, ...] = DEFAULT_NORMAL_KEYWORDS
INFO_KEYWORDS: tuple[str, ...] = DEFAULT_INFO_KEYWORDS


def classify(event: Event) -> Level:
    """Classify an event's urgency. Returns the determined level.

    Priority: urgent > normal > info (explicit source level is a hint,
    but keywords can escalate or demote).
    """
    text = f"{event.title} {event.body}".lower()
    policy = get_policy_config().classification

    if any(kw in text for kw in policy.urgent_keywords):
        return "urgent"

    if any(kw in text for kw in policy.info_keywords):
        return "info"

    if any(kw in text for kw in policy.normal_keywords):
        return "normal"

    # Fall back to the level provided by the source
    return event.level
