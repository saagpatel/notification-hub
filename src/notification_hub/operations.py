"""Operator actions beyond diagnostics: smoke checks and log retention."""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypedDict, cast

import httpx

from notification_hub.channels import ensure_log_dir, read_jsonl
from notification_hub.config import (
    DAEMON_STDERR_LOG,
    DAEMON_STDOUT_LOG,
    EVENTS_DIR,
    EVENTS_LOG,
    EXAMPLE_POLICY_CONFIG,
    HOST,
    POLICY_CONFIG,
    PORT,
    analyze_policy_config,
    get_policy_config,
)
from notification_hub.diagnostics import collect_doctor_report
from notification_hub.models import Event, StoredEvent


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


class RecentEventReport(TypedDict):
    event_id: str
    timestamp: str
    source: str
    level: str
    classified_level: str | None
    project: str | None
    title: str
    body: str


class LogsReport(TypedDict):
    status: str
    events_log: str
    stdout_log: str
    stderr_log: str
    recent_events: list[RecentEventReport]
    daemon_summary: DaemonLogSummary
    stdout_tail: list[str]
    stderr_tail: list[str]
    missing_paths: list[str]
    error: str | None


class DaemonLogSummary(TypedDict):
    access_status_counts: dict[str, int]
    accepted_event_posts: int
    rejected_event_posts: int
    validation_error_count: int
    recent_validation_errors: list[str]


class RepeatedSignatureReport(TypedDict):
    count: int
    source: str
    project: str | None
    level: str
    title: str
    body: str


class BurnInReport(TypedDict):
    status: str
    minutes: int
    events_seen: int
    accepted_event_posts: int
    rejected_event_posts: int
    validation_error_count: int
    repeated_signatures: list[RepeatedSignatureReport]
    daemon_summary: DaemonLogSummary
    error: str | None


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


class VerifyRuntimeReport(TypedDict):
    status: str
    read_only: bool
    include_smoke: bool
    health_url: str | None
    checks: dict[str, bool]
    runtime_wiring: dict[str, bool]
    doctor: dict[str, object]
    policy_check: PolicyCheckReport
    smoke: SmokeReport | None


class StatusReport(TypedDict):
    status: str
    health_url: str | None
    daemon_reachable: bool
    watcher_active: bool | None
    events_processed: int | None
    uptime_seconds: float | None
    policy_config_found: bool | None
    policy_warning_count: int
    retention_enabled: bool | None
    retention_last_status: str | None
    runtime_wiring_current: bool
    push_notifier_available: bool | None
    slack_configured: bool | None
    next_action: str


def _as_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    return {}


def _as_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _as_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    return None


