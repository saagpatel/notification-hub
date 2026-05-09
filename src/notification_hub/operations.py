"""Operator actions beyond diagnostics: smoke checks and log retention."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypedDict, cast

import httpx

from notification_hub.channels import ensure_log_dir, read_jsonl, send_push, send_slack
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
from notification_hub.coordination import infer_intent
from notification_hub.diagnostics import collect_doctor_report
from notification_hub.models import Event, Intent, StoredEvent


class SmokeReport(TypedDict):
    status: str
    health_url: str
    event_url: str
    event_id: str | None
    log_verified: bool
    response_status: int | None
    error: str | None


class DeliveryCheckReport(TypedDict):
    status: str
    verify_slack: bool
    verify_push: bool
    slack_ok: bool | None
    push_ok: bool | None
    event_id: str | None
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
    intent: str


class InboxItemReport(TypedDict):
    event_id: str
    timestamp: str
    source: str
    project: str | None
    level: str
    intent: str
    title: str
    body: str


class InboxRollupReport(TypedDict):
    count: int
    source: str
    project: str | None
    intent: str
    level: str
    title: str
    body: str
    latest_timestamp: str
    latest_event_id: str


class InboxReport(TypedDict):
    status: str
    hours: int
    events_seen: int
    needs_attention: list[InboxItemReport]
    waiting_or_blocked: list[InboxItemReport]
    ready: list[InboxItemReport]
    completed: list[InboxItemReport]
    rollups: list[InboxRollupReport]
    noise_candidates: list[RepeatedSignatureReport]
    error: str | None


class PersonalOpsActionReport(TypedDict):
    action_id: str
    source: str
    project: str | None
    intent: str
    priority: str
    state: str
    title: str
    summary: str
    suggested_next_action: str
    evidence_event_id: str
    evidence_timestamp: str
    count: int


class PersonalOpsActionExportReport(TypedDict):
    status: str
    schema_version: str
    generated_at: str
    hours: int
    actions: list[PersonalOpsActionReport]
    review_package: dict[str, object]
    inbox: InboxReport
    error: str | None


class ActionPackageValidationReport(TypedDict):
    status: str
    path: str
    schema_version: str | None
    action_count: int
    valid_action_count: int
    warning_count: int
    error_count: int
    warnings: list[str]
    errors: list[str]


class ActionReviewPackageReport(TypedDict):
    path: str
    name: str
    modified_at: str
    size_bytes: int
    validation_status: str
    action_count: int
    valid_action_count: int
    error_count: int


class PersonalOpsImportReport(TypedDict):
    status: str
    path: str
    dry_run: bool
    applied: bool
    validation: ActionPackageValidationReport
    next_action: str
    error: str | None


class BridgeSaveReport(TypedDict):
    attempted: bool
    status: str
    db_path: str | None
    snapshot_id: int | None
    snapshot_date: str | None
    error: str | None


class CoordinationSnapshotReport(TypedDict):
    status: str
    schema_version: str
    generated_at: str
    bridge_target_system: str
    bridge_snapshot_date: str
    bridge_snapshot: dict[str, object]
    bridge_save: BridgeSaveReport
    inbox: InboxReport
    runtime_status: StatusReport
    follow_up: list[str]
    error: str | None


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
    slack_delivery_failure_count: int
    recent_slack_delivery_failures: list[str]


class RepeatedSignatureReport(TypedDict):
    count: int
    source: str
    project: str | None
    level: str
    title: str
    body: str


class BurnInHealthReport(TypedDict):
    accepted_event_posts: int
    rejected_event_posts: int
    validation_error_count: int
    slack_delivery_failure_count: int
    status: str


class SlackVolumeReport(TypedDict):
    count: int
    source: str
    level: str


class BurnInReport(TypedDict):
    status: str
    minutes: int
    events_seen: int
    accepted_event_posts: int
    rejected_event_posts: int
    validation_error_count: int
    health: BurnInHealthReport
    noise_candidates: list[RepeatedSignatureReport]
    repeated_signatures: list[RepeatedSignatureReport]
    slack_eligible_events: int
    slack_volume: list[SlackVolumeReport]
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
    burn_in: BurnInReport
    delivery_check: DeliveryCheckReport | None
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
    slack_delivery_failures: int
    next_action: str


DEFAULT_BRIDGE_DB_PATH = Path.home() / ".local" / "share" / "bridge-db" / "bridge.db"
BRIDGE_SNAPSHOT_RETENTION_PER_SYSTEM = 10
ACTION_EXPORT_DIR = EVENTS_DIR / "action-exports"
ACTION_EXPORT_SCHEMA_VERSION = "notification-hub.personal_ops_action_export.v1"


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


def _save_bridge_snapshot(
    snapshot: dict[str, object],
    *,
    snapshot_date: str,
    db_path: Path | None = None,
) -> BridgeSaveReport:
    target_path = db_path or Path(os.environ.get("BRIDGE_DB_PATH", str(DEFAULT_BRIDGE_DB_PATH)))
    snapshot_json = json.dumps(snapshot)
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with sqlite3.connect(target_path) as conn:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            cursor = conn.execute(
                """
                INSERT INTO system_snapshots (system, snapshot_date, data)
                VALUES (?, ?, ?)
                """,
                ("codex", snapshot_date, snapshot_json),
            )
            snapshot_id = cursor.lastrowid
            if snapshot_id is not None:
                conn.execute(
                    "DELETE FROM content_index WHERE source_type = ? AND source_id = ?",
                    ("snapshot", str(snapshot_id)),
                )
                conn.execute(
                    "INSERT INTO content_index (source_type, source_id, text) VALUES (?, ?, ?)",
                    ("snapshot", str(snapshot_id), snapshot_json),
                )
            conn.execute(
                """
                DELETE FROM system_snapshots
                WHERE system = ? AND id NOT IN (
                    SELECT id FROM system_snapshots WHERE system = ?
                    ORDER BY created_at DESC LIMIT ?
                )
                """,
                ("codex", "codex", BRIDGE_SNAPSHOT_RETENTION_PER_SYSTEM),
            )
            conn.execute(
                """
                DELETE FROM content_index
                WHERE source_type = 'snapshot'
                AND NOT EXISTS (
                    SELECT 1 FROM system_snapshots
                    WHERE CAST(system_snapshots.id AS TEXT) = content_index.source_id
                )
                """
            )
        return {
            "attempted": True,
            "status": "ok",
            "db_path": str(target_path),
            "snapshot_id": snapshot_id,
            "snapshot_date": snapshot_date,
            "error": None,
        }
    except sqlite3.Error as exc:
        return {
            "attempted": True,
            "status": "degraded",
            "db_path": str(target_path),
            "snapshot_id": None,
            "snapshot_date": snapshot_date,
            "error": str(exc),
        }


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
_SLACK_DELIVERY_FAILURE_PREFIXES = (
    "Slack send failed",
    "Slack digest failed",
    "Slack webhook returned",
    "Slack digest webhook returned",
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
    slack_delivery_failures = [
        line
        for line in current_stderr_tail
        if any(line.startswith(prefix) for prefix in _SLACK_DELIVERY_FAILURE_PREFIXES)
    ]
    return {
        "access_status_counts": status_counts,
        "accepted_event_posts": sum(
            count for status, count in status_counts.items() if status.startswith("2")
        ),
        "rejected_event_posts": status_counts.get("422", 0),
        "validation_error_count": len(validation_errors),
        "recent_validation_errors": validation_errors[-5:],
        "slack_delivery_failure_count": len(slack_delivery_failures),
        "recent_slack_delivery_failures": slack_delivery_failures[-5:],
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
        "intent": infer_intent(event),
    }


def _inbox_item(event: StoredEvent) -> InboxItemReport:
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


def _build_inbox_rollups(events: list[StoredEvent]) -> list[InboxRollupReport]:
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
            }
        )

    return sorted(
        rollups,
        key=lambda item: (item["count"], item["latest_timestamp"]),
        reverse=True,
    )


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


def _action_from_rollup(rollup: InboxRollupReport) -> PersonalOpsActionReport:
    project_part = rollup["project"] or "general"
    normalized_title = re.sub(r"[^a-z0-9]+", "-", rollup["title"].lower()).strip("-") or "signal"
    evidence_part = re.sub(r"[^a-z0-9]+", "-", rollup["latest_event_id"].lower()).strip("-") or "event"
    action_id = (
        f"notification-hub:{rollup['source']}:{project_part}:"
        f"{rollup['intent']}:{normalized_title}:{evidence_part}"
    )
    return {
        "action_id": action_id,
        "source": rollup["source"],
        "project": rollup["project"],
        "intent": rollup["intent"],
        "priority": _action_priority(rollup["intent"], rollup["level"]),
        "state": _action_state(rollup["intent"]),
        "title": rollup["title"],
        "summary": f"{rollup['count']} repeated {rollup['source']} events: {rollup['body']}",
        "suggested_next_action": _suggested_action(rollup["intent"], rollup["title"]),
        "evidence_event_id": rollup["latest_event_id"],
        "evidence_timestamp": rollup["latest_timestamp"],
        "count": rollup["count"],
    }


def _write_action_review_package(
    report: dict[str, object],
    *,
    output_dir: Path | None = None,
) -> dict[str, object]:
    target_dir = output_dir or ACTION_EXPORT_DIR
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target_path = target_dir / f"personal-ops-actions-{timestamp}.json"
    try:
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        target_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(target_path, 0o600)
    except OSError as exc:
        return {
            "requested": True,
            "status": "degraded",
            "path": str(target_path),
            "error": str(exc),
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
                "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                "size_bytes": stat.st_size,
                "validation_status": validation["status"],
                "action_count": validation["action_count"],
                "valid_action_count": validation["valid_action_count"],
                "error_count": validation["error_count"],
            }
        )
    return reports


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
    return errors


def _intent_bucket(intent: Intent) -> str:
    if intent in ("needs_attention", "automation_failed"):
        return "needs_attention"
    if intent in ("blocked", "waiting_on_user"):
        return "waiting_or_blocked"
    if intent in ("ready_to_review", "ready_to_merge", "handoff_created"):
        return "ready"
    if intent == "completed":
        return "completed"
    return "informational"


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
    """Summarize recent health failures and repeated/noisy event signatures."""
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
        slack_counts: dict[tuple[str, str], int] = {}
        for event in recent_events:
            effective_level = event.classified_level or event.level
            if effective_level not in ("urgent", "normal"):
                continue
            key = (event.source, effective_level)
            slack_counts[key] = slack_counts.get(key, 0) + 1
        slack_volume: list[SlackVolumeReport] = [
            {
                "count": count,
                "source": source,
                "level": level,
            }
            for (source, level), count in slack_counts.items()
        ]
        slack_volume.sort(key=lambda item: item["count"], reverse=True)
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
            "health": {
                "accepted_event_posts": 0,
                "rejected_event_posts": 0,
                "validation_error_count": 0,
                "slack_delivery_failure_count": 0,
                "status": "degraded",
            },
            "noise_candidates": [],
            "repeated_signatures": [],
            "slack_eligible_events": 0,
            "slack_volume": [],
            "daemon_summary": _summarize_daemon_logs([], []),
            "error": str(exc),
        }

    health_status = (
        "ok"
        if daemon_summary["rejected_event_posts"] == 0
        and daemon_summary["validation_error_count"] == 0
        and daemon_summary["slack_delivery_failure_count"] == 0
        else "degraded"
    )
    noise_candidates = repeated[:10]
    return {
        "status": "ok",
        "minutes": window_minutes,
        "events_seen": len(recent_events),
        "accepted_event_posts": daemon_summary["accepted_event_posts"],
        "rejected_event_posts": daemon_summary["rejected_event_posts"],
        "validation_error_count": daemon_summary["validation_error_count"],
        "health": {
            "accepted_event_posts": daemon_summary["accepted_event_posts"],
            "rejected_event_posts": daemon_summary["rejected_event_posts"],
            "validation_error_count": daemon_summary["validation_error_count"],
            "slack_delivery_failure_count": daemon_summary["slack_delivery_failure_count"],
            "status": health_status,
        },
        "noise_candidates": noise_candidates,
        "repeated_signatures": noise_candidates,
        "slack_eligible_events": sum(item["count"] for item in slack_volume),
        "slack_volume": slack_volume[:10],
        "daemon_summary": daemon_summary,
        "error": None,
    }


def run_inbox(*, hours: int = 24, limit: int = 10) -> InboxReport:
    """Summarize recent events by coordination intent for operator review."""
    window_hours = max(hours, 1)
    item_limit = max(limit, 1)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    try:
        stored_events = [
            event
            for event in read_jsonl(path=EVENTS_LOG)
            if event.timestamp.astimezone(timezone.utc) >= cutoff
        ]
    except (OSError, ValueError) as exc:
        return {
            "status": "degraded",
            "hours": window_hours,
            "events_seen": 0,
            "needs_attention": [],
            "waiting_or_blocked": [],
            "ready": [],
            "completed": [],
            "rollups": [],
            "noise_candidates": [],
            "error": str(exc),
        }

    buckets: dict[str, list[InboxItemReport]] = {
        "needs_attention": [],
        "waiting_or_blocked": [],
        "ready": [],
        "completed": [],
    }
    for event in sorted(stored_events, key=lambda item: item.timestamp, reverse=True):
        bucket = _intent_bucket(infer_intent(event))
        if bucket in buckets:
            buckets[bucket].append(_inbox_item(event))

    burn_in = run_burn_in(minutes=window_hours * 60, lines=200)
    return {
        "status": "ok",
        "hours": window_hours,
        "events_seen": len(stored_events),
        "needs_attention": buckets["needs_attention"][:item_limit],
        "waiting_or_blocked": buckets["waiting_or_blocked"][:item_limit],
        "ready": buckets["ready"][:item_limit],
        "completed": buckets["completed"][:item_limit],
        "rollups": _build_inbox_rollups(stored_events)[:item_limit],
        "noise_candidates": burn_in["noise_candidates"][:item_limit],
        "error": None,
    }


def run_coordination_snapshot(
    *,
    hours: int = 24,
    limit: int = 10,
    save_bridge_db: bool = False,
    bridge_db_path: Path | None = None,
) -> CoordinationSnapshotReport:
    """Build a bridge-ready coordination snapshot, optionally saving it to bridge-db."""
    generated_at = datetime.now(timezone.utc)
    inbox = run_inbox(hours=hours, limit=limit)
    runtime_status = run_status()
    follow_up: list[str] = []

    if runtime_status["status"] != "ok":
        follow_up.append(runtime_status["next_action"])
    if inbox["noise_candidates"]:
        follow_up.append("Review top inbox noise candidates before adding more delivery surfaces.")
    if inbox["waiting_or_blocked"]:
        follow_up.append("Route waiting or blocked items into the action layer once bridge export is proven.")
    if not follow_up:
        follow_up.append("No immediate operator action needed.")

    bridge_snapshot: dict[str, object] = {
        "active_projects": {
            "notification-hub": {
                "state": runtime_status["status"],
                "summary": "Local notification daemon with coordination inbox export.",
                "next_action": runtime_status["next_action"],
            }
        },
        "coordination": {
            "generated_at": generated_at.isoformat(),
            "hours": inbox["hours"],
            "events_seen": inbox["events_seen"],
            "needs_attention": inbox["needs_attention"],
            "waiting_or_blocked": inbox["waiting_or_blocked"],
            "ready": inbox["ready"],
            "completed": inbox["completed"],
            "rollups": inbox["rollups"],
            "noise_candidates": inbox["noise_candidates"],
        },
        "runtime": runtime_status,
        "follow_up": follow_up,
    }

    snapshot_date = generated_at.date().isoformat()
    bridge_save: BridgeSaveReport = {
        "attempted": False,
        "status": "not_requested",
        "db_path": str(bridge_db_path) if bridge_db_path is not None else None,
        "snapshot_id": None,
        "snapshot_date": None,
        "error": None,
    }
    if save_bridge_db:
        bridge_save = _save_bridge_snapshot(
            bridge_snapshot,
            snapshot_date=snapshot_date,
            db_path=bridge_db_path,
        )

    status = (
        "ok"
        if inbox["status"] == "ok"
        and runtime_status["status"] == "ok"
        and bridge_save["status"] != "degraded"
        else "degraded"
    )
    error = inbox["error"] if inbox["error"] is not None else None
    if error is None and bridge_save["error"] is not None:
        error = bridge_save["error"]

    return {
        "status": status,
        "schema_version": "notification-hub.coordination_snapshot.v1",
        "generated_at": generated_at.isoformat(),
        "bridge_target_system": "codex",
        "bridge_snapshot_date": snapshot_date,
        "bridge_snapshot": bridge_snapshot,
        "bridge_save": bridge_save,
        "inbox": inbox,
        "runtime_status": runtime_status,
        "follow_up": follow_up,
        "error": error,
    }


def run_personal_ops_action_export(
    *,
    hours: int = 24,
    limit: int = 10,
    save_review_package: bool = False,
    review_dir: Path | None = None,
) -> PersonalOpsActionExportReport:
    """Prepare personal-ops action proposals without mutating personal-ops."""
    window_hours = max(hours, 1)
    item_limit = max(limit, 1)
    generated_at = datetime.now(timezone.utc).isoformat()
    inbox = run_inbox(hours=window_hours, limit=item_limit)
    actions = [
        _action_from_rollup(rollup)
        for rollup in inbox["rollups"]
        if rollup["intent"] in {"needs_attention", "blocked", "waiting_on_user", "ready_to_review", "ready_to_merge", "automation_failed"}
    ][:item_limit]

    report: PersonalOpsActionExportReport = {
        "status": inbox["status"],
        "schema_version": ACTION_EXPORT_SCHEMA_VERSION,
        "generated_at": generated_at,
        "hours": window_hours,
        "actions": actions,
        "review_package": {
            "requested": False,
            "status": "not_requested",
            "path": str(review_dir) if review_dir is not None else None,
            "error": None,
        },
        "inbox": inbox,
        "error": inbox["error"],
    }
    if save_review_package:
        review_payload = dict(report)
        review_payload["review_package"] = {
            "requested": True,
            "status": "pending_write",
            "path": str(review_dir) if review_dir is not None else None,
            "error": None,
        }
        review_package = _write_action_review_package(review_payload, output_dir=review_dir)
        report["review_package"] = review_package
        if review_package["status"] == "degraded":
            report["status"] = "degraded"
            report["error"] = str(review_package["error"])
    return report


def validate_action_package(path: Path) -> ActionPackageValidationReport:
    """Validate a saved personal-ops action package without importing it."""
    errors: list[str] = []
    warnings: list[str] = []
    schema_version: str | None = None
    action_count = 0
    valid_action_count = 0

    try:
        payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "degraded",
            "path": str(path),
            "schema_version": None,
            "action_count": 0,
            "valid_action_count": 0,
            "warning_count": 0,
            "error_count": 1,
            "warnings": [],
            "errors": [str(exc)],
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


def run_personal_ops_import_stub(*, path: Path, dry_run: bool = True) -> PersonalOpsImportReport:
    """Validate an action package and refuse mutation until apply semantics exist."""
    validation = validate_action_package(path)
    if validation["status"] != "ok":
        return {
            "status": "degraded",
            "path": str(path),
            "dry_run": dry_run,
            "applied": False,
            "validation": validation,
            "next_action": "Fix the validation errors before importing this package.",
            "error": "package validation failed",
        }

    return {
        "status": "ok",
        "path": str(path),
        "dry_run": dry_run,
        "applied": False,
        "validation": validation,
        "next_action": (
            "Package is valid. Build an explicit personal-ops apply integration before mutation."
        ),
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


def run_delivery_check(*, verify_slack: bool = False, verify_push: bool = False) -> DeliveryCheckReport:
    """Send explicit opt-in transport checks for Slack and/or push delivery."""
    event = StoredEvent(
        source="codex",
        level="normal",
        classified_level="normal",
        title="Notification Hub delivery check",
        body="Explicit operator-requested delivery verification.",
        project="notification-hub",
    )
    slack_ok = send_slack(event) if verify_slack else None
    push_ok = send_push(event) if verify_push else None
    failures: list[str] = []
    if slack_ok is False:
        failures.append("Slack delivery check failed")
    if push_ok is False:
        failures.append("Push delivery check failed")

    return {
        "status": "ok" if not failures else "degraded",
        "verify_slack": verify_slack,
        "verify_push": verify_push,
        "slack_ok": slack_ok,
        "push_ok": push_ok,
        "event_id": event.event_id,
        "error": "; ".join(failures) if failures else None,
    }


def run_verify_runtime(
    *,
    include_smoke: bool = False,
    verify_slack: bool = False,
    verify_push: bool = False,
) -> VerifyRuntimeReport:
    """Aggregate the core runtime checks into one operator-facing report."""
    doctor = collect_doctor_report()
    policy_check = run_policy_check()
    burn_in = run_burn_in(minutes=10, lines=200)
    smoke = run_smoke_check() if include_smoke else None
    delivery_check = (
        run_delivery_check(verify_slack=verify_slack, verify_push=verify_push)
        if verify_slack or verify_push
        else None
    )

    doctor_checks = _as_dict(doctor.get("checks"))
    local_api = _as_dict(doctor.get("local_api"))
    runtime_wiring = _as_dict(doctor.get("runtime_wiring"))

    checks = {
        "doctor_ok": doctor.get("status") == "ok",
        "policy_check_ok": policy_check["status"] != "degraded",
        "health_details_reachable": local_api.get("reachable") is True,
        "runtime_wiring_current": doctor_checks.get("runtime_wiring_current") is True,
        "recent_runtime_health_ok": burn_in["health"]["status"] == "ok",
        "smoke_ok": smoke is None or smoke["status"] == "ok",
        "delivery_check_ok": delivery_check is None or delivery_check["status"] == "ok",
    }
    status = "ok" if all(checks.values()) else "degraded"
    health_url = local_api.get("url")

    return {
        "status": status,
        "read_only": smoke is None and delivery_check is None,
        "include_smoke": include_smoke,
        "health_url": health_url if isinstance(health_url, str) else None,
        "checks": checks,
        "runtime_wiring": {key: bool(value) for key, value in runtime_wiring.items()},
        "doctor": doctor,
        "policy_check": policy_check,
        "burn_in": burn_in,
        "delivery_check": delivery_check,
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
    burn_in = verification["burn_in"]
    burn_in_health = burn_in["health"]

    daemon_reachable = checks["health_details_reachable"]
    runtime_wiring_current = checks["runtime_wiring_current"]
    policy_load_ok = doctor_checks.get("policy_load_ok") is True
    slack_delivery_failures = burn_in_health["slack_delivery_failure_count"]

    if verification["status"] == "ok":
        next_action = "No action needed."
    elif not daemon_reachable:
        next_action = "Start or restart the LaunchAgent, then run verify-runtime again."
    elif not runtime_wiring_current:
        next_action = "Refresh runtime templates from ops/, then run verify-runtime again."
    elif not checks["recent_runtime_health_ok"] and slack_delivery_failures > 0:
        next_action = "Inspect notification-hub logs for Slack delivery failures, then verify Slack transport."
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
        "slack_delivery_failures": slack_delivery_failures,
        "next_action": next_action,
    }
