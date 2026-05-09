"""Tests for coordination intent helpers."""

from __future__ import annotations

from notification_hub.coordination import infer_intent
from notification_hub.models import Event


def test_infer_intent_uses_explicit_event_intent() -> None:
    event = Event(
        source="codex",
        level="info",
        title="FYI",
        body="Routine update",
        intent="ready_to_merge",
    )

    assert infer_intent(event) == "ready_to_merge"


def test_infer_intent_maps_operator_phrases() -> None:
    event = Event(
        source="codex",
        level="normal",
        title="Review ready",
        body="Ready to review the implementation",
    )

    assert infer_intent(event) == "ready_to_review"


def test_infer_intent_falls_back_to_urgent_attention() -> None:
    event = Event(source="personal-ops", level="urgent", title="Ping", body="Look here")

    assert infer_intent(event) == "needs_attention"
