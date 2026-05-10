"""Operator actions beyond diagnostics: smoke checks and log retention."""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import sqlite3
import tempfile
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


class ActionReviewPackageDetailReport(TypedDict):
    status: str
    path: str
    name: str
    schema_version: str | None
    generated_at: str | None
    hours: int | None
    actions: list[dict[str, object]]
    queue_items: list[PersonalOpsImportQueueItemReport]
    validation: ActionPackageValidationReport
    applied: bool
    error: str | None


class ActionReviewPackageDeleteReport(TypedDict):
    status: str
    path: str
    name: str
    deleted: bool
    applied: bool
    error: str | None


class PersonalOpsImportReport(TypedDict):
    status: str
    path: str
    dry_run: bool
    applied: bool
    enqueued: bool
    queued_count: int
    skipped_count: int
    queue_path: str | None
    validation: ActionPackageValidationReport
    next_action: str
    error: str | None


class PersonalOpsImportQueueItemReport(TypedDict):
    queue_id: str
    status: str
    enqueued_at: str
    updated_at: str | None
    source_package_name: str
    source_package_path: str
    action_id: str
    title: str
    summary: str
    priority: str
    state: str
    evidence_event_id: str
    applied: bool
    snoozed_until: str | None
    outcome_reason: str | None
    promoted_at: str | None
    promotion_target: str | None
    promotion_target_id: str | None
    promotion_outcome: str | None
    promotion_outcome_at: str | None
    promotion_outcome_note: str | None


class PersonalOpsImportQueueUpdateReport(TypedDict):
    status: str
    queue_id: str
    queue_path: str
    updated: bool
    item: PersonalOpsImportQueueItemReport | None
    next_action: str
    error: str | None


class PersonalOpsImportQueueHealthReport(TypedDict):
    status: str
    queue_path: str
    total_count: int
    queued_count: int
    reviewed_count: int
    rejected_count: int
    snoozed_count: int
    superseded_count: int
    promoted_count: int
    promoted_pending_count: int
    promoted_pending_stale_count: int
    promoted_accepted_count: int
    promoted_rejected_count: int
    promoted_ignored_count: int
    needs_outcome_sync: bool
    needs_review: bool
    oldest_queued_at: str | None
    oldest_queued_age_seconds: float | None
    oldest_promoted_pending_at: str | None
    oldest_promoted_pending_age_seconds: float | None
    stale_after_hours: float
    next_action: str


class PersonalOpsImportQueueHealthCheckReport(TypedDict):
    status: str
    health: PersonalOpsImportQueueHealthReport
    queued_items: list[PersonalOpsImportQueueItemReport]
    pending_promotion_items: list[PersonalOpsImportQueueItemReport]
    next_commands: list[str]
    applied: bool


class PersonalOpsOutcomeSyncReminderReport(TypedDict):
    status: str
    should_remind: bool
    pending_count: int
    stale_count: int
    reminders: list[PersonalOpsImportQueueItemReport]
    next_commands: list[str]
    next_action: str
    applied: bool


class PersonalOpsQueueScenarioReport(TypedDict):
    status: str
    queue_path: str
    package_path: str
    queue_id: str | None
    queued_count: int
    review_status: str | None
    promotion_status: str | None
    promotion_outcome: str | None
    final_health: PersonalOpsImportQueueHealthReport
    applied: bool
    next_action: str
    error: str | None


class PersonalOpsQueueBurnInReport(TypedDict):
    status: str
    queue_health: PersonalOpsImportQueueHealthCheckReport
    scenario: PersonalOpsQueueScenarioReport
    runtime_burn_in: BurnInReport
    ready_for_live_promotion: bool
    outcome_sync_posture: str
    operator_steps: list[str]
    next_action: str
    report_file: dict[str, object]
    applied: bool


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
    noise_rule_suggestions: list[str]
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
    import_queue: PersonalOpsImportQueueHealthReport
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
    import_queue: PersonalOpsImportQueueHealthReport
    next_action: str


DEFAULT_BRIDGE_DB_PATH = Path.home() / ".local" / "share" / "bridge-db" / "bridge.db"
BRIDGE_SNAPSHOT_RETENTION_PER_SYSTEM = 10
ACTION_EXPORT_DIR = EVENTS_DIR / "action-exports"
BURN_IN_REPORT_DIR = EVENTS_DIR / "burn-in-reports"
PERSONAL_OPS_IMPORT_QUEUE = EVENTS_DIR / "personal-ops-import-queue.jsonl"
ACTION_EXPORT_SCHEMA_VERSION = "notification-hub.personal_ops_action_export.v1"
PERSONAL_OPS_IMPORT_QUEUE_SCHEMA_VERSION = "notification-hub.personal_ops_import_queue.v1"
PERSONAL_OPS_QUEUE_STATUSES = {
    "queued",
    "reviewed",
    "rejected",
    "snoozed",
    "superseded",
    "promoted",
}
PERSONAL_OPS_PROMOTION_OUTCOMES = {"pending", "accepted", "rejected", "ignored"}
PERSONAL_OPS_OUTCOME_SYNC_POSTURE = (
    "operator-mediated; notification-hub reports pending or stale promoted outcomes "
    "but does not create, accept, reject, or sync personal-ops work itself"
)


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
    validation_errors = [
        line for line in current_stderr_tail if line.startswith("Rejected event payload")
    ]
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
    evidence_part = (
        re.sub(r"[^a-z0-9]+", "-", rollup["latest_event_id"].lower()).strip("-") or "event"
    )
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
        target_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
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


