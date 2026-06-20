"""Proposal dismissal and group-history persistence helpers."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from notification_hub.config import EVENTS_DIR
from notification_hub.operations_types import (
    ActionProposalDismissalListReport,
    ActionProposalDismissalReport,
    ActionProposalDismissReport,
    ActionProposalGroupHistoryReport,
    ActionProposalUndismissReport,
    PersonalOpsActionReport,
)

_GENERIC_OPERATION_ERROR = "operation failed; inspect local logs for details"

ACTION_PROPOSAL_DISMISSALS = EVENTS_DIR / "action-proposal-dismissals.jsonl"
ACTION_PROPOSAL_GROUP_HISTORY = EVENTS_DIR / "action-proposal-group-history.jsonl"


def _as_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    return None


def _as_str(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _jsonl_dicts(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    records: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                raw = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(raw, dict):
                records.append(cast(dict[str, object], raw))
    return records


def _jsonl_append(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    os.chmod(path, 0o600)


def _group_history_report(raw: dict[str, object]) -> ActionProposalGroupHistoryReport | None:
    group_key = _as_str(raw.get("group_key"))
    event_type = _as_str(raw.get("event_type"))
    recorded_at = _as_str(raw.get("recorded_at"))
    status = _as_str(raw.get("status"))
    if group_key is None or event_type is None or recorded_at is None or status is None:
        return None
    raw_action_ids = raw.get("action_ids")
    action_ids = (
        [item for item in cast(list[object], raw_action_ids) if isinstance(item, str)]
        if isinstance(raw_action_ids, list)
        else []
    )
    raw_action_keys = raw.get("action_keys")
    action_keys = (
        [item for item in cast(list[object], raw_action_keys) if isinstance(item, str)]
        if isinstance(raw_action_keys, list)
        else []
    )
    return {
        "group_key": group_key,
        "event_type": event_type,
        "recorded_at": recorded_at,
        "status": status,
        "action_count": _as_int(raw.get("action_count")) or len(action_ids),
        "action_ids": action_ids,
        "action_keys": action_keys,
        "package_path": _as_str(raw.get("package_path")),
        "queued_count": _as_int(raw.get("queued_count")),
        "dismissed_count": _as_int(raw.get("dismissed_count")),
        "outcome": _as_str(raw.get("outcome")),
        "reason": _as_str(raw.get("reason")),
        "error": _as_str(raw.get("error")),
    }


def list_action_proposal_group_history(
    *,
    limit: int = 25,
    group_key: str | None = None,
    history_path: Path | None = None,
) -> list[ActionProposalGroupHistoryReport]:
    """Return recent proposal-group lifecycle records without applying work."""
    records: list[ActionProposalGroupHistoryReport] = []
    for raw in reversed(_jsonl_dicts(history_path or ACTION_PROPOSAL_GROUP_HISTORY)):
        report = _group_history_report(raw)
        if report is None:
            continue
        if group_key is not None and report["group_key"] != group_key:
            continue
        records.append(report)
        if len(records) >= max(limit, 1):
            break
    return records


def recent_group_history(
    *,
    since: datetime,
    limit: int,
    history_path: Path | None = None,
) -> list[ActionProposalGroupHistoryReport]:
    records: list[ActionProposalGroupHistoryReport] = []
    for raw in reversed(_jsonl_dicts(history_path or ACTION_PROPOSAL_GROUP_HISTORY)):
        report = _group_history_report(raw)
        if report is None:
            continue
        recorded_at = _parse_iso_datetime(report["recorded_at"])
        if recorded_at is None or recorded_at < since:
            continue
        records.append(report)
        if len(records) >= max(limit, 1):
            break
    return records


def record_action_proposal_group_history(
    *,
    group_key: str,
    event_type: str,
    status: str,
    actions: list[PersonalOpsActionReport],
    package_path: str | None = None,
    queued_count: int | None = None,
    dismissed_count: int | None = None,
    outcome: str | None = None,
    reason: str | None = None,
    error: str | None = None,
    history_path: Path | None = None,
) -> ActionProposalGroupHistoryReport:
    record: ActionProposalGroupHistoryReport = {
        "group_key": group_key,
        "event_type": event_type,
        "recorded_at": datetime.now(UTC).isoformat(),
        "status": status,
        "action_count": len(actions),
        "action_ids": [action["action_id"] for action in actions],
        "action_keys": [action["dismissal_key"] for action in actions],
        "package_path": package_path,
        "queued_count": queued_count,
        "dismissed_count": dismissed_count,
        "outcome": outcome,
        "reason": reason,
        "error": error,
    }
    _jsonl_append(history_path or ACTION_PROPOSAL_GROUP_HISTORY, dict(record))
    return record


def _dismissal_report(raw: dict[str, object]) -> ActionProposalDismissalReport | None:
    dismissal_key = _as_str(raw.get("dismissal_key"))
    deleted_at = _as_str(raw.get("deleted_at"))
    dismissed_at = _as_str(raw.get("dismissed_at")) or deleted_at
    if dismissal_key is None or dismissed_at is None:
        return None
    return {
        "dismissal_key": dismissal_key,
        "dismissed_at": dismissed_at,
        "deleted_at": deleted_at,
        "active": deleted_at is None,
        "reason": _as_str(raw.get("reason")) or "",
        "source": _as_str(raw.get("source")),
        "project": _as_str(raw.get("project")),
        "intent": _as_str(raw.get("intent")),
        "title": _as_str(raw.get("title")),
        "body": _as_str(raw.get("body")),
        "evidence_event_id": _as_str(raw.get("evidence_event_id")),
    }


def list_action_proposal_dismissals(
    *,
    limit: int = 25,
    dismissals_path: Path | None = None,
    include_inactive: bool = False,
) -> list[ActionProposalDismissalReport]:
    """Return latest proposal dismissals first."""
    path = dismissals_path or ACTION_PROPOSAL_DISMISSALS
    records: list[ActionProposalDismissalReport] = []
    seen: set[str] = set()
    for raw in reversed(_jsonl_dicts(path)):
        report = _dismissal_report(raw)
        if report is None or report["dismissal_key"] in seen:
            continue
        seen.add(report["dismissal_key"])
        if not include_inactive and not report["active"]:
            continue
        records.append(report)
        if len(records) >= max(limit, 1):
            break
    return records


def run_action_proposal_dismissal_list(
    *,
    limit: int = 25,
    dismissal_key: str | None = None,
    include_inactive: bool = False,
    dismissals_path: Path | None = None,
) -> ActionProposalDismissalListReport:
    """List or inspect local proposal dismissals without applying work."""
    path = dismissals_path or ACTION_PROPOSAL_DISMISSALS
    dismissals = list_action_proposal_dismissals(
        limit=10_000 if dismissal_key else max(limit, 1),
        dismissals_path=dismissals_path,
        include_inactive=include_inactive,
    )
    if dismissal_key:
        dismissals = [item for item in dismissals if item["dismissal_key"] == dismissal_key]
    return {
        "status": "ok",
        "path": str(path),
        "dismissal_count": len(dismissals),
        "dismissals": dismissals[: max(limit, 1)],
        "applied": False,
    }


def active_action_proposal_dismissals(
    dismissals_path: Path | None = None,
) -> dict[str, ActionProposalDismissalReport]:
    return {
        dismissal["dismissal_key"]: dismissal
        for dismissal in list_action_proposal_dismissals(
            limit=10_000, dismissals_path=dismissals_path
        )
    }


def dismiss_action_proposal(
    *,
    dismissal_key: str,
    reason: str,
    source: str | None = None,
    project: str | None = None,
    intent: str | None = None,
    title: str | None = None,
    body: str | None = None,
    evidence_event_id: str | None = None,
    dismissals_path: Path | None = None,
) -> ActionProposalDismissReport:
    """Persist a local operator dismissal for a repeated action proposal."""
    key = dismissal_key.strip()
    if not key:
        return {
            "status": "degraded",
            "path": str(dismissals_path or ACTION_PROPOSAL_DISMISSALS),
            "dismissal": None,
            "applied": False,
            "error": "dismissal_key is required",
        }
    path = dismissals_path or ACTION_PROPOSAL_DISMISSALS
    dismissed_at = datetime.now(UTC).isoformat()
    dismissal: ActionProposalDismissalReport = {
        "dismissal_key": key,
        "dismissed_at": dismissed_at,
        "deleted_at": None,
        "active": True,
        "reason": reason.strip() or "dismissed as known repeated noise",
        "source": source,
        "project": project,
        "intent": intent,
        "title": title,
        "body": body,
        "evidence_event_id": evidence_event_id,
    }
    try:
        _jsonl_append(path, dict(dismissal))
    except OSError:
        return {
            "status": "degraded",
            "path": str(path),
            "dismissal": None,
            "applied": False,
            "error": _GENERIC_OPERATION_ERROR,
        }
    return {
        "status": "ok",
        "path": str(path),
        "dismissal": dismissal,
        "applied": False,
        "error": None,
    }


def undismiss_action_proposal(
    *,
    dismissal_key: str,
    reason: str,
    dismissals_path: Path | None = None,
) -> ActionProposalUndismissReport:
    """Add an undismiss tombstone without deleting dismissal history."""
    key = dismissal_key.strip()
    path = dismissals_path or ACTION_PROPOSAL_DISMISSALS
    if not key:
        return {
            "status": "degraded",
            "path": str(path),
            "dismissal_key": dismissal_key,
            "removed": False,
            "applied": False,
            "error": "dismissal_key is required",
        }
    active = active_action_proposal_dismissals(dismissals_path).get(key)
    if active is None:
        return {
            "status": "degraded",
            "path": str(path),
            "dismissal_key": key,
            "removed": False,
            "applied": False,
            "error": "dismissal not found",
        }
    deleted_at = datetime.now(UTC).isoformat()
    tombstone: dict[str, object] = {
        "dismissal_key": key,
        "dismissed_at": active["dismissed_at"],
        "deleted_at": deleted_at,
        "reason": reason.strip() or "undismissed by operator",
        "source": active["source"],
        "project": active["project"],
        "intent": active["intent"],
        "title": active["title"],
        "body": active["body"],
        "evidence_event_id": active["evidence_event_id"],
    }
    try:
        _jsonl_append(path, tombstone)
    except OSError:
        return {
            "status": "degraded",
            "path": str(path),
            "dismissal_key": key,
            "removed": False,
            "applied": False,
            "error": _GENERIC_OPERATION_ERROR,
        }
    return {
        "status": "ok",
        "path": str(path),
        "dismissal_key": key,
        "removed": True,
        "applied": False,
        "error": None,
    }


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
