"""Deterministic urgency classification rules engine."""

from __future__ import annotations

from notification_hub.models import Event, Level

# Keywords that escalate to urgent — checked against title + body (lowercased)
URGENT_KEYWORDS: tuple[str, ...] = (
    "verification fail",
    "test regression",
    "eval degradation",
    "approval needed",
    "approval required",
    "can_auto_archive=false",
    "security finding",
    "security audit",
    "needs approval",
    "action required",
    "runtime issue",
)

# Keywords that classify as normal
NORMAL_KEYWORDS: tuple[str, ...] = (
    "session complete",
    "automation report",
    "milestone",
    "bridge sync",
    "[shipped]",
    "phase complete",
    "all phases complete",
    "v1.0 done",
    "deployed",
    "released",
    "submitted to app store",
    "published to github",
    "merged to main",
    "production deploy",
)

# Keywords that force info level (override normal if both match)
INFO_KEYWORDS: tuple[str, ...] = (
    "can_auto_archive=true",
    "bridge file read",
    "status update",
    "routine check",
)


def classify(event: Event) -> Level:
    """Classify an event's urgency. Returns the determined level.

    Priority: urgent > normal > info (explicit source level is a hint,
    but keywords can escalate or demote).
    """
    text = f"{event.title} {event.body}".lower()

    if any(kw in text for kw in URGENT_KEYWORDS):
        return "urgent"

    if any(kw in text for kw in INFO_KEYWORDS):
        return "info"

    if any(kw in text for kw in NORMAL_KEYWORDS):
        return "normal"

    # Fall back to the level provided by the source
    return event.level