def _as_float(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _as_str(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _tail_text_file(path: Path, *, lines: int) -> list[str]:
    if lines <= 0 or not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return [line.rstrip("\n") for line in handle.readlines()[-lines:]]


_EVENT_ACCESS_RE = re.compile(r'"POST /events HTTP/1\.1" (?P<status>\d{3})')
_DAEMON_START_MARKERS = (
    "INFO:     Started server process",
    "INFO:     Uvicorn running on ",
)


def _lines_since_latest_daemon_start(lines: list[str]) -> list[str]:
    """Return log lines scoped to the latest visible daemon start marker."""
    latest_start_index: int | None = None
    for index, line in enumerate(lines):
        if any(line.startswith(marker) for marker in _DAEMON_START_MARKERS):
            latest_start_index = index
    if latest_start_index is None:
        return lines
    return lines[latest_start_index + 1 :]


def _summarize_daemon_logs(stdout_tail: list[str], stderr_tail: list[str]) -> DaemonLogSummary:
    status_counts: dict[str, int] = {}
    for line in stdout_tail:
        match = _EVENT_ACCESS_RE.search(line)
        if match is None:
            continue
        status = match.group("status")
        status_counts[status] = status_counts.get(status, 0) + 1

    current_stderr_tail = _lines_since_latest_daemon_start(stderr_tail)
    validation_errors = [line for line in current_stderr_tail if line.startswith("Rejected event payload")]
    return {
        "access_status_counts": status_counts,
        "accepted_event_posts": sum(
            count for status, count in status_counts.items() if status.startswith("2")
        ),
        "rejected_event_posts": status_counts.get("422", 0),
        "validation_error_count": len(validation_errors),
        "recent_validation_errors": validation_errors[-5:],
    }


def _event_report(event: StoredEvent) -> RecentEventReport:
    return {
        "event_id": event.event_id,
        "timestamp": event.timestamp.isoformat(),
        "source": event.source,
        "level": event.level,
        "classified_level": event.classified_level,
        "project": event.project,
        "title": event.title,
        "body": event.body,
    }


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


def run_logs(*, events: int = 5, lines: int = 20) -> LogsReport:
    """Return recent event and daemon log entries without mutating local runtime state."""
    missing_paths = [
        str(path)
        for path in (EVENTS_LOG, DAEMON_STDOUT_LOG, DAEMON_STDERR_LOG)
        if not path.exists()
    ]

    try:
        stored_events = read_jsonl(path=EVENTS_LOG)
        event_limit = max(events, 0)
        recent_stored_events = stored_events[-event_limit:] if event_limit else []
        recent_events = [_event_report(event) for event in recent_stored_events]
        stdout_tail = _tail_text_file(DAEMON_STDOUT_LOG, lines=lines)
        stderr_tail = _tail_text_file(DAEMON_STDERR_LOG, lines=lines)
    except (OSError, ValueError) as exc:
        return {
            "status": "degraded",
            "events_log": str(EVENTS_LOG),
            "stdout_log": str(DAEMON_STDOUT_LOG),
            "stderr_log": str(DAEMON_STDERR_LOG),
            "recent_events": [],
            "daemon_summary": _summarize_daemon_logs([], []),
            "stdout_tail": [],
            "stderr_tail": [],
            "missing_paths": missing_paths,
            "error": str(exc),
        }

    return {
        "status": "ok",
        "events_log": str(EVENTS_LOG),
        "stdout_log": str(DAEMON_STDOUT_LOG),
        "stderr_log": str(DAEMON_STDERR_LOG),
        "recent_events": recent_events,
        "daemon_summary": _summarize_daemon_logs(stdout_tail, stderr_tail),
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "missing_paths": missing_paths,
        "error": None,
    }


def run_burn_in(*, minutes: int = 10, lines: int = 200) -> BurnInReport:
    """Summarize recent accepted/rejected events and repeated signatures."""
    window_minutes = max(minutes, 1)
    tail_lines = max(lines, 0)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    try:
        stored_events = read_jsonl(path=EVENTS_LOG)
        recent_events = [
            event
            for event in stored_events
            if event.timestamp.astimezone(timezone.utc) >= cutoff
        ]
        signatures: dict[tuple[str, str | None, str, str, str], int] = {}
        for event in recent_events:
            effective_level = event.classified_level or event.level
            key = (event.source, event.project, effective_level, event.title, event.body)
            signatures[key] = signatures.get(key, 0) + 1

        repeated: list[RepeatedSignatureReport] = [
            {
                "count": count,
                "source": source,
                "project": project,
                "level": level,
                "title": title,
                "body": body,
            }
            for (source, project, level, title, body), count in signatures.items()
            if count > 1
        ]
        repeated.sort(key=lambda item: item["count"], reverse=True)
        stdout_tail = _tail_text_file(DAEMON_STDOUT_LOG, lines=tail_lines)
        stderr_tail = _tail_text_file(DAEMON_STDERR_LOG, lines=tail_lines)
        daemon_summary = _summarize_daemon_logs(stdout_tail, stderr_tail)
    except (OSError, ValueError) as exc:
        return {
            "status": "degraded",
            "minutes": window_minutes,
            "events_seen": 0,
            "accepted_event_posts": 0,
            "rejected_event_posts": 0,
            "validation_error_count": 0,
            "repeated_signatures": [],
            "daemon_summary": _summarize_daemon_logs([], []),
            "error": str(exc),
        }

    return {
        "status": "ok",
        "minutes": window_minutes,
        "events_seen": len(recent_events),
        "accepted_event_posts": daemon_summary["accepted_event_posts"],
        "rejected_event_posts": daemon_summary["rejected_event_posts"],
        "validation_error_count": daemon_summary["validation_error_count"],
        "repeated_signatures": repeated[:10],
        "daemon_summary": daemon_summary,
        "error": None,
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


def run_verify_runtime(*, include_smoke: bool = False) -> VerifyRuntimeReport:
    """Aggregate the core runtime checks into one operator-facing report."""
    doctor = collect_doctor_report()
    policy_check = run_policy_check()
    smoke = run_smoke_check() if include_smoke else None

    doctor_checks = _as_dict(doctor.get("checks"))
    local_api = _as_dict(doctor.get("local_api"))
    runtime_wiring = _as_dict(doctor.get("runtime_wiring"))

    checks = {
        "doctor_ok": doctor.get("status") == "ok",
        "policy_check_ok": policy_check["status"] != "degraded",
        "health_details_reachable": local_api.get("reachable") is True,
        "runtime_wiring_current": doctor_checks.get("runtime_wiring_current") is True,
        "smoke_ok": smoke is None or smoke["status"] == "ok",
    }
    status = "ok" if all(checks.values()) else "degraded"
    health_url = local_api.get("url")

    return {
        "status": status,
        "read_only": smoke is None,
        "include_smoke": include_smoke,
        "health_url": health_url if isinstance(health_url, str) else None,
        "checks": checks,
        "runtime_wiring": {key: bool(value) for key, value in runtime_wiring.items()},
        "doctor": doctor,
        "policy_check": policy_check,
        "smoke": smoke,
    }


def run_status() -> StatusReport:
    """Return a compact read-only operator summary for the live daemon."""
    verification = run_verify_runtime()
    checks = verification["checks"]
    doctor = _as_dict(verification["doctor"])
    doctor_checks = _as_dict(doctor.get("checks"))
    local_api = _as_dict(doctor.get("local_api"))
    payload = _as_dict(local_api.get("payload"))
    config = _as_dict(doctor.get("config"))
    delivery = _as_dict(doctor.get("delivery"))
    retention = _as_dict(payload.get("retention"))

    daemon_reachable = checks["health_details_reachable"]
    runtime_wiring_current = checks["runtime_wiring_current"]
    policy_load_ok = doctor_checks.get("policy_load_ok") is True

    if verification["status"] == "ok":
        next_action = "No action needed."
    elif not daemon_reachable:
        next_action = "Start or restart the LaunchAgent, then run verify-runtime again."
    elif not runtime_wiring_current:
        next_action = "Refresh runtime templates from ops/, then run verify-runtime again."
    elif not policy_load_ok or not checks["policy_check_ok"]:
        next_action = "Run notification-hub policy-check and fix the reported policy issue."
    else:
        next_action = "Run notification-hub verify-runtime --json for the detailed failing check."

    return {
        "status": verification["status"],
        "health_url": verification["health_url"],
        "daemon_reachable": daemon_reachable,
        "watcher_active": _as_bool(payload.get("watcher_active")),
        "events_processed": _as_int(payload.get("events_processed")),
        "uptime_seconds": _as_float(payload.get("uptime_seconds")),
        "policy_config_found": _as_bool(config.get("exists")),
        "policy_warning_count": verification["policy_check"]["warning_count"],
        "retention_enabled": _as_bool(retention.get("enabled")),
        "retention_last_status": _as_str(retention.get("last_status")),
        "runtime_wiring_current": runtime_wiring_current,
        "push_notifier_available": _as_bool(delivery.get("push_notifier_available")),
        "slack_configured": _as_bool(delivery.get("slack_webhook_configured")),
        "next_action": next_action,
    }
