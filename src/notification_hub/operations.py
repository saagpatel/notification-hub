"""Operator actions beyond diagnostics: smoke checks and log retention."""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from typing import TypedDict

import httpx

from notification_hub.channels import ensure_log_dir, read_jsonl
from notification_hub.config import (
    EVENTS_DIR,
    EVENTS_LOG,
    EXAMPLE_POLICY_CONFIG,
    HOST,
    POLICY_CONFIG,
    PORT,
    analyze_policy_config,
    get_policy_config,
)
from notification_hub.models import Event


class SmokeReport(TypedDict):
    status: str
    health_url: str
    event_url: str
    event_id: str | None
    log_verified: bool
    response_status: int | None
    error: str | None


class RetentionReport(TypedDict):
    status: str
    rotated: bool
    archive_path: str | None
    events_before: int
    events_after: int
    archived_events: int
    deleted_archives: list[str]


class BootstrapConfigReport(TypedDict):
    status: str
    copied: bool
    config_path: str
    example_path: str
    error: str | None


class PolicyCheckReport(TypedDict):
    status: str
    config_path: str
    config_found: bool
    example_path: str
    load_error: str | None
    warning_count: int
    suggestion_count: int
    warnings: list[str]
    suggestions: list[str]


def _suggest_fix_for_warning(warning: str) -> str:
    """Turn a policy warning into a concrete next action."""
    if warning.startswith("automatic retention is disabled"):
        return (
            "Re-enable retention in the policy config unless you intentionally want log pruning to "
            "stay fully manual."
        )
    if "appears in both" in warning:
        return (
            "Keep the keyword in one classifier group only, or move it into the higher-priority "
            "group if that overlap is intentional."
        )
    if "sets both project and project_prefix" in warning:
        return (
            "Drop `project_prefix` when you only mean one project, or remove `project` if you "
            "really want the broader prefix match."
        )
    if "does not change level or delivery behavior" in warning:
        return (
            "Either add `force_level`, `disable_push`, or `disable_slack`, or delete the rule if "
            "it is only restating the default behavior."
        )
    if "is shadowed by earlier" in warning:
        return (
            "Move the narrower rule earlier, or tighten the earlier rule so the later one still "
            "has a chance to match."
        )
    if "share priority" in warning:
        return (
            "Give the more important rule a higher `priority`, or keep them on the same priority "
            "only if file order deciding between them is intentional."
        )
    if "sets continue_matching but there is no later rule to continue into" in warning:
        return (
            "Remove `continue_matching` on the final rule, or add a later rule if you meant to "
            "compose another routing step."
        )
    if "does not add behavior beyond earlier continue-matching rule(s)" in warning:
        return (
            "Delete the redundant rule, or tighten its matcher or delivery overrides so it changes "
            "something the earlier continue-matching chain does not already cover."
        )
    return "Review the warning and simplify the policy rule or classifier entry so its intent is explicit."


def run_smoke_check() -> SmokeReport:
    """Post a harmless info event and verify it lands in the live event log."""
    base_url = f"http://{HOST}:{PORT}"
    event_url = f"{base_url}/events"
    health_url = f"{base_url}/health/details"
    payload = Event(
        source="codex",
        level="info",
        title="Notification Hub smoke check",
        body=f"Smoke check at {datetime.now(timezone.utc).isoformat()}",
        project="notification-hub",
    )

    try:
        response = httpx.post(event_url, json=payload.model_dump(mode="json"), timeout=5.0)
        if response.status_code != 201:
            return {
                "status": "degraded",
                "health_url": health_url,
                "event_url": event_url,
                "event_id": None,
                "log_verified": False,
                "response_status": response.status_code,
                "error": f"unexpected status {response.status_code}",
            }

        event_id = response.json().get("event_id")
        log_verified = False
        if isinstance(event_id, str):
            log_verified = any(event.event_id == event_id for event in read_jsonl())

        return {
            "status": "ok" if log_verified else "degraded",
            "health_url": health_url,
            "event_url": event_url,
            "event_id": event_id if isinstance(event_id, str) else None,
            "log_verified": log_verified,
            "response_status": response.status_code,
            "error": None if log_verified else "event not found in log",
        }
    except httpx.HTTPError as exc:
        return {
            "status": "degraded",
            "health_url": health_url,
            "event_url": event_url,
            "event_id": None,
            "log_verified": False,
            "response_status": None,
            "error": str(exc),
        }


