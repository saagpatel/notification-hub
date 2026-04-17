"""Deterministic urgency classification rules engine."""

from __future__ import annotations

from dataclasses import dataclass

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


@dataclass(frozen=True)
class ClassificationDecision:
    input_level: Level
    output_level: Level
    reason: str
    matched_keyword: str | None
    matched_group: str


def _first_matching_keyword(text: str, keywords: tuple[str, ...]) -> str | None:
    """Return the first keyword that matches the event text."""
    for keyword in keywords:
        if keyword in text:
            return keyword
    return None


def explain_classification(event: Event) -> ClassificationDecision:
    """Explain how an event's urgency was classified."""
    text = f"{event.title} {event.body}".lower()
    policy = get_policy_config().classification

    urgent_keyword = _first_matching_keyword(text, policy.urgent_keywords)
    if urgent_keyword is not None:
        return ClassificationDecision(
            input_level=event.level,
            output_level="urgent",
            reason="matched urgent keyword",
            matched_keyword=urgent_keyword,
            matched_group="urgent",
        )

    info_keyword = _first_matching_keyword(text, policy.info_keywords)
    if info_keyword is not None:
        return ClassificationDecision(
            input_level=event.level,
            output_level="info",
            reason="matched info keyword",
            matched_keyword=info_keyword,
            matched_group="info",
        )

    normal_keyword = _first_matching_keyword(text, policy.normal_keywords)
    if normal_keyword is not None:
        return ClassificationDecision(
            input_level=event.level,
            output_level="normal",
            reason="matched normal keyword",
            matched_keyword=normal_keyword,
            matched_group="normal",
        )

    return ClassificationDecision(
        input_level=event.level,
        output_level=event.level,
        reason="fell back to source level",
        matched_keyword=None,
        matched_group="source_level",
    )


def classify(event: Event) -> Level:
    """Classify an event's urgency. Returns the determined level.

    Priority: urgent > normal > info (explicit source level is a hint,
    but keywords can escalate or demote).
    """
    return explain_classification(event).output_level
