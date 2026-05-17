"""Saved action package payload and validation helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from notification_hub.operations_types import ActionPackageValidationReport

_GENERIC_OPERATION_ERROR = "operation failed; inspect local logs for details"

ACTION_EXPORT_SCHEMA_VERSION = "notification-hub.personal_ops_action_export.v1"


def load_action_package_payload(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return cast(dict[str, object], payload)


def action_dicts_from_payload(payload: dict[str, object]) -> list[dict[str, object]]:
    actions_value = payload.get("actions")
    actions: list[dict[str, object]] = []
    if isinstance(actions_value, list):
        for action_value in cast(list[object], actions_value):
            if isinstance(action_value, dict):
                actions.append(cast(dict[str, object], action_value))
    return actions


def _require_str(value: object, field: str, errors: list[str]) -> str | None:
    if isinstance(value, str) and value:
        return value
    errors.append(f"missing or invalid string field: {field}")
    return None


def _validate_action_record(action: object, *, index: int) -> list[str]:
    errors: list[str] = []
    if not isinstance(action, dict):
        return [f"action {index} is not an object"]
    record = cast(dict[str, object], action)
    for field in (
        "action_id",
        "source",
        "intent",
        "priority",
        "state",
        "title",
        "summary",
        "suggested_next_action",
        "evidence_event_id",
        "evidence_timestamp",
    ):
        _require_str(record.get(field), f"actions[{index}].{field}", errors)
    priority = record.get("priority")
    if isinstance(priority, str) and priority not in {"high", "medium", "low"}:
        errors.append(f"actions[{index}].priority must be high, medium, or low")
    state = record.get("state")
    if isinstance(state, str) and state not in {"open", "waiting", "ready", "done"}:
        errors.append(f"actions[{index}].state must be open, waiting, ready, or done")
    count = record.get("count")
    if not isinstance(count, int) or count < 1:
        errors.append(f"actions[{index}].count must be a positive integer")
    context = record.get("evidence_context")
    if context is not None:
        if not isinstance(context, dict):
            errors.append(f"actions[{index}].evidence_context must be an object when present")
        else:
            for key, value in cast(dict[object, object], context).items():
                if not isinstance(key, str):
                    errors.append(f"actions[{index}].evidence_context keys must be strings")
                    break
                if not isinstance(value, (str, int, float, bool)) and value is not None:
                    errors.append(f"actions[{index}].evidence_context.{key} must be a scalar value")
                    break
    return errors


def validate_action_package(path: Path) -> ActionPackageValidationReport:
    """Validate a saved personal-ops action package without importing it."""
    errors: list[str] = []
    warnings: list[str] = []
    schema_version: str | None = None
    action_count = 0
    valid_action_count = 0

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "status": "degraded",
            "path": str(path),
            "schema_version": None,
            "action_count": 0,
            "valid_action_count": 0,
            "warning_count": 0,
            "error_count": 1,
            "warnings": [],
            "errors": [_GENERIC_OPERATION_ERROR],
        }

    if not isinstance(payload, dict):
        errors.append("package root must be an object")
        actions: list[object] = []
    else:
        package = cast(dict[str, object], payload)
        raw_schema = package.get("schema_version")
        schema_version = raw_schema if isinstance(raw_schema, str) else None
        if schema_version != ACTION_EXPORT_SCHEMA_VERSION:
            errors.append(f"schema_version must be {ACTION_EXPORT_SCHEMA_VERSION}")
        actions_value = package.get("actions")
        actions = cast(list[object], actions_value) if isinstance(actions_value, list) else []
        if not isinstance(actions_value, list):
            errors.append("actions must be a list")
        if not actions:
            warnings.append("package contains no action proposals")

    seen_action_ids: set[str] = set()
    action_count = len(actions)
    for index, action in enumerate(actions):
        action_errors = _validate_action_record(action, index=index)
        if isinstance(action, dict):
            action_record = cast(dict[str, object], action)
            action_id = action_record.get("action_id")
            if isinstance(action_id, str):
                if action_id in seen_action_ids:
                    action_errors.append(f"duplicate action_id: {action_id}")
                seen_action_ids.add(action_id)
        if action_errors:
            errors.extend(action_errors)
        else:
            valid_action_count += 1

    status = "degraded" if errors else "ok"
    return {
        "status": status,
        "path": str(path),
        "schema_version": schema_version,
        "action_count": action_count,
        "valid_action_count": valid_action_count,
        "warning_count": len(warnings),
        "error_count": len(errors),
        "warnings": warnings,
        "errors": errors,
    }
