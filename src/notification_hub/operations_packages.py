"""Saved action package payload and validation helpers."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from notification_hub.config import EVENTS_DIR
from notification_hub.operations_types import (
    ActionExportRetentionReport,
    ActionPackageValidationReport,
    ActionReviewPackageDeleteReport,
    ActionReviewPackageReport,
)

_GENERIC_OPERATION_ERROR = "operation failed; inspect local logs for details"

ACTION_EXPORT_DIR = EVENTS_DIR / "action-exports"
ACTION_EXPORT_SCHEMA_VERSION = "notification-hub.personal_ops_action_export.v1"


def write_action_review_package(
    report: dict[str, object],
    *,
    output_dir: Path | None = None,
) -> dict[str, object]:
    target_dir = output_dir or ACTION_EXPORT_DIR
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    target_path = target_dir / f"personal-ops-actions-{timestamp}.json"
    try:
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        target_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.chmod(target_path, 0o600)
    except OSError:
        return {
            "requested": True,
            "status": "degraded",
            "path": str(target_path),
            "error": _GENERIC_OPERATION_ERROR,
        }
    return {
        "requested": True,
        "status": "ok",
        "path": str(target_path),
        "error": None,
    }


def list_action_review_packages(
    *,
    review_dir: Path | None = None,
    limit: int = 10,
) -> list[ActionReviewPackageReport]:
    """List recent saved action review packages without importing or applying them."""
    target_dir = review_dir or ACTION_EXPORT_DIR
    try:
        candidates = sorted(
            target_dir.glob("personal-ops-actions-*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return []

    reports: list[ActionReviewPackageReport] = []
    for path in candidates[: max(limit, 1)]:
        try:
            stat = path.stat()
        except OSError:
            continue
        validation = validate_action_package(path)
        reports.append(
            {
                "path": str(path),
                "name": path.name,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                "size_bytes": stat.st_size,
                "validation_status": validation["status"],
                "action_count": validation["action_count"],
                "valid_action_count": validation["valid_action_count"],
                "error_count": validation["error_count"],
            }
        )
    return reports


def prune_action_export_files(
    *,
    keep: int = 20,
    dry_run: bool = True,
    export_dir: Path | None = None,
) -> ActionExportRetentionReport:
    """Prune older saved action-export files, keeping the newest N."""
    target_dir = export_dir or ACTION_EXPORT_DIR
    safe_keep = max(keep, 1)
    try:
        all_files = sorted(
            target_dir.glob("personal-ops-actions-*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        all_files = []

    to_delete = all_files[safe_keep:]
    deleted_files: list[str] = []
    error: str | None = None
    status = "ok"

    if not dry_run:
        for path in to_delete:
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError:
                status = "degraded"
                error = _GENERIC_OPERATION_ERROR
                break
            deleted_files.append(path.name)

    if dry_run:
        next_action = (
            "Run again with --apply to delete older action-export files."
            if to_delete
            else "No action-export files need pruning."
        )
    elif status == "ok":
        next_action = (
            "Older action-export files were pruned."
            if deleted_files
            else "No action-export files needed pruning."
        )
    else:
        next_action = "Fix the file deletion error, then rerun retention."

    return {
        "status": status,
        "export_dir": str(target_dir),
        "keep": safe_keep,
        "dry_run": dry_run,
        "total_count": len(all_files),
        "kept_count": min(len(all_files), safe_keep),
        "candidate_count": len(to_delete),
        "deleted_count": len(deleted_files),
        "candidate_files": [p.name for p in to_delete],
        "deleted_files": deleted_files,
        "next_action": next_action,
        "applied": not dry_run,
        "error": error,
    }


def empty_package_validation(path: Path, error: str) -> ActionPackageValidationReport:
    return {
        "status": "degraded",
        "path": str(path),
        "schema_version": None,
        "action_count": 0,
        "valid_action_count": 0,
        "warning_count": 0,
        "error_count": 1,
        "warnings": [],
        "errors": [error],
    }


def _is_safe_action_review_package_name(name: str) -> bool:
    return (
        Path(name).name == name
        and re.fullmatch(r"personal-ops-actions-\d{8}-\d{6}(?:-\d{6})?\.json", name) is not None
    )


def action_review_package_path_for_name(
    *,
    name: str,
    review_dir: Path | None = None,
) -> Path | None:
    """Build a review package path from a validated package filename."""
    if not _is_safe_action_review_package_name(name):
        return None
    return (review_dir or ACTION_EXPORT_DIR) / name


def delete_action_review_package(
    *,
    name: str,
    review_dir: Path | None = None,
) -> ActionReviewPackageDeleteReport:
    """Delete one saved review package without importing or applying it."""
    target_dir = review_dir or ACTION_EXPORT_DIR
    target_path = action_review_package_path_for_name(name=name, review_dir=target_dir)
    if target_path is None:
        display_path = target_dir / name
        return {
            "status": "degraded",
            "path": str(display_path),
            "name": name,
            "deleted": False,
            "applied": False,
            "error": "invalid review package name",
        }
    try:
        target_path.unlink()
    except FileNotFoundError:
        return {
            "status": "degraded",
            "path": str(target_path),
            "name": name,
            "deleted": False,
            "applied": False,
            "error": "review package not found",
        }
    except OSError:
        return {
            "status": "degraded",
            "path": str(target_path),
            "name": name,
            "deleted": False,
            "applied": False,
            "error": _GENERIC_OPERATION_ERROR,
        }
    return {
        "status": "ok",
        "path": str(target_path),
        "name": name,
        "deleted": True,
        "applied": False,
        "error": None,
    }


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