def _empty_package_validation(path: Path, error: str) -> ActionPackageValidationReport:
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
        and re.fullmatch(r"personal-ops-actions-\d{8}-\d{6}\.json", name) is not None
    )


def load_action_review_package_detail(
    *,
    name: str,
    review_dir: Path | None = None,
    queue_path: Path | None = None,
) -> ActionReviewPackageDetailReport:
    """Load a saved review package summary without importing or applying it."""
    target_dir = review_dir or ACTION_EXPORT_DIR
    target_path = target_dir / name
    if not _is_safe_action_review_package_name(name):
        error = "invalid review package name"
        return {
            "status": "degraded",
            "path": str(target_path),
            "name": name,
            "schema_version": None,
            "generated_at": None,
            "hours": None,
            "actions": [],
            "queue_items": [],
            "validation": _empty_package_validation(target_path, error),
            "applied": False,
            "error": error,
        }

    validation = validate_action_package(target_path)
    try:
        payload = json.loads(target_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "degraded",
            "path": str(target_path),
            "name": name,
            "schema_version": None,
            "generated_at": None,
            "hours": None,
            "actions": [],
            "queue_items": [],
            "validation": validation,
            "applied": False,
            "error": str(exc),
        }

    if not isinstance(payload, dict):
        error = "package root must be an object"
        return {
            "status": "degraded",
            "path": str(target_path),
            "name": name,
            "schema_version": None,
            "generated_at": None,
            "hours": None,
            "actions": [],
            "queue_items": [],
            "validation": validation,
            "applied": False,
            "error": error,
        }

    package = cast(dict[str, object], payload)
    actions = _action_dicts_from_payload(package)
    queue_items = [
        item
        for item in list_personal_ops_import_queue(queue_path=queue_path, limit=500)
        if item["source_package_name"] == name
    ]
    return {
        "status": validation["status"],
        "path": str(target_path),
        "name": name,
        "schema_version": _as_str(package.get("schema_version")),
        "generated_at": _as_str(package.get("generated_at")),
        "hours": _as_int(package.get("hours")),
        "actions": actions,
        "queue_items": queue_items,
        "validation": validation,
        "applied": False,
        "error": None if validation["status"] == "ok" else "package validation failed",
    }


