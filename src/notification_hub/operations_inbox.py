"""Inbox item and rollup shaping helpers."""

from __future__ import annotations

from notification_hub.coordination import infer_intent
from notification_hub.models import StoredEvent
from notification_hub.operations_types import InboxItemReport, InboxRollupReport


def inbox_item(event: StoredEvent) -> InboxItemReport:
    return {
        "event_id": event.event_id,
        "timestamp": event.timestamp.isoformat(),
        "source": event.source,
        "project": event.project,
        "level": event.classified_level or event.level,
        "intent": infer_intent(event),
        "title": event.title,
        "body": event.body,
    }


def build_inbox_rollups(events: list[StoredEvent]) -> list[InboxRollupReport]:
    grouped: dict[tuple[str, str | None, str, str, str, str], list[StoredEvent]] = {}
    for event in events:
        intent = infer_intent(event)
        key = (event.source, event.project, intent, event.level, event.title, event.body)
        grouped.setdefault(key, []).append(event)

    rollups: list[InboxRollupReport] = []
    for (source, project, intent, level, title, body), items in grouped.items():
        if len(items) < 2:
            continue
        latest = max(items, key=lambda item: item.timestamp)
        rollups.append(
            {
                "count": len(items),
                "source": source,
                "project": project,
                "intent": intent,
                "level": level,
                "title": title,
                "body": body,
                "latest_timestamp": latest.timestamp.isoformat(),
                "latest_event_id": latest.event_id,
                "latest_context": dict(latest.context),
            }
        )

    return sorted(
        rollups,
        key=lambda item: (item["count"], item["latest_timestamp"]),
        reverse=True,
    )


def build_near_rollup_singles(events: list[StoredEvent]) -> list[InboxRollupReport]:
    """Return count=1 events that are invisible to the repeated-rollup pipeline."""
    grouped: dict[tuple[str, str | None, str, str, str, str], list[StoredEvent]] = {}
    for event in events:
        intent = infer_intent(event)
        key = (event.source, event.project, intent, event.level, event.title, event.body)
        grouped.setdefault(key, []).append(event)

    singles: list[InboxRollupReport] = []
    for (source, project, intent, level, title, body), items in grouped.items():
        if len(items) != 1:
            continue
        item = items[0]
        singles.append(
            {
                "count": 1,
                "source": source,
                "project": project,
                "intent": intent,
                "level": level,
                "title": title,
                "body": body,
                "latest_timestamp": item.timestamp.isoformat(),
                "latest_event_id": item.event_id,
                "latest_context": dict(item.context),
            }
        )

    return sorted(singles, key=lambda item: item["latest_timestamp"], reverse=True)


def intent_bucket(intent: str) -> str:
    if intent in ("needs_attention", "automation_failed"):
        return "needs_attention"
    if intent in ("blocked", "waiting_on_user"):
        return "waiting_or_blocked"
    if intent in ("ready_to_review", "ready_to_merge", "handoff_created"):
        return "ready"
    if intent == "completed":
        return "completed"
    return "informational"
