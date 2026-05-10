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
    NoiseRule,
    POLICY_CONFIG,
    PORT,
    analyze_policy_config,
    get_policy_config,
    load_policy_config_file,
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
    dismissal_key: str
    source: str
    project: str | None
    intent: str
    priority: str
    state: str
    title: str
    summary: str
    signal_level: str
    signal_body: str
    suggested_next_action: str
    evidence_event_id: str
    evidence_timestamp: str
    count: int


class ActionProposalDismissalReport(TypedDict):
    dismissal_key: str
    dismissed_at: str
    deleted_at: str | None
    active: bool
    reason: str
    source: str | None
    project: str | None
    intent: str | None
    title: str | None
    body: str | None
    evidence_event_id: str | None


class ActionProposalDismissReport(TypedDict):
    status: str
    path: str
    dismissal: ActionProposalDismissalReport | None
    applied: bool
    error: str | None


class ActionProposalDismissalListReport(TypedDict):
    status: str
    path: str
    dismissal_count: int
    dismissals: list[ActionProposalDismissalReport]
    applied: bool


class ActionProposalUndismissReport(TypedDict):
    status: str
    path: str
    dismissal_key: str
    removed: bool
    applied: bool
    error: str | None


class ActionProposalGroupPackageReport(TypedDict):
    status: str
    group_key: str
    action_count: int
    review_package: dict[str, object]
    import_result: dict[str, object] | None
    group_history: ActionProposalGroupHistoryReport | None
    next_action: str
    applied: bool
    error: str | None


class ActionProposalGroupDismissReport(TypedDict):
    status: str
    group_key: str
    dismissed_count: int
    dismissals: list[ActionProposalDismissalReport]
    group_history: "ActionProposalGroupHistoryReport | None"
    next_action: str
    applied: bool
    error: str | None


class ActionProposalGroupOutcomeReport(TypedDict):
    status: str
    group_key: str
    outcome: str | None
    group_history: "ActionProposalGroupHistoryReport | None"
    next_action: str
    applied: bool
    error: str | None


class ActionProposalGroupHistoryReport(TypedDict):
    group_key: str
    event_type: str
    recorded_at: str
    status: str
    action_count: int
    action_ids: list[str]
    package_path: str | None
    queued_count: int | None
    dismissed_count: int | None
    outcome: str | None
    reason: str | None
    error: str | None


class OperatorReviewSessionGroupSummary(TypedDict):
    group_key: str
    saved_count: int
    queued_count: int
    dismissed_count: int
    outcome_count: int
    action_count: int
    latest_event_type: str | None
    latest_recorded_at: str | None
    latest_outcome: str | None


class OperatorReviewSessionReport(TypedDict):
    status: str
    generated_at: str
    hours: int
    since: str
    group_history_count: int
    queue_item_count: int
    saved_count: int
    queued_count: int
    dismissed_count: int
    outcome_count: int
    reviewed_count: int
    active_queue_count: int
    pending_promotion_count: int
    route_counts: dict[str, int]
    group_summaries: list[OperatorReviewSessionGroupSummary]
    recent_group_history: list[ActionProposalGroupHistoryReport]
    recent_queue_items: list[PersonalOpsImportQueueItemReport]
    next_action: str
    report_file: dict[str, object]
    applied: bool


class OperatorReviewSessionReportSummary(TypedDict):
    path: str
    name: str
    modified_at: str
    size_bytes: int
    status: str
    generated_at: str | None
    hours: int | None
    group_history_count: int
    queue_item_count: int
    saved_count: int
    queued_count: int
    dismissed_count: int
    outcome_count: int
    reviewed_count: int
    active_queue_count: int
    pending_promotion_count: int
    next_action: str | None


class OperatorReviewSessionReportDetail(TypedDict):
    status: str
    path: str
    name: str
    schema_version: str | None
    generated_at: str | None
    summary: OperatorReviewSessionReportSummary | None
    report: dict[str, object] | None
    applied: bool
    error: str | None


class OperatorReviewSessionRetentionReport(TypedDict):
    status: str
    report_dir: str
    keep: int
    dry_run: bool
    total_count: int
    kept_count: int
    candidate_count: int
    deleted_count: int
    candidate_reports: list[OperatorReviewSessionReportSummary]
    deleted_reports: list[OperatorReviewSessionReportSummary]
    next_action: str
    applied: bool
    error: str | None


class PersonalOpsActionExportReport(TypedDict):
    status: str
    schema_version: str
    generated_at: str
    hours: int
    actions: list[PersonalOpsActionReport]
    dismissed_action_count: int
    dismissals: list[ActionProposalDismissalReport]
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


class PersonalOpsQueueBurnInReportSummary(TypedDict):
    path: str
    name: str
    modified_at: str
    size_bytes: int
    status: str
    generated_at: str | None
    ready_for_live_promotion: bool
    queued_count: int
    pending_count: int
    stale_count: int
    runtime_status: str | None
    noise_candidate_count: int
    next_action: str | None


class PersonalOpsQueueBurnInReportDetail(TypedDict):
    status: str
    path: str
    name: str
    schema_version: str | None
    generated_at: str | None
    summary: PersonalOpsQueueBurnInReportSummary | None
    report: dict[str, object] | None
    applied: bool
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
    policy_drift: "PolicyDriftReport"


class PolicyDriftReport(TypedDict):
    status: str
    live_noise_rule_count: int
    sample_noise_rule_count: int
    missing_sample_noise_rule_count: int
    extra_live_noise_rule_count: int
    missing_sample_noise_rules: list[dict[str, object]]
    extra_live_noise_rules: list[dict[str, object]]
    next_action: str
    error: str | None


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


class CoordinationReadinessReport(TypedDict):
    status: str
    decision: str
    summary: str
    queue_status: str
    queued_count: int
    pending_count: int
    stale_count: int
    saved_burn_in_reports: int
    latest_burn_in_ready: bool | None
    latest_burn_in_noise_candidates: int | None
    runtime_status: str
    policy_warning_count: int
    next_action: str
    evidence: list[str]
    applied: bool


class CoordinationConsoleActionReport(TypedDict):
    action: PersonalOpsActionReport
    lineage_status: str
    lineage_label: str
    lineage_next_action: str
    queue_id: str | None
    queue_status: str | None
    promotion_outcome: str | None
    promotion_target_id: str | None


class CoordinationProposalRouteRecommendation(TypedDict):
    decision: str
    reason: str
    suggested_next_action: str
    promote_candidate_count: int
    suppress_candidate_count: int
    follow_up_candidate_count: int
    promote_candidate_action_ids: list[str]
    suppress_candidate_action_ids: list[str]
    follow_up_candidate_action_ids: list[str]


class CoordinationProposalReviewGroup(TypedDict):
    group_key: str
    source: str
    project: str | None
    intent: str
    priority: str
    state: str
    action_count: int
    total_event_count: int
    newest_evidence_timestamp: str | None
    titles: list[str]
    action_ids: list[str]
    history_count: int
    latest_history: ActionProposalGroupHistoryReport | None
    latest_outcome: str | None
    routing_recommendation: CoordinationProposalRouteRecommendation | None
    next_action: str


class CoordinationProposalReviewReport(TypedDict):
    mode: str
    summary: str
    active_count: int
    new_count: int
    queued_count: int
    promoted_count: int
    reviewed_only_count: int
    snoozed_count: int
    resolved_count: int
    ignored_count: int
    handled_count: int
    group_count: int
    primary_action_id: str | None
    groups: list[CoordinationProposalReviewGroup]
    group_history: list[ActionProposalGroupHistoryReport]
    next_action: str


class CoordinationConsoleGuideStep(TypedDict):
    step: int
    title: str
    status: str
    summary: str
    commands: list[str]
    action_id: str | None
    queue_id: str | None


class CoordinationNextSignalReport(TypedDict):
    status: str
    title: str
    summary: str
    qualifying_intents: list[str]
    hidden_action_count: int
    dismissed_count: int
    policy_covered_repeated_count: int
    policy_covered_signatures: list[RepeatedSignatureReport]
    dismissed_proposals: list[ActionProposalDismissalReport]
    next_action: str


class CoordinationConsoleReport(TypedDict):
    status: str
    readiness: CoordinationReadinessReport
    action_count: int
    active_action_count: int
    handled_action_count: int
    dismissal_count: int
    actions: list[CoordinationConsoleActionReport]
    handled_actions: list[CoordinationConsoleActionReport]
    proposal_review: CoordinationProposalReviewReport
    dismissals: list[ActionProposalDismissalReport]
    next_signal: CoordinationNextSignalReport
    queue_health: PersonalOpsImportQueueHealthReport
    queued_items: list[PersonalOpsImportQueueItemReport]
    pending_promotion_items: list[PersonalOpsImportQueueItemReport]
    outcome_sync_reminder: PersonalOpsOutcomeSyncReminderReport
    burn_in_reports: list[PersonalOpsQueueBurnInReportSummary]
    guide_stage: str
    guide_steps: list[CoordinationConsoleGuideStep]
    next_commands: list[str]
    next_action: str
    applied: bool


class OperatorDailyStateReport(TypedDict):
    status: str
    generated_at: str
    hours: int
    runtime: StatusReport
    queue_health: PersonalOpsImportQueueHealthCheckReport
    coordination_console: CoordinationConsoleReport
    burn_in: BurnInReport
    dismissals: list[ActionProposalDismissalReport]
    next_action: str
    report_file: dict[str, object]
    applied: bool


class OperatorHandoffDrillReport(TypedDict):
    status: str
    generated_at: str
    scenario: PersonalOpsQueueScenarioReport
    queue_burn_in: PersonalOpsQueueBurnInReport
    review_steps: list[str]
    next_action: str
    applied: bool


