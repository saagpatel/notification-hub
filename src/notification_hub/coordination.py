"""Deterministic coordination intent helpers."""

from __future__ import annotations

from notification_hub.models import Event, Intent

_INTENT_KEYWORDS: tuple[tuple[Intent, tuple[str, ...]], ...] = (
    ("automation_failed", ("automation failed", "workflow failed", "verification fail", "test regression")),
    ("blocked", ("blocked", "stuck", "cannot proceed", "failed to")),
    ("waiting_on_user", ("waiting on user", "approval needed", "approval requested", "needs approval")),
    ("ready_to_merge", ("ready to merge", "merge ready", "land it", "ship it")),
    ("ready_to_review", ("ready to review", "review ready", "needs review")),
    ("handoff_created", ("handoff created", "handoff ready", "restart prompt")),
    ("completed", ("session complete", "completed", "finished a turn", "merged to main")),
)


def infer_intent(event: Event) -> Intent:
    """Infer the event's coordination intent from structured value or deterministic text rules."""
    if event.intent is not None:
        return event.intent

    text = f"{event.title} {event.body}".lower()
    for intent, keywords in _INTENT_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return intent

    if event.level == "urgent":
        return "needs_attention"
    return "informational"