def delete_action_review_package(
    *,
    name: str,
    review_dir: Path | None = None,
) -> ActionReviewPackageDeleteReport:
    """Delete one saved review package without importing or applying it."""
    target_dir = review_dir or ACTION_EXPORT_DIR
    target_path = target_dir / name
    if not _is_safe_action_review_package_name(name):
        return {
            "status": "degraded",
            "path": str(target_path),
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
    except OSError as exc:
        return {
            "status": "degraded",
            "path": str(target_path),
            "name": name,
            "deleted": False,
            "applied": False,
            "error": str(exc),
        }
    return {
        "status": "ok",
        "path": str(target_path),
        "name": name,
        "deleted": True,
        "applied": False,
        "error": None,
    }


def _load_action_package_payload(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return cast(dict[str, object], payload)


def _action_dicts_from_payload(payload: dict[str, object]) -> list[dict[str, object]]:
    actions_value = payload.get("actions")
    actions: list[dict[str, object]] = []
    if isinstance(actions_value, list):
        for action_value in cast(list[object], actions_value):
            if isinstance(action_value, dict):
                actions.append(cast(dict[str, object], action_value))
    return actions


def _read_import_queue_items(queue_path: Path) -> list[dict[str, object]]:
    if not queue_path.exists():
        return []
    items: list[dict[str, object]] = []
    with open(queue_path, encoding="utf-8") as file:
        for line in file:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                item = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                items.append(cast(dict[str, object], item))
    return items


def _write_import_queue_items(queue_path: Path, items: list[dict[str, object]]) -> None:
    queue_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp_path = queue_path.with_suffix(f"{queue_path.suffix}.tmp")
    with open(tmp_path, "w", encoding="utf-8") as file:
        for item in items:
            file.write(json.dumps(item, sort_keys=True))
            file.write("\n")
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, queue_path)


def _import_queue_item_report(item: dict[str, object]) -> PersonalOpsImportQueueItemReport:
    action = _as_dict(item.get("action"))
    return {
        "queue_id": _as_str(item.get("queue_id")) or "",
        "status": _as_str(item.get("status")) or "unknown",
        "enqueued_at": _as_str(item.get("enqueued_at")) or "",
        "updated_at": _as_str(item.get("updated_at")),
        "source_package_name": _as_str(item.get("source_package_name")) or "",
        "source_package_path": _as_str(item.get("source_package_path")) or "",
        "action_id": _as_str(item.get("action_id")) or "",
        "title": _as_str(action.get("title")) or "",
        "summary": _as_str(action.get("summary")) or "",
        "priority": _as_str(action.get("priority")) or "",
        "state": _as_str(action.get("state")) or "",
        "evidence_event_id": _as_str(action.get("evidence_event_id")) or "",
        "applied": bool(item.get("applied")),
        "snoozed_until": _as_str(item.get("snoozed_until")),
        "outcome_reason": _as_str(item.get("outcome_reason")),
        "promoted_at": _as_str(item.get("promoted_at")),
        "promotion_target": _as_str(item.get("promotion_target")),
        "promotion_target_id": _as_str(item.get("promotion_target_id")),
        "promotion_outcome": _as_str(item.get("promotion_outcome")),
        "promotion_outcome_at": _as_str(item.get("promotion_outcome_at")),
        "promotion_outcome_note": _as_str(item.get("promotion_outcome_note")),
    }


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def summarize_personal_ops_import_queue(
    *,
    queue_path: Path | None = None,
    stale_after_hours: float = 4.0,
) -> PersonalOpsImportQueueHealthReport:
    """Summarize queue lifecycle health without applying queued items."""
    target_queue_path = queue_path or PERSONAL_OPS_IMPORT_QUEUE
    try:
        raw_items = _read_import_queue_items(target_queue_path)
    except OSError:
        raw_items = []
    counts = {status: 0 for status in PERSONAL_OPS_QUEUE_STATUSES}
    promotion_outcomes = {outcome: 0 for outcome in PERSONAL_OPS_PROMOTION_OUTCOMES}
    for item in raw_items:
        status = _as_str(item.get("status")) or "unknown"
        if status in counts:
            counts[status] += 1
        if status == "promoted":
            outcome = _as_str(item.get("promotion_outcome")) or "pending"
            if outcome in promotion_outcomes:
                promotion_outcomes[outcome] += 1
    now = datetime.now(timezone.utc)
    queued_items = [item for item in raw_items if item.get("status") == "queued"]
    queued_datetimes = [
        parsed
        for item in queued_items
        if (parsed := _parse_iso_datetime(_as_str(item.get("enqueued_at")))) is not None
    ]
    oldest = min(queued_datetimes) if queued_datetimes else None
    age = (now - oldest).total_seconds() if oldest is not None else None
    pending_items = [
        item
        for item in raw_items
        if item.get("status") == "promoted"
        and (_as_str(item.get("promotion_outcome")) or "pending") == "pending"
    ]
    pending_datetimes = [
        parsed
        for item in pending_items
        if (
            parsed := _parse_iso_datetime(
                _as_str(item.get("promotion_outcome_at"))
                or _as_str(item.get("promoted_at"))
                or _as_str(item.get("updated_at"))
            )
        )
        is not None
    ]
    oldest_pending = min(pending_datetimes) if pending_datetimes else None
    oldest_pending_age = (
        (now - oldest_pending).total_seconds() if oldest_pending is not None else None
    )
    stale_after_seconds = max(stale_after_hours, 0) * 60 * 60
    stale_pending_count = sum(
        1
        for item in pending_items
        if (
            parsed := _parse_iso_datetime(
                _as_str(item.get("promotion_outcome_at"))
                or _as_str(item.get("promoted_at"))
                or _as_str(item.get("updated_at"))
            )
        )
        is not None
        and (now - parsed).total_seconds() >= stale_after_seconds
    )
    queued_count = counts["queued"]
    promoted_pending_count = promotion_outcomes["pending"]
    status = "warn" if queued_count > 0 or promoted_pending_count > 0 else "ok"
    if queued_count > 0:
        next_action = "Review queued personal-ops handoff items."
    elif stale_pending_count > 0:
        next_action = (
            "Resolve the matching personal-ops suggestion, record the promotion outcome, "
            "then rerun notification-hub personal-ops-queue-health."
        )
    elif promoted_pending_count > 0:
        next_action = "Resolve promoted personal-ops handoff outcomes."
    else:
        next_action = "No queued personal-ops handoff items."
    return {
        "status": status,
        "queue_path": str(target_queue_path),
        "total_count": len(raw_items),
        "queued_count": queued_count,
        "reviewed_count": counts["reviewed"],
        "rejected_count": counts["rejected"],
        "snoozed_count": counts["snoozed"],
        "superseded_count": counts["superseded"],
        "promoted_count": counts["promoted"],
        "promoted_pending_count": promoted_pending_count,
        "promoted_pending_stale_count": stale_pending_count,
        "promoted_accepted_count": promotion_outcomes["accepted"],
        "promoted_rejected_count": promotion_outcomes["rejected"],
        "promoted_ignored_count": promotion_outcomes["ignored"],
        "needs_outcome_sync": promoted_pending_count > 0,
        "needs_review": queued_count > 0,
        "oldest_queued_at": oldest.isoformat() if oldest is not None else None,
        "oldest_queued_age_seconds": age,
        "oldest_promoted_pending_at": oldest_pending.isoformat()
        if oldest_pending is not None
        else None,
        "oldest_promoted_pending_age_seconds": oldest_pending_age,
        "stale_after_hours": stale_after_hours,
        "next_action": next_action,
    }


def run_personal_ops_import_queue_health_check(
    *,
    queue_path: Path | None = None,
    limit: int = 10,
    stale_after_hours: float = 4.0,
) -> PersonalOpsImportQueueHealthCheckReport:
    """Return the operator-facing queue maintenance state without mutating queue items."""
    health = summarize_personal_ops_import_queue(
        queue_path=queue_path, stale_after_hours=stale_after_hours
    )
    items = list_personal_ops_import_queue(queue_path=queue_path, limit=max(limit, 1))
    queued_items = [item for item in items if item["status"] == "queued"]
    pending_items = [
        item
        for item in items
        if item["status"] == "promoted" and (item["promotion_outcome"] or "pending") == "pending"
    ]
    next_commands: list[str] = []
    if health["queued_count"] > 0:
        next_commands.append("uv run notification-hub personal-ops-queue")
        next_commands.append('personal-ops notification-hub promote QUEUE_ID --note "..."')
    if health["promoted_pending_count"] > 0:
        next_commands.append('personal-ops suggestion accept|reject SUGGESTION_ID --note "..."')
        next_commands.append(
            "uv run notification-hub personal-ops-queue --queue-id QUEUE_ID "
            "--status promoted --promotion-target-id SUGGESTION_ID "
            '--promotion-outcome accepted|rejected --promotion-outcome-note "..."'
        )
    next_commands.append("uv run notification-hub personal-ops-queue-health")
    return {
        "status": health["status"],
        "health": health,
        "queued_items": queued_items,
        "pending_promotion_items": pending_items,
        "next_commands": next_commands,
        "applied": False,
    }


def run_personal_ops_outcome_sync_reminder(
    *,
    queue_path: Path | None = None,
    limit: int = 10,
    stale_after_hours: float = 4.0,
) -> PersonalOpsOutcomeSyncReminderReport:
    """Report promoted handoffs that need downstream outcome sync, without mutating them."""
    queue_report = run_personal_ops_import_queue_health_check(
        queue_path=queue_path,
        limit=limit,
        stale_after_hours=stale_after_hours,
    )
    health = queue_report["health"]
    reminders = queue_report["pending_promotion_items"]
    should_remind = health["promoted_pending_count"] > 0
    if health["promoted_pending_stale_count"] > 0:
        next_action = (
            "Resolve stale promoted personal-ops handoff outcomes before promoting more work."
        )
    elif should_remind:
        next_action = "Resolve promoted personal-ops handoff outcomes when downstream decisions are available."
    else:
        next_action = "No pending promoted personal-ops handoff outcomes."

    return {
        "status": "warn" if should_remind else "ok",
        "should_remind": should_remind,
        "pending_count": health["promoted_pending_count"],
        "stale_count": health["promoted_pending_stale_count"],
        "reminders": reminders,
        "next_commands": (
            [
                'personal-ops suggestion accept|reject SUGGESTION_ID --note "..."',
                "uv run notification-hub personal-ops-queue --queue-id QUEUE_ID "
                "--status promoted --promotion-target-id SUGGESTION_ID "
                '--promotion-outcome accepted|rejected --promotion-outcome-note "..."',
            ]
            if should_remind
            else ["uv run notification-hub personal-ops-queue-health"]
        ),
        "next_action": next_action,
        "applied": False,
    }


def _personal_ops_queue_operator_steps(health: PersonalOpsImportQueueHealthReport) -> list[str]:
    if health["queued_count"] > 0:
        return [
            "Open http://127.0.0.1:9199/review and inspect the queued handoff evidence.",
            "Promote one reviewed handoff through personal-ops, then record it as promoted with the returned suggestion id.",
            "Accept or reject the matching personal-ops suggestion, then record the outcome on the queue item.",
            "Rerun notification-hub personal-ops-queue-health and confirm pending/stale counts return to zero.",
        ]
    if health["promoted_pending_stale_count"] > 0:
        return [
            "Resolve the stale personal-ops suggestion and record the outcome on the queue item.",
            "Rerun notification-hub personal-ops-queue-health and confirm stale pending outcomes clear.",
        ]
    if health["promoted_pending_count"] > 0:
        return [
            "Accept or reject the personal-ops suggestion when the downstream decision is available.",
            "Record the accepted or rejected outcome on the queue item.",
            "Rerun notification-hub personal-ops-queue-health and confirm pending outcomes clear.",
        ]
    return [
        "Save or enqueue a real review package from /review when there is a real operator signal worth promoting.",
        "Run notification-hub personal-ops-queue-burn-in after the first live promotion to recheck queue, runtime, and noise health.",
    ]


def run_personal_ops_queue_burn_in(
    *,
    minutes: int = 10,
    lines: int = 200,
    limit: int = 10,
    save_report: bool = False,
    report_dir: Path | None = None,
) -> PersonalOpsQueueBurnInReport:
    """Report whether the queue loop is ready for live operator burn-in."""
    queue_health = run_personal_ops_import_queue_health_check(limit=limit)
    scenario = run_personal_ops_queue_scenario()
    runtime_burn_in = run_burn_in(minutes=minutes, lines=lines)
    health = queue_health["health"]
    ready_for_live_promotion = (
        scenario["status"] == "ok"
        and runtime_burn_in["health"]["status"] == "ok"
        and health["promoted_pending_stale_count"] == 0
    )
    status = "ok" if ready_for_live_promotion and queue_health["status"] == "ok" else "warn"
    operator_steps = _personal_ops_queue_operator_steps(health)
    if health["queued_count"] > 0:
        next_action = "Promote one reviewed handoff, then record the personal-ops outcome."
    elif health["promoted_pending_stale_count"] > 0:
        next_action = "Record stale promoted handoff outcomes before promoting more work."
    elif health["promoted_pending_count"] > 0:
        next_action = "Wait for or record the pending promoted handoff outcome."
    elif ready_for_live_promotion:
        next_action = (
            "Queue loop is ready; use the operator steps when the next real handoff appears."
        )
    else:
        next_action = "Fix the queue scenario or runtime burn-in warning before live promotion."
    report: PersonalOpsQueueBurnInReport = {
        "status": status,
        "queue_health": queue_health,
        "scenario": scenario,
        "runtime_burn_in": runtime_burn_in,
        "ready_for_live_promotion": ready_for_live_promotion,
        "outcome_sync_posture": PERSONAL_OPS_OUTCOME_SYNC_POSTURE,
        "operator_steps": operator_steps,
        "next_action": next_action,
        "report_file": {
            "requested": save_report,
            "status": "not_requested",
            "path": None,
            "error": None,
        },
        "applied": False,
    }
    if save_report:
        report_file = _write_personal_ops_queue_burn_in_report(report, output_dir=report_dir)
        report["report_file"] = report_file
        if report_file["status"] != "ok":
            report["status"] = "warn"
    return report


def _write_personal_ops_queue_burn_in_report(
    report: PersonalOpsQueueBurnInReport,
    *,
    output_dir: Path | None = None,
) -> dict[str, object]:
    target_dir = output_dir or BURN_IN_REPORT_DIR
    generated_at = datetime.now(timezone.utc)
    target_path = target_dir / (
        f"personal-ops-queue-burn-in-{generated_at.strftime('%Y%m%d-%H%M%S')}.json"
    )
    payload = {
        "schema_version": "notification-hub.personal_ops_queue_burn_in.v1",
        "generated_at": generated_at.isoformat(),
        "report": {key: value for key, value in report.items() if key != "report_file"},
    }
    try:
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(str(target_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
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


def _enqueue_personal_ops_import_actions(
    *,
    package_path: Path,
    queue_path: Path | None = None,
) -> tuple[str, int, int]:
    target_queue_path = queue_path or PERSONAL_OPS_IMPORT_QUEUE
    payload = _load_action_package_payload(package_path)
    actions = _action_dicts_from_payload(payload) if payload is not None else []
    existing_items = _read_import_queue_items(target_queue_path)
    existing_action_ids = {
        action_id
        for item in existing_items
        if item.get("status") == "queued" and isinstance((action_id := item.get("action_id")), str)
    }
    enqueued_at = datetime.now(timezone.utc).isoformat()
    queued_count = 0
    skipped_count = 0
    target_queue_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(target_queue_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        for action in actions:
            action_id = action.get("action_id")
            if not isinstance(action_id, str) or action_id in existing_action_ids:
                skipped_count += 1
                continue
            queue_seed = f"{action_id}:{package_path.expanduser()}".encode("utf-8")
            queue_item = {
                "schema_version": PERSONAL_OPS_IMPORT_QUEUE_SCHEMA_VERSION,
                "queue_id": hashlib.sha256(queue_seed).hexdigest()[:16],
                "status": "queued",
                "enqueued_at": enqueued_at,
                "source_package_path": str(package_path),
                "source_package_name": package_path.name,
                "action_id": action_id,
                "action": action,
                "applied": False,
            }
            os.write(fd, (json.dumps(queue_item, sort_keys=True) + "\n").encode("utf-8"))
            existing_action_ids.add(action_id)
            queued_count += 1
    finally:
        os.close(fd)
    return str(target_queue_path), queued_count, skipped_count


def list_personal_ops_import_queue(
    *,
    queue_path: Path | None = None,
    limit: int = 10,
) -> list[PersonalOpsImportQueueItemReport]:
    """List queued personal-ops handoff items without applying them."""
    target_queue_path = queue_path or PERSONAL_OPS_IMPORT_QUEUE
    try:
        raw_items = _read_import_queue_items(target_queue_path)
    except OSError:
        return []
    reports: list[PersonalOpsImportQueueItemReport] = []
    for item in reversed(raw_items):
        reports.append(_import_queue_item_report(item))
        if len(reports) >= max(limit, 1):
            break
    return reports


def update_personal_ops_import_queue_item(
    *,
    queue_id: str,
    status: str,
    reason: str | None = None,
    snoozed_until: str | None = None,
    promotion_target: str | None = None,
    promotion_target_id: str | None = None,
    promotion_outcome: str | None = None,
    promotion_outcome_note: str | None = None,
    queue_path: Path | None = None,
) -> PersonalOpsImportQueueUpdateReport:
    """Update one queued personal-ops handoff lifecycle state without applying work."""
    target_queue_path = queue_path or PERSONAL_OPS_IMPORT_QUEUE
    if status not in PERSONAL_OPS_QUEUE_STATUSES:
        return {
            "status": "degraded",
            "queue_id": queue_id,
            "queue_path": str(target_queue_path),
            "updated": False,
            "item": None,
            "next_action": "Use one of: queued, reviewed, rejected, snoozed, superseded, promoted.",
            "error": f"invalid queue status: {status}",
        }
    if promotion_outcome is not None and promotion_outcome not in PERSONAL_OPS_PROMOTION_OUTCOMES:
        return {
            "status": "degraded",
            "queue_id": queue_id,
            "queue_path": str(target_queue_path),
            "updated": False,
            "item": None,
            "next_action": "Use one of: pending, accepted, rejected, ignored.",
            "error": f"invalid promotion outcome: {promotion_outcome}",
        }
    if status == "snoozed" and snoozed_until is None:
        return {
            "status": "degraded",
            "queue_id": queue_id,
            "queue_path": str(target_queue_path),
            "updated": False,
            "item": None,
            "next_action": "Pass --snoozed-until when snoozing a queued handoff.",
            "error": "snoozed_until is required for snoozed status",
        }
    try:
        raw_items = _read_import_queue_items(target_queue_path)
    except OSError as exc:
        return {
            "status": "degraded",
            "queue_id": queue_id,
            "queue_path": str(target_queue_path),
            "updated": False,
            "item": None,
            "next_action": "Fix queue read errors, then retry the lifecycle update.",
            "error": str(exc),
        }

    matched: dict[str, object] | None = None
    now = datetime.now(timezone.utc).isoformat()
    for item in raw_items:
        if item.get("queue_id") != queue_id:
            continue
        item["status"] = status
        item["updated_at"] = now
        if reason:
            item["outcome_reason"] = reason
        if status == "reviewed":
            item["reviewed_at"] = now
        elif status == "rejected":
            item["rejected_at"] = now
        elif status == "snoozed":
            item["snoozed_at"] = now
            item["snoozed_until"] = snoozed_until
        elif status == "superseded":
            item["superseded_at"] = now
        elif status == "promoted":
            item["promoted_at"] = now
            item["promotion_target"] = promotion_target or "personal-ops task suggestion"
            if promotion_target_id:
                item["promotion_target_id"] = promotion_target_id
            item["promotion_outcome"] = (
                promotion_outcome or _as_str(item.get("promotion_outcome")) or "pending"
            )
            if promotion_outcome is not None:
                item["promotion_outcome_at"] = now
            if promotion_outcome_note:
                item["promotion_outcome_note"] = promotion_outcome_note
            item["applied"] = True
        elif status == "queued":
            item["applied"] = False
            item.pop("promoted_at", None)
            item.pop("promotion_target", None)
            item.pop("promotion_target_id", None)
            item.pop("promotion_outcome", None)
            item.pop("promotion_outcome_at", None)
            item.pop("promotion_outcome_note", None)
        if status != "queued" and promotion_outcome is not None:
            item["promotion_outcome"] = promotion_outcome
            item["promotion_outcome_at"] = now
        if status != "queued" and promotion_outcome_note:
            item["promotion_outcome_note"] = promotion_outcome_note
        matched = item
        break

    if matched is None:
        return {
            "status": "degraded",
            "queue_id": queue_id,
            "queue_path": str(target_queue_path),
            "updated": False,
            "item": None,
            "next_action": "Refresh the import queue and choose an existing queue id.",
            "error": "queue item not found",
        }

    try:
        _write_import_queue_items(target_queue_path, raw_items)
    except OSError as exc:
        return {
            "status": "degraded",
            "queue_id": queue_id,
            "queue_path": str(target_queue_path),
            "updated": False,
            "item": None,
            "next_action": "Fix queue write errors, then retry the lifecycle update.",
            "error": str(exc),
        }

    if status == "promoted":
        item_report = _import_queue_item_report(matched)
        if item_report["promotion_outcome"] == "pending":
            next_action = "Accept or reject the matching personal-ops task suggestion, then record the outcome."
        else:
            next_action = "Promotion outcome is recorded; no queue action is needed."
    elif status == "rejected":
        next_action = "No personal-ops action will be created for this handoff."
    elif status == "snoozed":
        next_action = "Review this handoff again after the snooze window."
    else:
        next_action = "Continue reviewing the import queue."
    return {
        "status": "ok",
        "queue_id": queue_id,
        "queue_path": str(target_queue_path),
        "updated": True,
        "item": _import_queue_item_report(matched),
        "next_action": next_action,
        "error": None,
    }


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


def _noise_rule_suggestions(noise_candidates: list[RepeatedSignatureReport]) -> list[str]:
    suggestions: list[str] = []
    for item in noise_candidates[:5]:
        parts = [
            f"source={item['source']!r}",
            f"title_contains={item['title']!r}",
            f"level={item['level']!r}",
            "window_minutes=10",
        ]
        if item["project"] is not None:
            parts.insert(1, f"project={item['project']!r}")
        suggestions.append("Review noise rule candidate: " + ", ".join(parts))
    return suggestions


def run_burn_in(*, minutes: int = 10, lines: int = 200) -> BurnInReport:
    """Summarize recent health failures and repeated/noisy event signatures."""
    window_minutes = max(minutes, 1)
    tail_lines = max(lines, 0)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    try:
        stored_events = read_jsonl(path=EVENTS_LOG)
        recent_events = [
            event for event in stored_events if event.timestamp.astimezone(timezone.utc) >= cutoff
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
            "noise_rule_suggestions": [],
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
    noise_rule_suggestions = _noise_rule_suggestions(noise_candidates)
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
        "noise_rule_suggestions": noise_rule_suggestions,
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
        follow_up.append(
            "Route waiting or blocked items into the action layer once bridge export is proven."
        )
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
        if rollup["intent"]
        in {
            "needs_attention",
            "blocked",
            "waiting_on_user",
            "ready_to_review",
            "ready_to_merge",
            "automation_failed",
        }
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


def run_personal_ops_import_stub(
    *,
    path: Path,
    dry_run: bool = True,
    enqueue: bool = False,
    queue_path: Path | None = None,
) -> PersonalOpsImportReport:
    """Validate an action package and optionally enqueue handoff items without applying them."""
    validation = validate_action_package(path)
    if validation["status"] != "ok":
        return {
            "status": "degraded",
            "path": str(path),
            "dry_run": dry_run,
            "applied": False,
            "enqueued": False,
            "queued_count": 0,
            "skipped_count": 0,
            "queue_path": str(queue_path) if queue_path is not None else None,
            "validation": validation,
            "next_action": "Fix the validation errors before importing this package.",
            "error": "package validation failed",
        }

    queue_result_path: str | None = None
    queued_count = 0
    skipped_count = 0
    if enqueue:
        try:
            queue_result_path, queued_count, skipped_count = _enqueue_personal_ops_import_actions(
                package_path=path,
                queue_path=queue_path,
            )
        except OSError as exc:
            return {
                "status": "degraded",
                "path": str(path),
                "dry_run": dry_run,
                "applied": False,
                "enqueued": False,
                "queued_count": 0,
                "skipped_count": 0,
                "queue_path": str(queue_path) if queue_path is not None else None,
                "validation": validation,
                "next_action": "Fix queue write errors before importing this package.",
                "error": str(exc),
            }

    return {
        "status": "ok",
        "path": str(path),
        "dry_run": dry_run,
        "applied": False,
        "enqueued": enqueue,
        "queued_count": queued_count,
        "skipped_count": skipped_count,
        "queue_path": queue_result_path,
        "validation": validation,
        "next_action": "Review the queued personal-ops handoff items."
        if enqueue
        else ("Package is valid. Use --enqueue to add it to the personal-ops import queue."),
        "error": None,
    }


def run_personal_ops_queue_scenario() -> PersonalOpsQueueScenarioReport:
    """Exercise the local queue lifecycle without touching the operator queue."""
    with tempfile.TemporaryDirectory(prefix="notification-hub-queue-scenario-") as tmp:
        scenario_dir = Path(tmp)
        package_path = scenario_dir / "personal-ops-actions-scenario.json"
        queue_path = scenario_dir / "personal-ops-import-queue.jsonl"
        package_path.write_text(
            json.dumps(
                {
                    "schema_version": ACTION_EXPORT_SCHEMA_VERSION,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "hours": 2,
                    "actions": [
                        {
                            "action_id": "scenario-action-1",
                            "title": "Scenario handoff",
                            "summary": "Exercise notification-hub to personal-ops queue lifecycle.",
                            "suggested_next_action": "Confirm the scripted scenario records the final promotion outcome.",
                            "priority": "high",
                            "state": "waiting",
                            "source": "notification-hub",
                            "project": "notification-hub",
                            "intent": "test",
                            "count": 1,
                            "evidence_event_id": "scenario-event-1",
                            "evidence_timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        imported = run_personal_ops_import_stub(
            path=package_path, enqueue=True, queue_path=queue_path
        )
        queue_items = list_personal_ops_import_queue(queue_path=queue_path, limit=1)
        queue_id = queue_items[0]["queue_id"] if queue_items else None
        if imported["status"] != "ok" or queue_id is None:
            return {
                "status": "degraded",
                "queue_path": str(queue_path),
                "package_path": str(package_path),
                "queue_id": queue_id,
                "queued_count": imported["queued_count"],
                "review_status": None,
                "promotion_status": None,
                "promotion_outcome": None,
                "final_health": summarize_personal_ops_import_queue(queue_path=queue_path),
                "applied": False,
                "next_action": "Fix scenario import or queue creation before relying on the lifecycle.",
                "error": imported["error"] or "scenario queue item was not created",
            }

        reviewed = update_personal_ops_import_queue_item(
            queue_id=queue_id,
            status="reviewed",
            reason="scenario evidence checked",
            queue_path=queue_path,
        )
        promoted = update_personal_ops_import_queue_item(
            queue_id=queue_id,
            status="promoted",
            reason="scenario personal-ops task suggestion created",
            promotion_target="personal-ops task suggestion",
            promotion_target_id="scenario-suggestion-1",
            promotion_outcome="pending",
            promotion_outcome_note="Scenario suggestion created; awaiting operator decision.",
            queue_path=queue_path,
        )
        accepted = update_personal_ops_import_queue_item(
            queue_id=queue_id,
            status="promoted",
            reason="scenario personal-ops task suggestion accepted",
            promotion_target="personal-ops task suggestion",
            promotion_target_id="scenario-suggestion-1",
            promotion_outcome="accepted",
            promotion_outcome_note="Scenario accepted.",
            queue_path=queue_path,
        )
        final_item = accepted["item"] or promoted["item"] or reviewed["item"]
        final_health = summarize_personal_ops_import_queue(queue_path=queue_path)
        status = (
            "ok"
            if reviewed["status"] == promoted["status"] == accepted["status"] == "ok"
            else "degraded"
        )
        return {
            "status": status,
            "queue_path": str(queue_path),
            "package_path": str(package_path),
            "queue_id": queue_id,
            "queued_count": imported["queued_count"],
            "review_status": reviewed["status"],
            "promotion_status": promoted["status"],
            "promotion_outcome": final_item["promotion_outcome"]
            if final_item is not None
            else None,
            "final_health": final_health,
            "applied": final_item["applied"] if final_item is not None else False,
            "next_action": "Scenario passed; use the same lifecycle for real queued handoffs."
            if status == "ok"
            else ("Inspect the scenario update reports before promoting real handoffs."),
            "error": accepted["error"] or promoted["error"] or reviewed["error"],
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


def run_delivery_check(
    *, verify_slack: bool = False, verify_push: bool = False
) -> DeliveryCheckReport:
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
    import_queue = summarize_personal_ops_import_queue()
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
        "import_queue_ok": import_queue["status"] == "ok",
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
        "import_queue": import_queue,
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
    import_queue = cast(
        PersonalOpsImportQueueHealthReport,
        cast(dict[str, object], verification).get("import_queue")
        or summarize_personal_ops_import_queue(),
    )

    daemon_reachable = checks["health_details_reachable"]
    runtime_wiring_current = checks["runtime_wiring_current"]
    policy_load_ok = doctor_checks.get("policy_load_ok") is True
    slack_delivery_failures = burn_in_health["slack_delivery_failure_count"]

    if import_queue["needs_review"] or import_queue["needs_outcome_sync"]:
        next_action = import_queue["next_action"]
    elif verification["status"] == "ok":
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
        "import_queue": import_queue,
        "next_action": next_action,
    }
