"""Typed report shapes produced by notification-hub operations."""

from __future__ import annotations

from typing import NotRequired, TypedDict


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


class DeliveryCheckState(TypedDict):
    last_slack_ok_at: str | None
    last_slack_event_id: str | None
    last_push_ok_at: str | None
    last_push_event_id: str | None


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
    latest_context: NotRequired[dict[str, object]]


class InboxReport(TypedDict):
    status: str
    hours: int
    events_seen: int
    needs_attention: list[InboxItemReport]
    waiting_or_blocked: list[InboxItemReport]
    ready: list[InboxItemReport]
    completed: list[InboxItemReport]
    rollups: list[InboxRollupReport]
    near_rollup_singles: list[InboxRollupReport]
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
    evidence_context: NotRequired[dict[str, object]]
    evidence_quality: str
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
    action_keys: list[str]
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


class ActionExportRetentionReport(TypedDict):
    status: str
    export_dir: str
    keep: int
    dry_run: bool
    total_count: int
    kept_count: int
    candidate_count: int
    deleted_count: int
    candidate_files: list[str]
    deleted_files: list[str]
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
    evidence_context: dict[str, object]
    evidence_quality: str
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


class PersonalOpsQueueReviewBatchReport(TypedDict):
    batch_key: str
    source_package_name: str
    title: str
    priority: str
    state: str
    item_count: int
    queue_ids: list[str]
    evidence_event_ids: list[str]
    summaries: list[str]
    first_queue_id: str | None
    suggested_next_action: str


class PersonalOpsQueueReviewReport(TypedDict):
    status: str
    queue_status: str
    queued_count: int
    pending_count: int
    stale_count: int
    operator_decision_count: int
    batch_count: int
    batches: list[PersonalOpsQueueReviewBatchReport]
    next_action: str
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
    evidence_quality: str
    rich_evidence_ready: bool
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
    visible_daemon_summary: DaemonLogSummary
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
    visible_daemon_summary: DaemonLogSummary
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
    visible_slack_delivery_failures: int
    latest_delivery_check: DeliveryCheckState
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
    lineage_reason: str
    lineage_history_event_type: str | None
    lineage_history_recorded_at: str | None
    lineage_history_outcome: str | None
    stable_key_matched: bool
    evidence_event_rotated: bool
    previous_action_id: str | None
    queue_id: str | None
    queue_status: str | None
    promotion_outcome: str | None
    promotion_target_id: str | None


class CoordinationProposalRouteRecommendation(TypedDict):
    decision: str
    reason: str
    suggested_next_action: str
    operator_decision_required_count: int
    promote_candidate_count: int
    suppress_candidate_count: int
    follow_up_candidate_count: int
    operator_decision_required_action_ids: list[str]
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
    rich_evidence_count: int
    thin_evidence_count: int
    history_count: int
    latest_history: ActionProposalGroupHistoryReport | None
    latest_outcome: str | None
    routing_recommendation: CoordinationProposalRouteRecommendation | None
    promotion_readiness: str
    promotion_readiness_summary: str
    promotion_ready_action_ids: list[str]
    promotion_blocked_action_ids: list[str]
    next_action: str


class CoordinationProposalReviewReport(TypedDict):
    mode: str
    summary: str
    active_count: int
    new_count: int
    queued_count: int
    promoted_count: int
    reviewed_only_count: int
    follow_up_count: int
    snoozed_count: int
    resolved_count: int
    ignored_count: int
    handled_count: int
    handled_mail_count: int
    handled_mail_rich_count: int
    handled_mail_thin_count: int
    handled_stable_key_match_count: int
    handled_evidence_rotation_count: int
    rich_follow_up_review_count: int
    rich_follow_up_action_ids: list[str]
    handled_history_summary: str | None
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
    watch_posture: str
    notify_on: list[str]
    quiet_reason: str | None
    qualifying_intents: list[str]
    hidden_action_count: int
    dismissed_count: int
    rich_follow_up_review_count: int
    policy_covered_repeated_count: int
    policy_covered_signatures: list[RepeatedSignatureReport]
    dismissed_proposals: list[ActionProposalDismissalReport]
    next_action: str


class HandoffOutcomeQualityBucket(TypedDict):
    total: int
    pending: int
    accepted: int
    rejected: int
    ignored: int
    resolved: int
    acceptance_rate: float | None


class HandoffOutcomeQualityReport(TypedDict):
    status: str
    summary: str
    rich: HandoffOutcomeQualityBucket
    thin: HandoffOutcomeQualityBucket
    unknown: HandoffOutcomeQualityBucket
    next_action: str


class FirstRichHandoffGateReport(TypedDict):
    status: str
    title: str
    summary: str
    operator_mediated: bool
    active_count: int
    active_rich_count: int
    active_thin_count: int
    queued_count: int
    pending_count: int
    stale_count: int
    rich_resolved_count: int
    rich_action_ids: list[str]
    thin_action_ids: list[str]
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
    outcome_quality: HandoffOutcomeQualityReport
    first_rich_handoff_gate: FirstRichHandoffGateReport
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


class NoiseCandidateReviewItem(TypedDict):
    count: int
    source: str
    project: str | None
    level: str
    title: str
    body: str
    decision_hint: str
    suggested_rule: str | None


class NoiseCandidateReviewReport(TypedDict):
    status: str
    report_name: str | None
    generated_at: str | None
    noise_candidate_count: int
    candidates: list[NoiseCandidateReviewItem]
    next_action: str
    applied: bool
    error: str | None


class OperatorDailyStateReport(TypedDict):
    status: str
    generated_at: str
    hours: int
    runtime: StatusReport
    queue_health: PersonalOpsImportQueueHealthCheckReport
    coordination_console: CoordinationConsoleReport
    burn_in: BurnInReport
    dismissals: list[ActionProposalDismissalReport]
    outcome_quality: HandoffOutcomeQualityReport
    outcome_quality_summary: str
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