def run_retention(*, max_events: int, keep_archives: int) -> RetentionReport:
    """Archive older events when the live JSONL log exceeds the configured size."""
    ensure_log_dir()
    if not EVENTS_LOG.exists():
        return {
            "status": "ok",
            "rotated": False,
            "archive_path": None,
            "events_before": 0,
            "events_after": 0,
            "archived_events": 0,
            "deleted_archives": [],
        }

    with EVENTS_LOG.open("r", encoding="utf-8") as handle:
        lines = handle.readlines()

    events_before = len(lines)
    if events_before <= max_events:
        return {
            "status": "ok",
            "rotated": False,
            "archive_path": None,
            "events_before": events_before,
            "events_after": events_before,
            "archived_events": 0,
            "deleted_archives": [],
        }

    archive_dir = EVENTS_DIR / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    archive_path = archive_dir / f"events-{datetime.now(timezone.utc):%Y%m%d-%H%M%S-%f}.jsonl"

    archived_lines = lines[:-max_events]
    remaining_lines = lines[-max_events:]

    archive_fd = os.open(str(archive_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(archive_fd, "".join(archived_lines).encode("utf-8"))
    finally:
        os.close(archive_fd)

    log_fd = os.open(str(EVENTS_LOG), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(log_fd, "".join(remaining_lines).encode("utf-8"))
    finally:
        os.close(log_fd)

    deleted_archives: list[str] = []
    archive_paths = sorted(archive_dir.glob("events-*.jsonl"))
    if len(archive_paths) > keep_archives:
        for extra in archive_paths[: len(archive_paths) - keep_archives]:
            extra.unlink(missing_ok=True)
            deleted_archives.append(str(extra))

    return {
        "status": "ok",
        "rotated": True,
        "archive_path": str(archive_path),
        "events_before": events_before,
        "events_after": len(remaining_lines),
        "archived_events": len(archived_lines),
        "deleted_archives": deleted_archives,
    }


def bootstrap_policy_config(*, force: bool = False) -> BootstrapConfigReport:
    """Copy the repo sample policy file into the live config location."""
    example_path = EXAMPLE_POLICY_CONFIG
    config_path = POLICY_CONFIG

    if not example_path.exists():
        return {
            "status": "degraded",
            "copied": False,
            "config_path": str(config_path),
            "example_path": str(example_path),
            "error": "sample policy config not found",
        }

    if config_path.exists() and not force:
        return {
            "status": "ok",
            "copied": False,
            "config_path": str(config_path),
            "example_path": str(example_path),
            "error": None,
        }

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(example_path, config_path)
        os.chmod(config_path, 0o600)
    except OSError as exc:
        return {
            "status": "degraded",
            "copied": False,
            "config_path": str(config_path),
            "example_path": str(example_path),
            "error": str(exc),
        }

    return {
        "status": "ok",
        "copied": True,
        "config_path": str(config_path),
        "example_path": str(example_path),
        "error": None,
    }


def run_policy_check() -> PolicyCheckReport:
    """Analyze the current policy config for overlapping or ineffective rules."""
    policy = get_policy_config()
    warnings = list(analyze_policy_config(policy))
    suggestions = [_suggest_fix_for_warning(warning) for warning in warnings]
    load_error = policy.load_error

    if load_error is not None or not EXAMPLE_POLICY_CONFIG.exists():
        status = "degraded"
    elif warnings:
        status = "warn"
    else:
        status = "ok"

    return {
        "status": status,
        "config_path": str(policy.path),
        "config_found": policy.config_found,
        "example_path": str(EXAMPLE_POLICY_CONFIG),
        "load_error": load_error,
        "warning_count": len(warnings),
        "suggestion_count": len(suggestions),
        "warnings": warnings,
        "suggestions": suggestions,
    }