DEFAULT_BRIDGE_DB_PATH = Path.home() / ".local" / "share" / "bridge-db" / "bridge.db"
BRIDGE_SNAPSHOT_RETENTION_PER_SYSTEM = 10
ACTION_EXPORT_DIR = EVENTS_DIR / "action-exports"
BURN_IN_REPORT_DIR = EVENTS_DIR / "burn-in-reports"
OPERATOR_STATE_REPORT_DIR = EVENTS_DIR / "operator-state-reports"
OPERATOR_REVIEW_SESSION_REPORT_DIR = EVENTS_DIR / "operator-review-session-reports"
PERSONAL_OPS_IMPORT_QUEUE = EVENTS_DIR / "personal-ops-import-queue.jsonl"
ACTION_PROPOSAL_DISMISSALS = EVENTS_DIR / "action-proposal-dismissals.jsonl"
ACTION_PROPOSAL_GROUP_HISTORY = EVENTS_DIR / "action-proposal-group-history.jsonl"
ACTION_EXPORT_SCHEMA_VERSION = "notification-hub.personal_ops_action_export.v1"
PERSONAL_OPS_IMPORT_QUEUE_SCHEMA_VERSION = "notification-hub.personal_ops_import_queue.v1"
ACTION_PROPOSAL_MIN_CANDIDATES = 25
ACTION_PROPOSAL_CANDIDATE_MULTIPLIER = 5
PERSONAL_OPS_QUEUE_STATUSES = {
    "queued",
    "reviewed",
    "rejected",
    "snoozed",
    "superseded",
    "promoted",
}
PERSONAL_OPS_PROMOTION_OUTCOMES = {"pending", "accepted", "rejected", "ignored"}
ACTION_PROPOSAL_GROUP_OUTCOMES = {
    "accepted",
    "rejected",
    "snoozed",
    "superseded",
    "needs_follow_up",
}
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
    return {
        "group_key": group_key,
        "event_type": event_type,
        "recorded_at": recorded_at,
        "status": status,
        "action_count": _as_int(raw.get("action_count")) or len(action_ids),
        "action_ids": action_ids,
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


def _recent_group_history(
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


def _record_action_proposal_group_history(
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
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "action_count": len(actions),
        "action_ids": [action["action_id"] for action in actions],
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


def _active_action_proposal_dismissals(
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
    dismissed_at = datetime.now(timezone.utc).isoformat()
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
    except OSError as exc:
        return {
            "status": "degraded",
            "path": str(path),
            "dismissal": None,
            "applied": False,
            "error": str(exc),
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
    active = _active_action_proposal_dismissals(dismissals_path).get(key)
    if active is None:
        return {
            "status": "degraded",
            "path": str(path),
            "dismissal_key": key,
            "removed": False,
            "applied": False,
            "error": "dismissal not found",
        }
    deleted_at = datetime.now(timezone.utc).isoformat()
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
    except OSError as exc:
        return {
            "status": "degraded",
            "path": str(path),
            "dismissal_key": key,
            "removed": False,
            "applied": False,
            "error": str(exc),
        }
    return {
        "status": "ok",
        "path": str(path),
        "dismissal_key": key,
        "removed": True,
        "applied": False,
        "error": None,
    }


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


def _proposal_dismissal_key(rollup: InboxRollupReport) -> str:
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
        re.sub(r"[^a-z0-9]+", "-", (rollup["project"] or "general").lower()).strip("-") or "general"
    )
    intent_part = re.sub(r"[^a-z0-9]+", "-", rollup["intent"].lower()).strip("-") or "intent"
    return f"proposal:{source_part}:{project_part}:{intent_part}:{digest}"


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
        "dismissal_key": _proposal_dismissal_key(rollup),
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
        "count": rollup["count"],
    }


def _action_proposal_candidate_limit(limit: int) -> int:
    item_limit = max(limit, 1)
    return max(
        ACTION_PROPOSAL_MIN_CANDIDATES,
        item_limit * ACTION_PROPOSAL_CANDIDATE_MULTIPLIER,
    )


def _write_action_review_package(
    report: dict[str, object],
    *,
    output_dir: Path | None = None,
) -> dict[str, object]:
    target_dir = output_dir or ACTION_EXPORT_DIR
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
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
        and re.fullmatch(r"personal-ops-actions-\d{8}-\d{6}(?:-\d{6})?\.json", name)
        is not None
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


def _queue_item_activity_datetime(item: PersonalOpsImportQueueItemReport) -> datetime | None:
    return _parse_iso_datetime(
        item["promotion_outcome_at"]
        or item["promoted_at"]
        or item["updated_at"]
        or item["enqueued_at"]
    )


def _recent_queue_item_reports(
    *,
    since: datetime,
    limit: int,
    queue_path: Path | None = None,
) -> list[PersonalOpsImportQueueItemReport]:
    try:
        raw_items = _read_import_queue_items(queue_path or PERSONAL_OPS_IMPORT_QUEUE)
    except OSError:
        raw_items = []
    reports = [_import_queue_item_report(item) for item in raw_items]
    recent: list[PersonalOpsImportQueueItemReport] = []
    for item in reports:
        activity_at = _queue_item_activity_datetime(item)
        if activity_at is not None and activity_at >= since:
            recent.append(item)
    return sorted(
        recent,
        key=lambda item: _queue_item_activity_datetime(item) or datetime.min.replace(
            tzinfo=timezone.utc
        ),
        reverse=True,
    )[: max(limit, 1)]


def _group_history_route(event_type: str) -> str:
    for route in ("promote", "suppress", "follow_up"):
        if event_type.endswith(f"_{route}"):
            return route
    return "all"


def run_operator_review_session(
    *,
    hours: int = 2,
    limit: int = 25,
    save_report: bool = False,
    report_dir: Path | None = None,
    queue_path: Path | None = None,
    group_history_path: Path | None = None,
) -> OperatorReviewSessionReport:
    """Summarize recent local review activity without applying work."""
    now = datetime.now(timezone.utc)
    bounded_hours = max(hours, 1)
    bounded_limit = max(limit, 1)
    since = now - timedelta(hours=bounded_hours)
    recent_group_history = _recent_group_history(
        since=since,
        limit=bounded_limit,
        history_path=group_history_path,
    )
    recent_queue_items = _recent_queue_item_reports(
        since=since,
        limit=bounded_limit,
        queue_path=queue_path,
    )

    saved_count = sum(1 for item in recent_group_history if item["event_type"].startswith("saved"))
    queued_count = sum(
        1 for item in recent_group_history if item["event_type"].startswith("queued")
    )
    dismissed_count = sum(
        1 for item in recent_group_history if item["event_type"].startswith("dismissed")
    )
    outcome_count = sum(1 for item in recent_group_history if item["event_type"] == "outcome")
    reviewed_count = sum(1 for item in recent_queue_items if item["status"] == "reviewed")
    active_queue_count = sum(1 for item in recent_queue_items if item["status"] == "queued")
    pending_promotion_count = sum(
        1
        for item in recent_queue_items
        if item["status"] == "promoted" and (item["promotion_outcome"] or "pending") == "pending"
    )

    route_counts: dict[str, int] = {}
    grouped: dict[str, OperatorReviewSessionGroupSummary] = {}
    for item in recent_group_history:
        route = _group_history_route(item["event_type"])
        route_counts[route] = route_counts.get(route, 0) + 1
        existing_summary = grouped.get(item["group_key"])
        if existing_summary is None:
            summary: OperatorReviewSessionGroupSummary = {
                "group_key": item["group_key"],
                "saved_count": 0,
                "queued_count": 0,
                "dismissed_count": 0,
                "outcome_count": 0,
                "action_count": 0,
                "latest_event_type": item["event_type"],
                "latest_recorded_at": item["recorded_at"],
                "latest_outcome": item["outcome"],
            }
            grouped[item["group_key"]] = summary
        else:
            summary = existing_summary
        if item["event_type"].startswith("saved"):
            summary["saved_count"] += 1
        elif item["event_type"].startswith("queued"):
            summary["queued_count"] += 1
        elif item["event_type"].startswith("dismissed"):
            summary["dismissed_count"] += 1
        elif item["event_type"] == "outcome":
            summary["outcome_count"] += 1
        summary["action_count"] += item["action_count"]
        latest_recorded_at = summary["latest_recorded_at"]
        if latest_recorded_at is None or item["recorded_at"] > latest_recorded_at:
            summary["latest_event_type"] = item["event_type"]
            summary["latest_recorded_at"] = item["recorded_at"]
            summary["latest_outcome"] = item["outcome"]

    status = "warn" if active_queue_count > 0 or pending_promotion_count > 0 else "ok"
    if active_queue_count > 0:
        next_action = "Review queued personal-ops handoff items from the local review queue."
    elif pending_promotion_count > 0:
        next_action = "Record outcomes for promoted personal-ops handoffs."
    elif recent_group_history or recent_queue_items:
        next_action = "Recent review activity is summarized; monitor /review for the next signal."
    else:
        next_action = "No recent review-session activity found in this window."

    report: OperatorReviewSessionReport = {
        "status": status,
        "generated_at": now.isoformat(),
        "hours": bounded_hours,
        "since": since.isoformat(),
        "group_history_count": len(recent_group_history),
        "queue_item_count": len(recent_queue_items),
        "saved_count": saved_count,
        "queued_count": queued_count,
        "dismissed_count": dismissed_count,
        "outcome_count": outcome_count,
        "reviewed_count": reviewed_count,
        "active_queue_count": active_queue_count,
        "pending_promotion_count": pending_promotion_count,
        "route_counts": route_counts,
        "group_summaries": list(grouped.values()),
        "recent_group_history": recent_group_history,
        "recent_queue_items": recent_queue_items,
        "next_action": next_action,
        "report_file": {
            "requested": save_report,
            "status": "not_requested",
            "path": str(report_dir) if report_dir is not None else None,
            "error": None,
        },
        "applied": False,
    }
    if save_report:
        report_file = write_operator_review_session_report(report, output_dir=report_dir)
        report["report_file"] = report_file
        if report_file["status"] != "ok":
            report["status"] = "warn"
    return report


def write_operator_review_session_report(
    report: OperatorReviewSessionReport,
    *,
    output_dir: Path | None = None,
) -> dict[str, object]:
    target_dir = output_dir or OPERATOR_REVIEW_SESSION_REPORT_DIR
    generated_at = datetime.now(timezone.utc)
    target_path = target_dir / (
        f"operator-review-session-{generated_at.strftime('%Y%m%d-%H%M%S')}.json"
    )
    payload = {
        "schema_version": "notification-hub.operator_review_session.v1",
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


def _is_safe_operator_review_session_report_name(name: str) -> bool:
    return (
        Path(name).name == name
        and re.fullmatch(r"operator-review-session-\d{8}-\d{6}\.json", name) is not None
    )


def _operator_review_session_report_summary(
    *,
    path: Path,
    payload: dict[str, object],
) -> OperatorReviewSessionReportSummary:
    stat = path.stat()
    report = _as_dict(payload.get("report"))
    return {
        "path": str(path),
        "name": path.name,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "size_bytes": stat.st_size,
        "status": _as_str(report.get("status")) or "unknown",
        "generated_at": _as_str(payload.get("generated_at")),
        "hours": _as_int(report.get("hours")),
        "group_history_count": _as_int(report.get("group_history_count")) or 0,
        "queue_item_count": _as_int(report.get("queue_item_count")) or 0,
        "saved_count": _as_int(report.get("saved_count")) or 0,
        "queued_count": _as_int(report.get("queued_count")) or 0,
        "dismissed_count": _as_int(report.get("dismissed_count")) or 0,
        "outcome_count": _as_int(report.get("outcome_count")) or 0,
        "reviewed_count": _as_int(report.get("reviewed_count")) or 0,
        "active_queue_count": _as_int(report.get("active_queue_count")) or 0,
        "pending_promotion_count": _as_int(report.get("pending_promotion_count")) or 0,
        "next_action": _as_str(report.get("next_action")),
    }


def list_operator_review_session_reports(
    *,
    report_dir: Path | None = None,
    limit: int = 10,
) -> list[OperatorReviewSessionReportSummary]:
    """List saved operator review-session reports without applying work."""
    target_dir = report_dir or OPERATOR_REVIEW_SESSION_REPORT_DIR
    try:
        candidates = sorted(
            target_dir.glob("operator-review-session-*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return []

    reports: list[OperatorReviewSessionReportSummary] = []
    for path in candidates[: max(limit, 1)]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                continue
            reports.append(
                _operator_review_session_report_summary(
                    path=path, payload=cast(dict[str, object], payload)
                )
            )
        except (OSError, json.JSONDecodeError):
            continue
    return reports


def load_operator_review_session_report_detail(
    *,
    name: str,
    report_dir: Path | None = None,
) -> OperatorReviewSessionReportDetail:
    """Inspect one saved review-session report without applying work."""
    target_dir = report_dir or OPERATOR_REVIEW_SESSION_REPORT_DIR
    target_path = target_dir / name
    if not _is_safe_operator_review_session_report_name(name):
        return {
            "status": "degraded",
            "path": str(target_path),
            "name": name,
            "schema_version": None,
            "generated_at": None,
            "summary": None,
            "report": None,
            "applied": False,
            "error": "invalid review-session report name",
        }
    try:
        payload = json.loads(target_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("review-session report root must be an object")
        typed_payload = cast(dict[str, object], payload)
        report = _as_dict(typed_payload.get("report"))
        return {
            "status": _as_str(report.get("status")) or "unknown",
            "path": str(target_path),
            "name": name,
            "schema_version": _as_str(typed_payload.get("schema_version")),
            "generated_at": _as_str(typed_payload.get("generated_at")),
            "summary": _operator_review_session_report_summary(
                path=target_path, payload=typed_payload
            ),
            "report": report,
            "applied": False,
            "error": None,
        }
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {
            "status": "degraded",
            "path": str(target_path),
            "name": name,
            "schema_version": None,
            "generated_at": None,
            "summary": None,
            "report": None,
            "applied": False,
            "error": str(exc),
        }


def prune_operator_review_session_reports(
    *,
    report_dir: Path | None = None,
    keep: int = 20,
    dry_run: bool = True,
) -> OperatorReviewSessionRetentionReport:
    """Prune older saved review-session reports after the newest kept set."""
    target_dir = report_dir or OPERATOR_REVIEW_SESSION_REPORT_DIR
    safe_keep = max(keep, 1)
    reports = list_operator_review_session_reports(report_dir=target_dir, limit=10_000)
    candidate_reports = reports[safe_keep:]
    deleted_reports: list[OperatorReviewSessionReportSummary] = []
    error: str | None = None
    status = "ok"

    if not dry_run:
        for report in candidate_reports:
            path = Path(report["path"])
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                status = "degraded"
                error = str(exc)
                break
            deleted_reports.append(report)

    if dry_run:
        next_action = (
            "Run again with --apply to delete older review-session reports."
            if candidate_reports
            else "No review-session reports need pruning."
        )
    elif status == "ok":
        next_action = (
            "Older review-session reports were pruned."
            if deleted_reports
            else "No review-session reports needed pruning."
        )
    else:
        next_action = "Fix the report deletion error, then rerun retention."

    return {
        "status": status,
        "report_dir": str(target_dir),
        "keep": safe_keep,
        "dry_run": dry_run,
        "total_count": len(reports),
        "kept_count": min(len(reports), safe_keep),
        "candidate_count": len(candidate_reports),
        "deleted_count": len(deleted_reports),
        "candidate_reports": candidate_reports,
        "deleted_reports": deleted_reports,
        "next_action": next_action,
        "applied": not dry_run,
        "error": error,
    }


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


def write_operator_daily_state_report(
    report: OperatorDailyStateReport,
    *,
    output_dir: Path | None = None,
) -> dict[str, object]:
    target_dir = output_dir or OPERATOR_STATE_REPORT_DIR
    generated_at = datetime.now(timezone.utc)
    target_path = target_dir / (
        f"operator-daily-state-{generated_at.strftime('%Y%m%d-%H%M%S')}.json"
    )
    payload = {
        "schema_version": "notification-hub.operator_daily_state.v1",
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


def _is_safe_burn_in_report_name(name: str) -> bool:
    return (
        Path(name).name == name
        and re.fullmatch(r"personal-ops-queue-burn-in-\d{8}-\d{6}\.json", name) is not None
    )


def _burn_in_report_summary(
    *,
    path: Path,
    payload: dict[str, object],
) -> PersonalOpsQueueBurnInReportSummary:
    stat = path.stat()
    report = _as_dict(payload.get("report"))
    queue_health = _as_dict(report.get("queue_health"))
    health = _as_dict(queue_health.get("health"))
    runtime_burn_in = _as_dict(report.get("runtime_burn_in"))
    runtime_health = _as_dict(runtime_burn_in.get("health"))
    raw_noise_candidates = runtime_burn_in.get("noise_candidates")
    noise_candidate_count = (
        len(cast(list[object], raw_noise_candidates))
        if isinstance(raw_noise_candidates, list)
        else 0
    )
    return {
        "path": str(path),
        "name": path.name,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "size_bytes": stat.st_size,
        "status": _as_str(report.get("status")) or "unknown",
        "generated_at": _as_str(payload.get("generated_at")),
        "ready_for_live_promotion": bool(report.get("ready_for_live_promotion")),
        "queued_count": _as_int(health.get("queued_count")) or 0,
        "pending_count": _as_int(health.get("promoted_pending_count")) or 0,
        "stale_count": _as_int(health.get("promoted_pending_stale_count")) or 0,
        "runtime_status": _as_str(runtime_health.get("status")),
        "noise_candidate_count": noise_candidate_count,
        "next_action": _as_str(report.get("next_action")),
    }


def list_personal_ops_queue_burn_in_reports(
    *,
    report_dir: Path | None = None,
    limit: int = 10,
) -> list[PersonalOpsQueueBurnInReportSummary]:
    """List saved queue burn-in reports without applying work."""
    target_dir = report_dir or BURN_IN_REPORT_DIR
    try:
        candidates = sorted(
            target_dir.glob("personal-ops-queue-burn-in-*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return []

    reports: list[PersonalOpsQueueBurnInReportSummary] = []
    for path in candidates[: max(limit, 1)]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                continue
            reports.append(
                _burn_in_report_summary(path=path, payload=cast(dict[str, object], payload))
            )
        except (OSError, json.JSONDecodeError):
            continue
    return reports


def load_personal_ops_queue_burn_in_report_detail(
    *,
    name: str,
    report_dir: Path | None = None,
) -> PersonalOpsQueueBurnInReportDetail:
    """Inspect one saved queue burn-in report without applying work."""
    target_dir = report_dir or BURN_IN_REPORT_DIR
    target_path = target_dir / name
    if not _is_safe_burn_in_report_name(name):
        return {
            "status": "degraded",
            "path": str(target_path),
            "name": name,
            "schema_version": None,
            "generated_at": None,
            "summary": None,
            "report": None,
            "applied": False,
            "error": "invalid burn-in report name",
        }
    try:
        payload = json.loads(target_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("burn-in report root must be an object")
        typed_payload = cast(dict[str, object], payload)
        report = _as_dict(typed_payload.get("report"))
        return {
            "status": _as_str(report.get("status")) or "unknown",
            "path": str(target_path),
            "name": name,
            "schema_version": _as_str(typed_payload.get("schema_version")),
            "generated_at": _as_str(typed_payload.get("generated_at")),
            "summary": _burn_in_report_summary(path=target_path, payload=typed_payload),
            "report": report,
            "applied": False,
            "error": None,
        }
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {
            "status": "degraded",
            "path": str(target_path),
            "name": name,
            "schema_version": None,
            "generated_at": None,
            "summary": None,
            "report": None,
            "applied": False,
            "error": str(exc),
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


def _noise_rule_matches_signature(rule: NoiseRule, item: RepeatedSignatureReport) -> bool:
    title_text = item["title"].lower()
    body_text = item["body"].lower()
    combined_text = f"{title_text} {body_text}"
    if rule.source is not None and rule.source != item["source"]:
        return False
    if rule.project is not None and rule.project != item["project"]:
        return False
    if rule.project_prefix is not None:
        if item["project"] is None or not item["project"].startswith(rule.project_prefix):
            return False
    if rule.level is not None and rule.level != item["level"]:
        return False
    if rule.title_contains is not None and rule.title_contains not in title_text:
        return False
    if rule.body_contains is not None and rule.body_contains not in body_text:
        return False
    if rule.text_contains is not None and rule.text_contains not in combined_text:
        return False
    return True


def _filter_known_noise_candidates(
    repeated: list[RepeatedSignatureReport],
) -> list[RepeatedSignatureReport]:
    rules = get_policy_config().noise.rules
    if not rules:
        return repeated
    return [
        item
        for item in repeated
        if not any(_noise_rule_matches_signature(rule, item) for rule in rules)
    ]


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
    noise_candidates = _filter_known_noise_candidates(repeated)[:10]
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
        "repeated_signatures": repeated[:10],
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
    dismissals_path: Path | None = None,
    include_dismissed: bool = False,
) -> PersonalOpsActionExportReport:
    """Prepare personal-ops action proposals without mutating personal-ops."""
    window_hours = max(hours, 1)
    item_limit = max(limit, 1)
    generated_at = datetime.now(timezone.utc).isoformat()
    inbox = run_inbox(
        hours=window_hours,
        limit=_action_proposal_candidate_limit(item_limit),
    )
    candidate_actions: list[PersonalOpsActionReport] = []
    proposed_actions: list[PersonalOpsActionReport] = []
    for rollup in inbox["rollups"]:
        if rollup["intent"] not in ACTION_PROPOSAL_INTENTS:
            continue
        candidate_actions.append(_action_from_rollup(rollup))
        if _rollup_is_policy_covered(rollup):
            continue
        proposed_actions.append(candidate_actions[-1])
    dismissals = _active_action_proposal_dismissals(dismissals_path)
    active_actions = [
        action for action in proposed_actions if action["dismissal_key"] not in dismissals
    ]
    actions = (candidate_actions if include_dismissed else active_actions)[:item_limit]
    dismissed_action_count = len(candidate_actions) - len(active_actions)

    report: PersonalOpsActionExportReport = {
        "status": inbox["status"],
        "schema_version": ACTION_EXPORT_SCHEMA_VERSION,
        "generated_at": generated_at,
        "hours": window_hours,
        "actions": actions,
        "dismissed_action_count": dismissed_action_count,
        "dismissals": list(dismissals.values())[:item_limit],
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


def _actions_for_group_key(
    *,
    group_key: str,
    hours: int,
    limit: int,
    dismissals_path: Path | None = None,
) -> tuple[PersonalOpsActionExportReport, list[PersonalOpsActionReport]]:
    export = run_personal_ops_action_export(
        hours=hours,
        limit=max(limit, 1),
        dismissals_path=dismissals_path,
    )
    actions = [
        action
        for action in export["actions"]
        if _action_group_label(_action_group_key(action)) == group_key
    ]
    return export, actions


def save_action_proposal_group_package(
    *,
    group_key: str,
    route: str | None = None,
    hours: int = 2,
    limit: int = 25,
    enqueue: bool = False,
    review_dir: Path | None = None,
    queue_path: Path | None = None,
    group_history_path: Path | None = None,
) -> ActionProposalGroupPackageReport:
    """Stage one active proposal group as a review package without applying personal-ops work."""
    safe_group_key = group_key.strip()
    if not safe_group_key:
        return {
            "status": "degraded",
            "group_key": group_key,
            "action_count": 0,
            "review_package": {
                "requested": True,
                "status": "degraded",
                "path": str(review_dir) if review_dir is not None else None,
                "error": "group_key is required",
            },
            "import_result": None,
            "group_history": None,
            "next_action": "Choose a valid proposal group before saving a package.",
            "applied": False,
            "error": "group_key is required",
        }
    route_key, route_error = _normalize_action_proposal_group_route(route)
    if route_error is not None:
        return {
            "status": "degraded",
            "group_key": safe_group_key,
            "action_count": 0,
            "review_package": {
                "requested": True,
                "status": "degraded",
                "path": str(review_dir) if review_dir is not None else None,
                "error": route_error,
            },
            "import_result": None,
            "group_history": None,
            "next_action": "Choose one of: all, promote, suppress, or follow_up.",
            "applied": False,
            "error": route_error,
        }
    export, actions = _actions_for_group_key(
        group_key=safe_group_key,
        hours=max(hours, 1),
        limit=max(limit, 1),
    )
    actions = _filter_actions_for_group_route(actions, route_key)
    if not actions:
        route_label = f" for route {route_key}" if route_key is not None else ""
        return {
            "status": "degraded",
            "group_key": safe_group_key,
            "action_count": 0,
            "review_package": {
                "requested": True,
                "status": "degraded",
                "path": str(review_dir) if review_dir is not None else None,
                "error": f"no active proposals matched this group{route_label}",
            },
            "import_result": None,
            "group_history": None,
            "next_action": "Refresh Proposal Review; this group or route is no longer active.",
            "applied": False,
            "error": f"no active proposals matched this group{route_label}",
        }
    payload = dict(export)
    payload["actions"] = actions
    payload["selected_group"] = {
        "group_key": safe_group_key,
        "route": route_key or "all",
        "action_count": len(actions),
        "enqueue_requested": enqueue,
    }
    payload["review_package"] = {
        "requested": True,
        "status": "pending_write",
        "path": str(review_dir) if review_dir is not None else None,
        "error": None,
    }
    review_package = _write_action_review_package(payload, output_dir=review_dir)
    status = "ok" if review_package["status"] == "ok" else "degraded"
    error = str(review_package["error"]) if review_package["error"] is not None else None
    import_result: dict[str, object] | None = None
    next_action = "Inspect the saved group package before queueing it."
    event_type = "saved"
    queued_count: int | None = None
    if status == "ok" and enqueue:
        path_value = review_package["path"]
        if isinstance(path_value, str):
            import_result = dict(
                run_personal_ops_import_stub(
                    path=Path(path_value),
                    enqueue=True,
                    queue_path=queue_path,
                )
            )
            status_value = import_result.get("status")
            if isinstance(status_value, str):
                status = status_value
            error_value = import_result.get("error")
            error = error_value if isinstance(error_value, str) else error
            next_action = str(import_result.get("next_action") or "Review the queued group handoff.")
            queued_count = _as_int(import_result.get("queued_count"))
            event_type = "queued"
        else:
            status = "degraded"
            error = "review package path missing"
            next_action = "Save the group package again before queueing it."
            event_type = "queue_failed"
    event_type = _route_event_type(event_type, route_key)
    group_history: ActionProposalGroupHistoryReport | None = None
    try:
        group_history = _record_action_proposal_group_history(
            group_key=safe_group_key,
            event_type=event_type if status == "ok" else f"{event_type}_failed",
            status=status,
            actions=actions,
            package_path=review_package["path"] if isinstance(review_package["path"], str) else None,
            queued_count=queued_count,
            error=error,
            history_path=group_history_path,
        )
    except OSError as exc:
        status = "degraded"
        error = str(exc)
        next_action = "Fix group history write errors before using group lifecycle controls."
    return {
        "status": status,
        "group_key": safe_group_key,
        "action_count": len(actions),
        "review_package": review_package,
        "import_result": import_result,
        "group_history": group_history,
        "next_action": next_action,
        "applied": False,
        "error": error,
    }


def dismiss_action_proposal_group(
    *,
    group_key: str,
    reason: str,
    route: str | None = None,
    hours: int = 2,
    limit: int = 25,
    dismissals_path: Path | None = None,
    group_history_path: Path | None = None,
) -> ActionProposalGroupDismissReport:
    """Dismiss every currently active proposal in one proposal-review group."""
    safe_group_key = group_key.strip()
    if not safe_group_key:
        return {
            "status": "degraded",
            "group_key": group_key,
            "dismissed_count": 0,
            "dismissals": [],
            "group_history": None,
            "next_action": "Choose a valid proposal group before dismissing it.",
            "applied": False,
            "error": "group_key is required",
        }
    route_key, route_error = _normalize_action_proposal_group_route(route)
    if route_error is not None:
        return {
            "status": "degraded",
            "group_key": safe_group_key,
            "dismissed_count": 0,
            "dismissals": [],
            "group_history": None,
            "next_action": "Choose one of: all, promote, suppress, or follow_up.",
            "applied": False,
            "error": route_error,
        }
    _, actions = _actions_for_group_key(
        group_key=safe_group_key,
        hours=max(hours, 1),
        limit=max(limit, 1),
        dismissals_path=dismissals_path,
    )
    actions = _filter_actions_for_group_route(actions, route_key)
    if not actions:
        route_label = f" for route {route_key}" if route_key is not None else ""
        return {
            "status": "degraded",
            "group_key": safe_group_key,
            "dismissed_count": 0,
            "dismissals": [],
            "group_history": None,
            "next_action": "Refresh Proposal Review; this group or route is no longer active.",
            "applied": False,
            "error": f"no active proposals matched this group{route_label}",
        }
    dismissals: list[ActionProposalDismissalReport] = []
    error: str | None = None
    for action in actions:
        report = dismiss_action_proposal(
            dismissal_key=action["dismissal_key"],
            reason=reason,
            source=action["source"],
            project=action["project"],
            intent=action["intent"],
            title=action["title"],
            body=action["signal_body"],
            evidence_event_id=action["evidence_event_id"],
            dismissals_path=dismissals_path,
        )
        if report["dismissal"] is not None:
            dismissals.append(report["dismissal"])
        if report["error"] is not None:
            error = report["error"]
    status = "ok" if error is None else "degraded"
    group_history: ActionProposalGroupHistoryReport | None = None
    try:
        group_history = _record_action_proposal_group_history(
            group_key=safe_group_key,
            event_type=_route_event_type("dismissed", route_key)
            if status == "ok"
            else _route_event_type("dismiss_failed", route_key),
            status=status,
            actions=actions,
            dismissed_count=len(dismissals),
            reason=reason,
            error=error,
            history_path=group_history_path,
        )
    except OSError as exc:
        status = "degraded"
        error = str(exc)
    return {
        "status": status,
        "group_key": safe_group_key,
        "dismissed_count": len(dismissals),
        "dismissals": dismissals,
        "group_history": group_history,
        "next_action": "Group dismissed from the local console. Undismiss individual proposals if they should return.",
        "applied": False,
        "error": error,
    }


def record_action_proposal_group_outcome(
    *,
    group_key: str,
    outcome: str,
    reason: str,
    hours: int = 2,
    limit: int = 25,
    group_history_path: Path | None = None,
) -> ActionProposalGroupOutcomeReport:
    """Record an operator-visible outcome for one proposal-review group."""
    safe_group_key = group_key.strip()
    safe_outcome = outcome.strip()
    if not safe_group_key:
        return {
            "status": "degraded",
            "group_key": group_key,
            "outcome": None,
            "group_history": None,
            "next_action": "Choose a valid proposal group before recording an outcome.",
            "applied": False,
            "error": "group_key is required",
        }
    if safe_outcome not in ACTION_PROPOSAL_GROUP_OUTCOMES:
        return {
            "status": "degraded",
            "group_key": safe_group_key,
            "outcome": safe_outcome,
            "group_history": None,
            "next_action": "Choose a valid group outcome before recording it.",
            "applied": False,
            "error": f"invalid group outcome: {safe_outcome}",
        }

    _, actions = _actions_for_group_key(
        group_key=safe_group_key,
        hours=max(hours, 1),
        limit=max(limit, 1),
    )
    if not actions:
        return {
            "status": "degraded",
            "group_key": safe_group_key,
            "outcome": safe_outcome,
            "group_history": None,
            "next_action": "Refresh Proposal Review; this group is no longer active.",
            "applied": False,
            "error": "no active proposals matched this group",
        }

    try:
        group_history = _record_action_proposal_group_history(
            group_key=safe_group_key,
            event_type="outcome",
            status="ok",
            actions=actions,
            outcome=safe_outcome,
            reason=reason.strip() or "operator recorded group outcome",
            history_path=group_history_path,
        )
    except OSError as exc:
        return {
            "status": "degraded",
            "group_key": safe_group_key,
            "outcome": safe_outcome,
            "group_history": None,
            "next_action": "Fix group history write errors before recording group outcomes.",
            "applied": False,
            "error": str(exc),
        }

    return {
        "status": "ok",
        "group_key": safe_group_key,
        "outcome": safe_outcome,
        "group_history": group_history,
        "next_action": "Group outcome recorded locally. Use queue or dismissal controls for any follow-up handoff state.",
        "applied": False,
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


NOISE_RULE_DRIFT_FIELDS = (
    "source",
    "project",
    "project_prefix",
    "title_contains",
    "body_contains",
    "text_contains",
    "level",
    "window_minutes",
)


def _noise_rule_signature(rule: NoiseRule) -> tuple[object, ...]:
    return tuple(getattr(rule, field_name) for field_name in NOISE_RULE_DRIFT_FIELDS)


def _noise_rule_summary(rule: NoiseRule) -> dict[str, object]:
    summary: dict[str, object] = {}
    for field_name in NOISE_RULE_DRIFT_FIELDS:
        value = getattr(rule, field_name)
        if value is not None:
            summary[field_name] = value
    return summary


def _build_policy_drift_report(policy: object) -> PolicyDriftReport:
    if not isinstance(policy, type(get_policy_config())):
        policy = get_policy_config()
    live_policy = cast(type(get_policy_config()), policy)
    sample_policy = load_policy_config_file(EXAMPLE_POLICY_CONFIG)
    if not sample_policy.config_found:
        return {
            "status": "degraded",
            "live_noise_rule_count": len(live_policy.noise.rules),
            "sample_noise_rule_count": 0,
            "missing_sample_noise_rule_count": 0,
            "extra_live_noise_rule_count": 0,
            "missing_sample_noise_rules": [],
            "extra_live_noise_rules": [],
            "next_action": "Restore the repo sample policy config before checking live policy drift.",
            "error": "sample policy config not found",
        }
    if sample_policy.load_error is not None:
        return {
            "status": "degraded",
            "live_noise_rule_count": len(live_policy.noise.rules),
            "sample_noise_rule_count": 0,
            "missing_sample_noise_rule_count": 0,
            "extra_live_noise_rule_count": 0,
            "missing_sample_noise_rules": [],
            "extra_live_noise_rules": [],
            "next_action": "Fix the repo sample policy config before checking live policy drift.",
            "error": sample_policy.load_error,
        }

    live_signatures = {_noise_rule_signature(rule) for rule in live_policy.noise.rules}
    sample_signatures = {_noise_rule_signature(rule) for rule in sample_policy.noise.rules}
    missing_sample_rules = [
        rule for rule in sample_policy.noise.rules if _noise_rule_signature(rule) not in live_signatures
    ]
    extra_live_rules = [
        rule for rule in live_policy.noise.rules if _noise_rule_signature(rule) not in sample_signatures
    ]

    missing_count = len(missing_sample_rules)
    status = "warn" if missing_count else "ok"
    next_action = (
        "Add the missing sample noise rules to the live policy config or update the sample deliberately."
        if missing_count
        else "Live policy includes every sample noise rule."
    )
    return {
        "status": status,
        "live_noise_rule_count": len(live_policy.noise.rules),
        "sample_noise_rule_count": len(sample_policy.noise.rules),
        "missing_sample_noise_rule_count": missing_count,
        "extra_live_noise_rule_count": len(extra_live_rules),
        "missing_sample_noise_rules": [_noise_rule_summary(rule) for rule in missing_sample_rules],
        "extra_live_noise_rules": [_noise_rule_summary(rule) for rule in extra_live_rules],
        "next_action": next_action,
        "error": None,
    }


def run_policy_check() -> PolicyCheckReport:
    """Analyze the current policy config for overlapping or ineffective rules."""
    policy = get_policy_config()
    warnings = list(analyze_policy_config(policy))
    suggestions = [_suggest_fix_for_warning(warning) for warning in warnings]
    load_error = policy.load_error
    policy_drift = _build_policy_drift_report(policy)

    if load_error is not None or policy_drift["status"] == "degraded":
        status = "degraded"
    elif warnings or policy_drift["status"] == "warn":
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
        "policy_drift": policy_drift,
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


def run_coordination_readiness(*, limit: int = 5) -> CoordinationReadinessReport:
    """Summarize whether the operator loop is ready for broader coordination work."""
    runtime_status = run_status()
    queue = runtime_status["import_queue"]
    reports = list_personal_ops_queue_burn_in_reports(limit=max(limit, 1))
    latest_report = reports[0] if reports else None
    queued_count = queue["queued_count"]
    pending_count = queue["promoted_pending_count"]
    stale_count = queue["promoted_pending_stale_count"]
    latest_ready = latest_report["ready_for_live_promotion"] if latest_report is not None else None
    latest_noise = latest_report["noise_candidate_count"] if latest_report is not None else None

    evidence = [
        f"runtime={runtime_status['status']}",
        f"queue={queue['status']} queued={queued_count} pending={pending_count} stale={stale_count}",
        f"saved_burn_in_reports={len(reports)}",
    ]
    if latest_report is not None:
        evidence.append(
            "latest_burn_in="
            f"{latest_report['status']} ready={latest_report['ready_for_live_promotion']} "
            f"noise={latest_report['noise_candidate_count']}"
        )

    if runtime_status["status"] != "ok" or runtime_status["policy_warning_count"] > 0:
        decision = "fix_noise_first"
        status = "warn"
        summary = "Runtime or policy needs attention before expanding coordination."
        next_action = runtime_status["next_action"]
    elif queued_count > 0 or pending_count > 0 or stale_count > 0:
        decision = "keep_burning_in"
        status = "warn" if stale_count > 0 else "ok"
        summary = "Operator queue has active handoff state; finish the loop before expanding."
        next_action = queue["next_action"]
    elif latest_report is None:
        decision = "keep_burning_in"
        status = "ok"
        summary = "Runtime is healthy, but there is not enough saved burn-in evidence yet."
        next_action = "Run personal-ops-queue-burn-in --save-report around real operator use."
    elif latest_report["noise_candidate_count"] > 0:
        decision = "fix_noise_first"
        status = "warn"
        summary = "Saved burn-in evidence still has repeated noise candidates."
        next_action = "Review the latest burn-in report noise candidates before expanding."
    elif queue["promoted_count"] < 3:
        decision = "keep_burning_in"
        status = "ok"
        summary = "The loop is healthy, but real promotion volume is still low."
        next_action = "Keep collecting burn-in reports around real handoff promotions."
    else:
        decision = "ready_to_expand"
        status = "ok"
        summary = "Runtime, queue, and saved burn-in evidence are ready for a small coordination expansion."
        next_action = "Plan the next compact coordination console slice."

    return {
        "status": status,
        "decision": decision,
        "summary": summary,
        "queue_status": queue["status"],
        "queued_count": queued_count,
        "pending_count": pending_count,
        "stale_count": stale_count,
        "saved_burn_in_reports": len(reports),
        "latest_burn_in_ready": latest_ready,
        "latest_burn_in_noise_candidates": latest_noise,
        "runtime_status": runtime_status["status"],
        "policy_warning_count": runtime_status["policy_warning_count"],
        "next_action": next_action,
        "evidence": evidence,
        "applied": False,
    }


ACTION_PROPOSAL_INTENTS = {
    "needs_attention",
    "blocked",
    "waiting_on_user",
    "ready_to_review",
    "ready_to_merge",
    "automation_failed",
}


def _rollup_repeated_signature(rollup: InboxRollupReport) -> RepeatedSignatureReport:
    return {
        "count": rollup["count"],
        "source": rollup["source"],
        "project": rollup["project"],
        "level": rollup["level"],
        "title": rollup["title"],
        "body": rollup["body"],
    }


def _policy_covered_rollups(rollups: list[InboxRollupReport]) -> list[RepeatedSignatureReport]:
    rules = get_policy_config().noise.rules
    if not rules:
        return []
    signatures: list[RepeatedSignatureReport] = []
    for rollup in rollups:
        signature = _rollup_repeated_signature(rollup)
        if any(_noise_rule_matches_signature(rule, signature) for rule in rules):
            signatures.append(signature)
    return signatures


def _rollup_is_policy_covered(rollup: InboxRollupReport) -> bool:
    return bool(_policy_covered_rollups([rollup]))


def _build_next_signal_report(
    *,
    readiness: CoordinationReadinessReport,
    actions: PersonalOpsActionExportReport,
    active_actions: list[CoordinationConsoleActionReport],
    queue_health: PersonalOpsImportQueueHealthReport,
    dismissals: list[ActionProposalDismissalReport],
    limit: int,
) -> CoordinationNextSignalReport:
    raw_inbox = cast(dict[str, object], actions.get("inbox", {}))
    raw_rollups = raw_inbox.get("rollups")
    rollups = cast(list[InboxRollupReport], raw_rollups) if isinstance(raw_rollups, list) else []
    policy_covered = _policy_covered_rollups(rollups)[:limit]
    if active_actions:
        title = "Active proposal waiting"
        summary = "The next real signal is already visible as an action proposal."
        status = "ready"
        next_action = (
            "Save and validate a review package, then queue one handoff for operator review."
        )
    elif queue_health["queued_count"] > 0 or queue_health["promoted_pending_count"] > 0:
        title = "Queue lifecycle in progress"
        summary = "The next real signal is already in the queue lifecycle."
        status = "busy"
        next_action = queue_health["next_action"]
    elif readiness["decision"] != "ready_to_expand":
        title = "Readiness gate first"
        summary = readiness["summary"]
        status = "blocked"
        next_action = readiness["next_action"]
    else:
        title = "Waiting for next real signal"
        summary = (
            "A signal qualifies when repeated recent events have a coordination intent "
            "and are not already dismissed or covered by policy noise rules."
        )
        status = "monitor"
        next_action = (
            "Monitor /review; use dismissal management if a hidden proposal should return."
        )
    return {
        "status": status,
        "title": title,
        "summary": summary,
        "qualifying_intents": sorted(ACTION_PROPOSAL_INTENTS),
        "hidden_action_count": int(actions.get("dismissed_action_count", 0)),
        "dismissed_count": len(dismissals),
        "policy_covered_repeated_count": len(policy_covered),
        "policy_covered_signatures": policy_covered,
        "dismissed_proposals": dismissals[:limit],
        "next_action": next_action,
    }


def _proposal_lineage_status(queue_item: PersonalOpsImportQueueItemReport | None) -> str:
    if queue_item is None:
        return "new"
    if queue_item["status"] == "queued":
        return "queued"
    if queue_item["status"] in {"reviewed", "snoozed"}:
        return queue_item["status"]
    if queue_item["status"] == "promoted":
        outcome = queue_item["promotion_outcome"] or "pending"
        return "promoted" if outcome == "pending" else "resolved"
    return "ignored"


def _proposal_lineage_label(status: str) -> str:
    return {
        "new": "New",
        "queued": "Queued",
        "reviewed": "Reviewed only",
        "snoozed": "Snoozed",
        "promoted": "Pending outcome",
        "resolved": "Resolved",
        "ignored": "Closed",
    }.get(status, status.replace("_", " ").title())


def _proposal_lineage_next_action(status: str) -> str:
    return {
        "new": "Save and validate a review package if the evidence is right.",
        "queued": "Review the queued handoff before any downstream promotion.",
        "reviewed": "Evidence was reviewed and no downstream promotion is required.",
        "snoozed": "Wait until the snooze expires or reactivate the handoff manually.",
        "promoted": "Record the downstream personal-ops outcome.",
        "resolved": "Downstream outcome is recorded; no queue action is needed.",
        "ignored": "This handoff is closed without downstream personal-ops work.",
    }.get(status, "Review this handoff before taking further action.")


def _build_proposal_lineage(
    actions: list[PersonalOpsActionReport],
) -> list[CoordinationConsoleActionReport]:
    raw_items = _read_import_queue_items(PERSONAL_OPS_IMPORT_QUEUE)
    queue_by_action_id: dict[str, PersonalOpsImportQueueItemReport] = {}
    queue_by_evidence_id: dict[str, PersonalOpsImportQueueItemReport] = {}
    for raw_item in reversed(raw_items):
        item = _import_queue_item_report(raw_item)
        if item["action_id"] and item["action_id"] not in queue_by_action_id:
            queue_by_action_id[item["action_id"]] = item
        if item["evidence_event_id"] and item["evidence_event_id"] not in queue_by_evidence_id:
            queue_by_evidence_id[item["evidence_event_id"]] = item

    reports: list[CoordinationConsoleActionReport] = []
    for action in actions:
        queue_item = queue_by_action_id.get(action["action_id"]) or queue_by_evidence_id.get(
            action["evidence_event_id"]
        )
        lineage_status = _proposal_lineage_status(queue_item)
        reports.append(
            {
                "action": action,
                "lineage_status": lineage_status,
                "lineage_label": _proposal_lineage_label(lineage_status),
                "lineage_next_action": _proposal_lineage_next_action(lineage_status),
                "queue_id": queue_item["queue_id"] if queue_item is not None else None,
                "queue_status": queue_item["status"] if queue_item is not None else None,
                "promotion_outcome": queue_item["promotion_outcome"]
                if queue_item is not None
                else None,
                "promotion_target_id": queue_item["promotion_target_id"]
                if queue_item is not None
                else None,
            }
        )
    return reports


def _action_group_key(action: PersonalOpsActionReport) -> tuple[str, str | None, str, str, str]:
    return (
        action["source"],
        action["project"],
        action["intent"],
        action["priority"],
        action["state"],
    )


def _action_group_label(key: tuple[str, str | None, str, str, str]) -> str:
    source, project, intent, priority, state = key
    return f"{source}:{project or 'none'}:{intent}:{priority}:{state}"


MAIL_PROMOTION_CUES = (
    "initial reply needed",
    "send this reply",
    "outbound workflow reply",
)
MAIL_SUPPRESSION_CUES = (
    "phase ",
    "prepared handoff",
    "review and approval flow",
    "secondary approval",
    "test draft",
)
ACTION_PROPOSAL_GROUP_ROUTES = {"promote", "suppress", "follow_up"}


def _action_signal_body(action: PersonalOpsActionReport) -> str:
    raw = cast(dict[str, object], action).get("signal_body")
    return raw if isinstance(raw, str) else ""


def _mail_route_category(action: PersonalOpsActionReport) -> str:
    body = _action_signal_body(action).lower()
    if any(cue in body for cue in MAIL_PROMOTION_CUES):
        return "promote"
    if any(cue in body for cue in MAIL_SUPPRESSION_CUES):
        return "suppress"
    return "follow_up"


def _normalize_action_proposal_group_route(route: str | None) -> tuple[str | None, str | None]:
    if route is None:
        return None, None
    normalized = route.strip().lower().replace("-", "_")
    if normalized in {"", "all"}:
        return None, None
    normalized = normalized.removesuffix("_candidate")
    if normalized in ACTION_PROPOSAL_GROUP_ROUTES:
        return normalized, None
    return None, f"invalid group route: {route}"


def _filter_actions_for_group_route(
    actions: list[PersonalOpsActionReport],
    route: str | None,
) -> list[PersonalOpsActionReport]:
    if route is None:
        return actions
    return [action for action in actions if _mail_route_category(action) == route]


def _route_event_type(base_event_type: str, route: str | None) -> str:
    return f"{base_event_type}_{route}" if route is not None else base_event_type


def _mail_route_recommendation(
    actions: list[CoordinationConsoleActionReport],
) -> CoordinationProposalRouteRecommendation:
    promote_action_ids: list[str] = []
    suppress_action_ids: list[str] = []
    follow_up_action_ids: list[str] = []
    for item in actions:
        action = item["action"]
        category = _mail_route_category(action)
        if category == "promote":
            promote_action_ids.append(action["action_id"])
        elif category == "suppress":
            suppress_action_ids.append(action["action_id"])
        else:
            follow_up_action_ids.append(action["action_id"])

    promote_count = len(promote_action_ids)
    suppress_count = len(suppress_action_ids)
    follow_up_count = len(follow_up_action_ids)

    def recommendation(
        *,
        decision: str,
        reason: str,
        suggested_next_action: str,
    ) -> CoordinationProposalRouteRecommendation:
        return {
            "decision": decision,
            "reason": reason,
            "suggested_next_action": suggested_next_action,
            "promote_candidate_count": promote_count,
            "suppress_candidate_count": suppress_count,
            "follow_up_candidate_count": follow_up_count,
            "promote_candidate_action_ids": promote_action_ids,
            "suppress_candidate_action_ids": suppress_action_ids,
            "follow_up_candidate_action_ids": follow_up_action_ids,
        }

    if promote_count and suppress_count:
        return recommendation(
            decision="split_mail_batch",
            reason="This group mixes concrete reply approvals with phase or workflow chatter.",
            suggested_next_action=(
                "Save or queue only the concrete reply route after inspection, and dismiss only the "
                "phase/workflow route if it is already covered."
            ),
        )
    if promote_count:
        return recommendation(
            decision="promote_candidate",
            reason="This mail group looks like concrete reply approval work.",
            suggested_next_action=(
                "Save and inspect the package, then promote one reviewed handoff through personal-ops "
                "only if it maps to a real outbound decision."
            ),
        )
    if suppress_count == len(actions):
        return recommendation(
            decision="dismiss_or_suppress",
            reason="This mail group looks like repeated phase or workflow chatter.",
            suggested_next_action=(
                "Dismiss the group locally if it is already handled, or add a policy rule in a separate "
                "policy pass if this pattern should stay hidden."
            ),
        )
    return recommendation(
        decision="review_mail_batch",
        reason="This mail group needs operator inspection before choosing a route.",
        suggested_next_action=(
            "Save the group package, inspect evidence, then choose follow-up, dismissal, or promotion."
        ),
    )


def _group_route_recommendation(
    *,
    key: tuple[str, str | None, str, str, str],
    actions: list[CoordinationConsoleActionReport],
) -> CoordinationProposalRouteRecommendation | None:
    source, project, intent, _, _ = key
    if source == "personal-ops" and project == "mail" and intent == "waiting_on_user":
        return _mail_route_recommendation(actions)
    return None


def _build_proposal_review_group(
    *,
    key: tuple[str, str | None, str, str, str],
    actions: list[CoordinationConsoleActionReport],
    history_by_group: dict[str, list[ActionProposalGroupHistoryReport]],
) -> CoordinationProposalReviewGroup:
    source, project, intent, priority, state = key
    titles: list[str] = []
    for item in actions:
        title = item["action"]["title"]
        if title not in titles:
            titles.append(title)
    newest = max((item["action"]["evidence_timestamp"] for item in actions), default=None)
    total_count = sum(item["action"]["count"] for item in actions)
    group_label = _action_group_label(key)
    group_history = history_by_group.get(group_label, [])
    latest_outcome = next((item["outcome"] for item in group_history if item["outcome"]), None)
    next_action = (
        "Review this group as one package when the evidence points to the same operator decision."
        if len(actions) > 1
        else "Review this proposal on its own before queueing or dismissing it."
    )
    group: CoordinationProposalReviewGroup = {
        "group_key": group_label,
        "source": source,
        "project": project,
        "intent": intent,
        "priority": priority,
        "state": state,
        "action_count": len(actions),
        "total_event_count": total_count,
        "newest_evidence_timestamp": newest,
        "titles": titles[:5],
        "action_ids": [item["action"]["action_id"] for item in actions],
        "history_count": len(group_history),
        "latest_history": group_history[0] if group_history else None,
        "latest_outcome": latest_outcome,
        "routing_recommendation": None,
        "next_action": next_action,
    }
    routing_recommendation = _group_route_recommendation(key=key, actions=actions)
    if routing_recommendation is not None:
        group["routing_recommendation"] = routing_recommendation
        group["next_action"] = routing_recommendation["suggested_next_action"]
    return group


def _build_proposal_review(
    *,
    active_actions: list[CoordinationConsoleActionReport],
    handled_actions: list[CoordinationConsoleActionReport],
    group_history: list[ActionProposalGroupHistoryReport],
) -> CoordinationProposalReviewReport:
    new_count = sum(1 for item in active_actions if item["lineage_status"] == "new")
    queued_count = sum(1 for item in active_actions if item["lineage_status"] == "queued")
    promoted_count = sum(1 for item in active_actions if item["lineage_status"] == "promoted")
    reviewed_only_count = sum(
        1 for item in handled_actions if item["lineage_status"] == "reviewed"
    )
    snoozed_count = sum(1 for item in handled_actions if item["lineage_status"] == "snoozed")
    resolved_count = sum(1 for item in handled_actions if item["lineage_status"] == "resolved")
    ignored_count = sum(1 for item in handled_actions if item["lineage_status"] == "ignored")
    grouped: dict[tuple[str, str | None, str, str, str], list[CoordinationConsoleActionReport]] = {}
    for item in active_actions:
        grouped.setdefault(_action_group_key(item["action"]), []).append(item)
    history_by_group: dict[str, list[ActionProposalGroupHistoryReport]] = {}
    for item in group_history:
        history_by_group.setdefault(item["group_key"], []).append(item)
    groups = [
        _build_proposal_review_group(
            key=key,
            actions=items,
            history_by_group=history_by_group,
        )
        for key, items in sorted(
            grouped.items(),
            key=lambda pair: (
                -len(pair[1]),
                pair[0][0],
                pair[0][1] or "",
                pair[0][2],
                pair[0][3],
                pair[0][4],
            ),
        )
    ]
    primary_action_id = active_actions[0]["action"]["action_id"] if active_actions else None
    if new_count > 1:
        mode = "batch_review"
        summary = f"{new_count} new proposal(s) can be reviewed as grouped operator work."
        next_action = "Save one review package, inspect grouped evidence, then queue only the right handoff(s)."
    elif new_count == 1:
        mode = "single_review"
        summary = "One new proposal is ready for operator review."
        next_action = "Save and validate a review package, then queue the handoff if the evidence is right."
    elif queued_count or promoted_count:
        mode = "lifecycle"
        summary = "A proposal is already in the queue or promotion lifecycle."
        next_action = "Finish the queued or promoted handoff before staging more work."
    else:
        mode = "monitor"
        if handled_actions:
            details: list[str] = []
            if reviewed_only_count:
                details.append(f"{reviewed_only_count} reviewed-only")
            if resolved_count:
                details.append(f"{resolved_count} resolved")
            if ignored_count:
                details.append(f"{ignored_count} closed")
            if snoozed_count:
                details.append(f"{snoozed_count} snoozed")
            suffix = f" ({', '.join(details)})" if details else ""
            summary = f"{len(handled_actions)} handled proposal(s) are visible as history{suffix}."
        else:
            summary = "No active proposals are waiting."
        next_action = "Monitor for the next real proposal."
    return {
        "mode": mode,
        "summary": summary,
        "active_count": len(active_actions),
        "new_count": new_count,
        "queued_count": queued_count,
        "promoted_count": promoted_count,
        "reviewed_only_count": reviewed_only_count,
        "snoozed_count": snoozed_count,
        "resolved_count": resolved_count,
        "ignored_count": ignored_count,
        "handled_count": len(handled_actions),
        "group_count": len(groups),
        "primary_action_id": primary_action_id,
        "groups": groups[:5],
        "group_history": group_history[:5],
        "next_action": next_action,
    }


def _console_guide_step(
    *,
    step: int,
    title: str,
    status: str,
    summary: str,
    commands: list[str] | None = None,
    action_id: str | None = None,
    queue_id: str | None = None,
) -> CoordinationConsoleGuideStep:
    return {
        "step": step,
        "title": title,
        "status": status,
        "summary": summary,
        "commands": commands or [],
        "action_id": action_id,
        "queue_id": queue_id,
    }


def _queue_outcome_commands(item: PersonalOpsImportQueueItemReport | None) -> list[str]:
    queue_id = item["queue_id"] if item is not None else "QUEUE_ID"
    target_id = (
        item["promotion_target_id"]
        if item is not None and item["promotion_target_id"]
        else "SUGGESTION_ID"
    )
    return [
        f'personal-ops suggestion accept|reject {target_id} --note "..."',
        "uv run notification-hub personal-ops-queue "
        f"--queue-id {queue_id} --status promoted --promotion-target-id {target_id} "
        '--promotion-outcome accepted|rejected --promotion-outcome-note "..."',
    ]


def _build_coordination_console_guide(
    *,
    readiness: CoordinationReadinessReport,
    active_actions: list[CoordinationConsoleActionReport],
    handled_actions: list[CoordinationConsoleActionReport],
    queue_health: PersonalOpsImportQueueHealthReport,
    queued_items: list[PersonalOpsImportQueueItemReport],
    pending_promotion_items: list[PersonalOpsImportQueueItemReport],
) -> tuple[str, list[CoordinationConsoleGuideStep]]:
    first_new_action = next(
        (item for item in active_actions if item["lineage_status"] == "new"), None
    )
    first_queued = queued_items[0] if queued_items else None
    first_pending = pending_promotion_items[0] if pending_promotion_items else None

    if queue_health["promoted_pending_count"] > 0:
        return (
            "outcome_sync",
            [
                _console_guide_step(
                    step=1,
                    title="Resolve promoted outcome",
                    status="current",
                    summary=queue_health["next_action"],
                    commands=_queue_outcome_commands(first_pending),
                    queue_id=first_pending["queue_id"] if first_pending is not None else None,
                ),
                _console_guide_step(
                    step=2,
                    title="Recheck queue health",
                    status="pending",
                    summary="Confirm pending and stale promoted counts return to zero.",
                    commands=["uv run notification-hub personal-ops-queue-health"],
                ),
            ],
        )

    if queue_health["queued_count"] > 0:
        queue_id = first_queued["queue_id"] if first_queued is not None else "QUEUE_ID"
        return (
            "queue_review",
            [
                _console_guide_step(
                    step=1,
                    title="Review queued handoff",
                    status="current",
                    summary=queue_health["next_action"],
                    commands=[
                        "uv run notification-hub personal-ops-queue",
                        "uv run notification-hub personal-ops-queue "
                        f'--queue-id {queue_id} --status reviewed --reason "evidence checked"',
                    ],
                    queue_id=first_queued["queue_id"] if first_queued is not None else None,
                ),
                _console_guide_step(
                    step=2,
                    title="Promote through personal-ops",
                    status="pending",
                    summary="Create or update the downstream personal-ops suggestion, then record its id.",
                    commands=[
                        f'personal-ops notification-hub promote {queue_id} --note "..."',
                        "uv run notification-hub personal-ops-queue "
                        f"--queue-id {queue_id} --status promoted "
                        "--promotion-target-id SUGGESTION_ID --promotion-outcome pending",
                    ],
                    queue_id=first_queued["queue_id"] if first_queued is not None else None,
                ),
            ],
        )

    if readiness["decision"] != "ready_to_expand":
        return (
            "readiness",
            [
                _console_guide_step(
                    step=1,
                    title="Clear readiness gate",
                    status="current",
                    summary=readiness["next_action"],
                    commands=["uv run notification-hub coordination-readiness"],
                )
            ],
        )

    if first_new_action is not None:
        action = first_new_action["action"]
        return (
            "package_review",
            [
                _console_guide_step(
                    step=1,
                    title="Save review package",
                    status="current",
                    summary="Stage the current proposals locally for inspection.",
                    commands=["uv run notification-hub personal-ops-actions --save-review-package"],
                    action_id=action["action_id"],
                ),
                _console_guide_step(
                    step=2,
                    title="Validate and inspect package",
                    status="pending",
                    summary="Inspect the saved package in /review or validate it before queueing.",
                    commands=[
                        "uv run notification-hub validate-action-package path/to/actions.json"
                    ],
                    action_id=action["action_id"],
                ),
                _console_guide_step(
                    step=3,
                    title="Queue handoff for operator review",
                    status="pending",
                    summary="Queue only after the evidence looks right; this still does not apply work.",
                    commands=[
                        "uv run notification-hub personal-ops-import path/to/actions.json --enqueue"
                    ],
                    action_id=action["action_id"],
                ),
            ],
        )

    if active_actions:
        active = active_actions[0]
        return (
            "active_lineage",
            [
                _console_guide_step(
                    step=1,
                    title="Continue active handoff",
                    status="current",
                    summary="Finish the queued or promoted lifecycle before adding new work.",
                    commands=["uv run notification-hub personal-ops-queue-health"],
                    action_id=active["action"]["action_id"],
                    queue_id=active["queue_id"],
                )
            ],
        )

    handled_summary = (
        f"{len(handled_actions)} handled proposal(s) are visible as history."
        if handled_actions
        else "No active proposals are waiting."
    )
    return (
        "monitor",
        [
            _console_guide_step(
                step=1,
                title="Monitor for next signal",
                status="current",
                summary=f"{handled_summary} Keep watching the read-only console.",
                commands=[
                    "uv run notification-hub coordination-console",
                    "uv run notification-hub personal-ops-queue-health",
                ],
            )
        ],
    )


def run_coordination_console(
    *,
    hours: int = 2,
    limit: int = 5,
    group_history_path: Path | None = None,
) -> CoordinationConsoleReport:
    """Build one read-only coordination view after readiness has cleared expansion."""
    safe_hours = max(hours, 1)
    safe_limit = max(limit, 1)
    readiness = run_coordination_readiness(limit=safe_limit)
    actions = run_personal_ops_action_export(hours=safe_hours, limit=safe_limit)
    queue = run_personal_ops_import_queue_health_check(limit=safe_limit)
    outcome_sync_reminder = run_personal_ops_outcome_sync_reminder(limit=safe_limit)
    burn_in_reports = list_personal_ops_queue_burn_in_reports(limit=safe_limit)
    group_history = list_action_proposal_group_history(
        limit=safe_limit,
        history_path=group_history_path,
    )
    proposal_lineage = _build_proposal_lineage(actions["actions"])
    action_dismissals = actions.get("dismissals", [])
    active_actions = [
        action
        for action in proposal_lineage
        if action["lineage_status"] in {"new", "queued", "promoted"}
    ]
    handled_actions = [
        action
        for action in proposal_lineage
        if action["lineage_status"] in {"reviewed", "snoozed", "resolved", "ignored"}
    ]
    proposal_review = _build_proposal_review(
        active_actions=active_actions,
        handled_actions=handled_actions,
        group_history=group_history,
    )

    queue_health = queue["health"]
    next_signal = _build_next_signal_report(
        readiness=readiness,
        actions=actions,
        active_actions=active_actions,
        queue_health=queue_health,
        dismissals=action_dismissals,
        limit=safe_limit,
    )
    guide_stage, guide_steps = _build_coordination_console_guide(
        readiness=readiness,
        active_actions=active_actions,
        handled_actions=handled_actions,
        queue_health=queue_health,
        queued_items=queue["queued_items"],
        pending_promotion_items=queue["pending_promotion_items"],
    )
    current_guide_step = next(
        (step for step in guide_steps if step["status"] == "current"),
        None,
    )
    next_commands = current_guide_step["commands"] if current_guide_step is not None else []
    if queue_health["queued_count"] > 0 or queue_health["promoted_pending_count"] > 0:
        next_action = queue_health["next_action"]
    elif readiness["decision"] != "ready_to_expand":
        next_action = readiness["next_action"]
    elif any(action["lineage_status"] == "new" for action in active_actions):
        next_action = (
            "Save and validate a review package, then queue one handoff for operator review."
        )
    elif active_actions:
        next_action = "Continue the queued or promoted handoff lifecycle before adding new work."
    else:
        next_action = "Monitor /review for the next real handoff signal."

    status = (
        "ok"
        if (
            readiness["status"] == "ok"
            and actions["status"] == "ok"
            and queue["status"] == "ok"
            and outcome_sync_reminder["status"] == "ok"
        )
        else "warn"
    )
    return {
        "status": status,
        "readiness": readiness,
        "action_count": len(actions["actions"]),
        "active_action_count": len(active_actions),
        "handled_action_count": len(handled_actions),
        "dismissal_count": len(action_dismissals),
        "actions": active_actions[:safe_limit],
        "handled_actions": handled_actions[:safe_limit],
        "proposal_review": proposal_review,
        "dismissals": action_dismissals[:safe_limit],
        "next_signal": next_signal,
        "queue_health": queue_health,
        "queued_items": queue["queued_items"][:safe_limit],
        "pending_promotion_items": queue["pending_promotion_items"][:safe_limit],
        "outcome_sync_reminder": outcome_sync_reminder,
        "burn_in_reports": burn_in_reports,
        "guide_stage": guide_stage,
        "guide_steps": guide_steps,
        "next_commands": next_commands,
        "next_action": next_action,
        "applied": False,
    }


def run_operator_daily_state(
    *,
    hours: int = 24,
    limit: int = 10,
    save_report: bool = False,
    report_dir: Path | None = None,
) -> OperatorDailyStateReport:
    """Build a resume-ready operator state snapshot without applying work."""
    safe_hours = max(hours, 1)
    safe_limit = max(limit, 1)
    generated_at = datetime.now(timezone.utc).isoformat()
    runtime = run_status()
    queue_health = run_personal_ops_import_queue_health_check(limit=safe_limit)
    coordination_console = run_coordination_console(hours=safe_hours, limit=safe_limit)
    burn_in = run_burn_in(minutes=min(safe_hours * 60, 24 * 60), lines=200)
    dismissals = list_action_proposal_dismissals(limit=safe_limit)
    status = (
        "ok"
        if runtime["status"] == "ok"
        and queue_health["status"] == "ok"
        and coordination_console["status"] == "ok"
        and burn_in["status"] == "ok"
        else "warn"
    )
    next_action = coordination_console["next_action"]
    report: OperatorDailyStateReport = {
        "status": status,
        "generated_at": generated_at,
        "hours": safe_hours,
        "runtime": runtime,
        "queue_health": queue_health,
        "coordination_console": coordination_console,
        "burn_in": burn_in,
        "dismissals": dismissals,
        "next_action": next_action,
        "report_file": {
            "requested": False,
            "status": "not_requested",
            "path": str(report_dir) if report_dir is not None else None,
            "error": None,
        },
        "applied": False,
    }
    if save_report:
        report_file = write_operator_daily_state_report(report, output_dir=report_dir)
        report["report_file"] = report_file
        if report_file["status"] != "ok":
            report["status"] = "warn"
    return report


def run_operator_handoff_drill(
    *,
    save_burn_in_report: bool = False,
    report_dir: Path | None = None,
) -> OperatorHandoffDrillReport:
    """Run a temporary handoff lifecycle drill without touching the live import queue."""
    generated_at = datetime.now(timezone.utc).isoformat()
    scenario = run_personal_ops_queue_scenario()
    queue_burn_in = run_personal_ops_queue_burn_in(
        save_report=save_burn_in_report,
        report_dir=report_dir,
    )
    status = "ok" if scenario["status"] == "ok" and queue_burn_in["status"] == "ok" else "warn"
    review_steps = [
        "Open /review and inspect an action proposal before saving a package.",
        "Save and validate the package; queue only evidence-backed handoffs.",
        "Promote externally through personal-ops, then record the suggestion id and outcome.",
        "Rerun queue health and burn-in before expanding the apply boundary.",
    ]
    next_action = (
        "Use the same operator-mediated lifecycle for the next real handoff."
        if status == "ok"
        else "Fix the drill or burn-in warning before using the lifecycle for real handoffs."
    )
    return {
        "status": status,
        "generated_at": generated_at,
        "scenario": scenario,
        "queue_burn_in": queue_burn_in,
        "review_steps": review_steps,
        "next_action": next_action,
        "applied": False,
    }
