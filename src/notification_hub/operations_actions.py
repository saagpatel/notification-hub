"""Action proposal shaping and evidence-quality helpers."""

from __future__ import annotations

import hashlib
import json
import re
from typing import cast

from notification_hub.operations_types import InboxRollupReport, PersonalOpsActionReport

ACTION_PROPOSAL_MIN_CANDIDATES = 25
ACTION_PROPOSAL_CANDIDATE_MULTIPLIER = 5


def _as_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    return {}


def _as_str(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _action_priority(intent: str, level: str) -> str:
    if intent in {"needs_attention", "blocked", "automation_failed"} or level == "urgent":
        return "high"
    if intent in {"waiting_on_user", "ready_to_review", "ready_to_merge"}:
        return "medium"
    return "low"


def _action_state(intent: str) -> str:
    if intent in {"blocked", "waiting_on_user"}:
        return "waiting"
    if intent in {"ready_to_review", "ready_to_merge"}:
        return "ready"
    if intent == "completed":
        return "done"
    return "open"


def _suggested_action(intent: str, title: str) -> str:
    if intent == "blocked":
        return "Review blocker and decide the next unblock step."
    if intent == "waiting_on_user":
        return "Review the waiting item and approve, reply, or dismiss it."
    if intent in {"ready_to_review", "ready_to_merge"}:
        return "Review the ready work and decide whether to land it."
    if intent == "automation_failed":
        return "Inspect the failed automation and rerun or repair it."
    if intent == "needs_attention":
        return "Review the attention item and choose the next operator action."
    if intent == "completed":
        return "Archive or use as recent completion context."
    return f"Review repeated signal: {title}."


def proposal_dismissal_key(rollup: InboxRollupReport) -> str:
    stable_parts = {
        "source": rollup["source"],
        "project": rollup["project"] or "",
        "intent": rollup["intent"],
        "level": rollup["level"],
        "title": rollup["title"],
        "body": rollup["body"],
    }
    digest = hashlib.sha256(json.dumps(stable_parts, sort_keys=True).encode("utf-8")).hexdigest()[
        :16
    ]
    source_part = re.sub(r"[^a-z0-9]+", "-", rollup["source"].lower()).strip("-") or "source"
    project_part = (
        re.sub(r"[^a-z0-9]+", "-", (rollup["project"] or "general").lower()).strip("-")
        or "general"
    )
    intent_part = re.sub(r"[^a-z0-9]+", "-", rollup["intent"].lower()).strip("-") or "intent"
    return f"proposal:{source_part}:{project_part}:{intent_part}:{digest}"


def action_from_rollup(rollup: InboxRollupReport) -> PersonalOpsActionReport:
    project_part = rollup["project"] or "general"
    normalized_title = re.sub(r"[^a-z0-9]+", "-", rollup["title"].lower()).strip("-") or "signal"
    evidence_part = (
        re.sub(r"[^a-z0-9]+", "-", rollup["latest_event_id"].lower()).strip("-") or "event"
    )
    action_id = (
        f"notification-hub:{rollup['source']}:{project_part}:"
        f"{rollup['intent']}:{normalized_title}:{evidence_part}"
    )
    evidence_context = dict(rollup.get("latest_context", {}))
    return {
        "action_id": action_id,
        "dismissal_key": proposal_dismissal_key(rollup),
        "source": rollup["source"],
        "project": rollup["project"],
        "intent": rollup["intent"],
        "priority": _action_priority(rollup["intent"], rollup["level"]),
        "state": _action_state(rollup["intent"]),
        "title": rollup["title"],
        "summary": f"{rollup['count']} repeated {rollup['source']} events: {rollup['body']}",
        "signal_level": rollup["level"],
        "signal_body": rollup["body"],
        "suggested_next_action": _suggested_action(rollup["intent"], rollup["title"]),
        "evidence_event_id": rollup["latest_event_id"],
        "evidence_timestamp": rollup["latest_timestamp"],
        "evidence_context": evidence_context,
        "evidence_quality": evidence_quality(evidence_context),
        "count": rollup["count"],
    }


def action_proposal_candidate_limit(limit: int) -> int:
    item_limit = max(limit, 1)
    return max(
        ACTION_PROPOSAL_MIN_CANDIDATES,
        item_limit * ACTION_PROPOSAL_CANDIDATE_MULTIPLIER,
    )


def evidence_quality(context: dict[str, object] | None) -> str:
    if not context:
        return "thin"
    keys = {key for key, value in context.items() if value not in ("", None)}
    has_anchor = bool(keys & {"thread_id", "message_id"})
    has_work_item = bool(
        keys & {"draft_id", "approval_id", "provider_draft_id", "review_id", "queue_id"}
    )
    return "rich" if has_anchor and has_work_item else "thin"


def action_evidence_quality(action: PersonalOpsActionReport) -> str:
    quality = _as_str(cast(dict[str, object], action).get("evidence_quality"))
    if quality in {"rich", "thin"}:
        return quality
    return evidence_quality(action.get("evidence_context"))


def raw_queue_item_evidence_quality(item: dict[str, object]) -> str:
    action = _as_dict(item.get("action"))
    quality = _as_str(action.get("evidence_quality"))
    if quality in {"rich", "thin"}:
        return quality
    return evidence_quality(_as_dict(action.get("evidence_context")))
