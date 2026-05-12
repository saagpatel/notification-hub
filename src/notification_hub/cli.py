"""Operator-facing command entrypoints."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from notification_hub.diagnostics import collect_doctor_report
from notification_hub.models import Event
from notification_hub.operations import (
    BootstrapConfigReport,
    BurnInReport,
    CoordinationConsoleReport,
    CoordinationReadinessReport,
    CoordinationSnapshotReport,
    DeliveryCheckReport,
    InboxReport,
    LogsReport,
    PersonalOpsActionExportReport,
    PersonalOpsImportReport,
    PersonalOpsImportQueueHealthCheckReport,
    PersonalOpsImportQueueHealthReport,
    PersonalOpsImportQueueItemReport,
    PersonalOpsImportQueueUpdateReport,
    PersonalOpsOutcomeSyncReminderReport,
    PersonalOpsQueueBurnInReport,
    PersonalOpsQueueReviewReport,
    PersonalOpsQueueScenarioReport,
    ActionPackageValidationReport,
    ActionProposalDismissalListReport,
    ActionProposalDismissReport,
    ActionProposalUndismissReport,
    ActionProposalGroupOutcomeReport,
    OperatorDailyStateReport,
    OperatorHandoffDrillReport,
    ActionExportRetentionReport,
    OperatorReviewSessionRetentionReport,
    OperatorReviewSessionReport,
    PolicyCheckReport,
    RetentionReport,
    SmokeReport,
    StatusReport,
    VerifyRuntimeReport,
    bootstrap_policy_config,
    dismiss_action_proposal,
    run_action_proposal_dismissal_list,
    run_burn_in,
    run_coordination_console,
    run_coordination_readiness,
    run_coordination_snapshot,
    run_delivery_check,
    run_inbox,
    run_logs,
    run_operator_daily_state,
    run_operator_handoff_drill,
    run_operator_review_session,
    run_personal_ops_action_export,
    run_personal_ops_import_queue_health_check,
    run_personal_ops_import_stub,
    run_personal_ops_outcome_sync_reminder,
    run_personal_ops_queue_burn_in,
    run_personal_ops_queue_review,
    run_personal_ops_queue_scenario,
    list_personal_ops_import_queue,
    summarize_personal_ops_import_queue,
    undismiss_action_proposal,
    update_personal_ops_import_queue_item,
    validate_action_package,
    run_policy_check,
    run_retention,
    run_smoke_check,
    run_status,
    run_verify_runtime,
    record_action_proposal_group_outcome,
    prune_action_export_files,
    prune_operator_review_session_reports,
)
from notification_hub.pipeline import build_event_explanation_report

_INBOX_SECTIONS: tuple[
    tuple[str, str],
    ...,
] = (
    ("needs_attention", "needs attention"),
    ("waiting_or_blocked", "waiting or blocked"),
    ("ready", "ready"),
    ("completed", "completed"),
)


def _build_parser(prog: str = "notification-hub") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog)
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Run the built-in doctor checks.")
    doctor.add_argument(
        "--json",
        action="store_true",
        help="Emit the doctor report as JSON.",
    )

    smoke = subparsers.add_parser("smoke", help="Send a harmless smoke event and verify the log.")
    smoke.add_argument(
        "--json",
        action="store_true",
        help="Emit the smoke report as JSON.",
    )

    status = subparsers.add_parser("status", help="Show a compact read-only runtime summary.")
    status.add_argument(
        "--json",
        action="store_true",
        help="Emit the status report as JSON.",
    )

    logs = subparsers.add_parser("logs", help="Show recent events and daemon log tails.")
    logs.add_argument(
        "--events",
        type=int,
        default=5,
        help="Number of recent stored events to show.",
    )
    logs.add_argument(
        "--lines",
        type=int,
        default=20,
        help="Number of daemon stdout/stderr lines to show.",
    )
    logs.add_argument(
        "--json",
        action="store_true",
        help="Emit the logs report as JSON.",
    )

    burn_in = subparsers.add_parser(
        "burn-in",
        help="Summarize recent runtime acceptance and repeated event signatures.",
    )
    burn_in.add_argument(
        "--minutes",
        type=int,
        default=10,
        help="Recent event window to summarize.",
    )
    burn_in.add_argument(
        "--lines",
        type=int,
        default=200,
        help="Daemon log tail size used for accepted/rejected counts.",
    )
    burn_in.add_argument(
        "--json",
        action="store_true",
        help="Emit the burn-in report as JSON.",
    )

    verify_runtime = subparsers.add_parser(
        "verify-runtime",
        help="Run the core live-runtime checks without posting an event by default.",
    )
    verify_runtime.add_argument(
        "--include-smoke",
        action="store_true",
        help="Also post a harmless smoke event and verify it lands in the log.",
    )
    verify_runtime.add_argument(
        "--verify-slack",
        action="store_true",
        help="Send one explicit Slack delivery-check message.",
    )
    verify_runtime.add_argument(
        "--verify-push",
        action="store_true",
        help="Send one explicit local push delivery-check notification.",
    )
    verify_runtime.add_argument(
        "--json",
        action="store_true",
        help="Emit the runtime verification report as JSON.",
    )

    policy_check = subparsers.add_parser(
        "policy-check",
        help="Analyze the current policy config for overlaps, shadowing, and no-op rules.",
    )
    policy_check.add_argument(
        "--json",
        action="store_true",
        help="Emit the policy-check report as JSON.",
    )

    delivery_check = subparsers.add_parser(
        "delivery-check",
        help="Send explicit opt-in Slack and/or push transport checks.",
    )
    delivery_check.add_argument(
        "--slack",
        action="store_true",
        help="Send one Slack delivery-check message.",
    )
    delivery_check.add_argument(
        "--push",
        action="store_true",
        help="Send one local push delivery-check notification.",
    )
    delivery_check.add_argument(
        "--json",
        action="store_true",
        help="Emit the delivery-check report as JSON.",
    )

    inbox = subparsers.add_parser(
        "inbox",
        help="Show recent events grouped by coordination intent.",
    )
    inbox.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Recent event window to summarize.",
    )
    inbox.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum items per inbox section.",
    )
    inbox.add_argument(
        "--json",
        action="store_true",
        help="Emit the inbox report as JSON.",
    )

    coordination_snapshot = subparsers.add_parser(
        "coordination-snapshot",
        help="Build a bridge-ready snapshot from runtime status and inbox state.",
    )
    coordination_snapshot.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Recent inbox window to summarize.",
    )
    coordination_snapshot.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum items per inbox section.",
    )
    coordination_snapshot.add_argument(
        "--output",
        help="Optional JSON output path. Stdout remains the default.",
    )
    coordination_snapshot.add_argument(
        "--save-bridge-db",
        action="store_true",
        help="Also save the bridge_snapshot payload into bridge-db as a Codex snapshot.",
    )
    coordination_snapshot.add_argument(
        "--bridge-db-path",
        help="Optional bridge-db SQLite path. Defaults to BRIDGE_DB_PATH or the standard local path.",
    )
    coordination_snapshot.add_argument(
        "--json",
        action="store_true",
        help="Emit the full snapshot report as JSON.",
    )

    coordination_readiness = subparsers.add_parser(
        "coordination-readiness",
        help="Summarize whether the operator loop is ready for broader coordination work.",
    )
    coordination_readiness.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Saved queue burn-in reports to inspect.",
    )
    coordination_readiness.add_argument(
        "--json",
        action="store_true",
        help="Emit the coordination readiness report as JSON.",
    )

    coordination_console = subparsers.add_parser(
        "coordination-console",
        help="Show a compact read-only coordination console summary.",
    )
    coordination_console.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Recent action-proposal window to summarize.",
    )
    coordination_console.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum actions, queue items, and burn-in reports to include.",
    )
    coordination_console.add_argument(
        "--json",
        action="store_true",
        help="Emit the coordination console report as JSON.",
    )

    personal_ops_actions = subparsers.add_parser(
        "personal-ops-actions",
        help="Prepare personal-ops action proposals from inbox rollups without writing them.",
    )
    personal_ops_actions.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Recent inbox window to summarize.",
    )
    personal_ops_actions.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum action proposals to emit.",
    )
    personal_ops_actions.add_argument(
        "--output",
        help="Optional JSON output path. Stdout remains the default.",
    )
    personal_ops_actions.add_argument(
        "--save-review-package",
        action="store_true",
        help="Save a review package under the local notification-hub action export directory.",
    )
    personal_ops_actions.add_argument(
        "--review-dir",
        help="Optional directory for saved review packages.",
    )
    personal_ops_actions.add_argument(
        "--json",
        action="store_true",
        help="Emit the full action export as JSON.",
    )

    validate_action_package_parser = subparsers.add_parser(
        "validate-action-package",
        help="Validate a saved personal-ops action review package without importing it.",
    )
    validate_action_package_parser.add_argument(
        "path",
        help="Path to a saved personal-ops action review package JSON file.",
    )
    validate_action_package_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the validation report as JSON.",
    )

    action_proposal_dismiss = subparsers.add_parser(
        "action-proposal-dismiss",
        help="Dismiss a repeated action proposal from the local coordination console.",
    )
    action_proposal_dismiss.add_argument(
        "dismissal_key",
        help="Stable dismissal key from a personal-ops action proposal.",
    )
    action_proposal_dismiss.add_argument(
        "--reason",
        default="dismissed as known repeated noise",
        help="Operator note explaining why this proposal should stay hidden.",
    )
    action_proposal_dismiss.add_argument(
        "--json",
        action="store_true",
        help="Emit the dismissal report as JSON.",
    )

    action_proposal_dismissals = subparsers.add_parser(
        "action-proposal-dismissals",
        help="List or inspect local action proposal dismissals.",
    )
    action_proposal_dismissals.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Maximum dismissals to show.",
    )
    action_proposal_dismissals.add_argument(
        "--dismissal-key",
        help="Inspect one dismissal key.",
    )
    action_proposal_dismissals.add_argument(
        "--include-inactive",
        action="store_true",
        help="Include undismissed dismissal history.",
    )
    action_proposal_dismissals.add_argument(
        "--json",
        action="store_true",
        help="Emit the dismissal list report as JSON.",
    )

    action_proposal_undismiss = subparsers.add_parser(
        "action-proposal-undismiss",
        help="Reactivate a repeated action proposal by adding an undismiss tombstone.",
    )
    action_proposal_undismiss.add_argument(
        "dismissal_key",
        help="Stable dismissal key to remove from active dismissals.",
    )
    action_proposal_undismiss.add_argument(
        "--reason",
        default="undismissed by operator",
        help="Operator note explaining why this dismissal should be removed.",
    )
    action_proposal_undismiss.add_argument(
        "--json",
        action="store_true",
        help="Emit the undismiss report as JSON.",
    )

    action_proposal_group_outcome = subparsers.add_parser(
        "action-proposal-group-outcome",
        help="Record a local outcome for one Proposal Review group.",
    )
    action_proposal_group_outcome.add_argument(
        "group_key",
        help="Proposal Review group key from coordination-console.",
    )
    action_proposal_group_outcome.add_argument(
        "--outcome",
        required=True,
        choices=["accepted", "rejected", "snoozed", "superseded", "needs_follow_up"],
        help="Local group outcome to record.",
    )
    action_proposal_group_outcome.add_argument(
        "--reason",
        default="operator recorded group outcome",
        help="Operator note explaining the group outcome.",
    )
    action_proposal_group_outcome.add_argument(
        "--hours",
        type=int,
        default=2,
        help="Recent action-proposal window to search.",
    )
    action_proposal_group_outcome.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Maximum action proposals to search.",
    )
    action_proposal_group_outcome.add_argument(
        "--json",
        action="store_true",
        help="Emit the group outcome report as JSON.",
    )

    operator_daily_state = subparsers.add_parser(
        "operator-daily-state",
        help="Build a resume-ready local operator state snapshot.",
    )
    operator_daily_state.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Recent runtime window to summarize.",
    )
    operator_daily_state.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum queue, dismissal, and console items to include.",
    )
    operator_daily_state.add_argument(
        "--save-report",
        action="store_true",
        help="Save the snapshot under local notification-hub runtime state.",
    )
    operator_daily_state.add_argument(
        "--report-dir",
        help="Optional directory for saved operator state reports.",
    )
    operator_daily_state.add_argument(
        "--json",
        action="store_true",
        help="Emit the operator daily state report as JSON.",
    )

    operator_review_session = subparsers.add_parser(
        "operator-review-session",
        help="Summarize recent local review-session activity.",
    )
    operator_review_session.add_argument(
        "--hours",
        type=int,
        default=2,
        help="Recent review-session window to summarize.",
    )
    operator_review_session.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Maximum group-history and queue items to include.",
    )
    operator_review_session.add_argument(
        "--save-report",
        action="store_true",
        help="Save a timestamped review-session JSON report under local runtime state.",
    )
    operator_review_session.add_argument(
        "--report-dir",
        help="Optional directory for saved review-session reports.",
    )
    operator_review_session.add_argument(
        "--json",
        action="store_true",
        help="Emit the operator review-session report as JSON.",
    )

    operator_review_session_retention = subparsers.add_parser(
        "operator-review-session-retention",
        help="Prune older saved operator review-session reports.",
    )
    operator_review_session_retention.add_argument(
        "--keep",
        type=int,
        default=20,
        help="Keep this many newest saved review-session reports.",
    )
    operator_review_session_retention.add_argument(
        "--apply",
        action="store_true",
        help="Delete older reports. Without this, only show prune candidates.",
    )
    operator_review_session_retention.add_argument(
        "--report-dir",
        help="Optional directory for saved review-session reports.",
    )
    operator_review_session_retention.add_argument(
        "--json",
        action="store_true",
        help="Emit the review-session retention report as JSON.",
    )

    action_export_retention = subparsers.add_parser(
        "action-export-retention",
        help="Prune older saved action-export files.",
    )
    action_export_retention.add_argument(
        "--keep",
        type=int,
        default=20,
        help="Keep this many newest action-export files.",
    )
    action_export_retention.add_argument(
        "--apply",
        action="store_true",
        help="Delete older files. Without this, only show prune candidates.",
    )
    action_export_retention.add_argument(
        "--export-dir",
        help="Optional directory for saved action-export files.",
    )
    action_export_retention.add_argument(
        "--json",
        action="store_true",
        help="Emit the retention report as JSON.",
    )

    operator_handoff_drill = subparsers.add_parser(
        "operator-handoff-drill",
        help="Run a temporary handoff lifecycle drill through the review model.",
    )
    operator_handoff_drill.add_argument(
        "--save-burn-in-report",
        action="store_true",
        help="Save the queue burn-in report produced during the drill.",
    )
    operator_handoff_drill.add_argument(
        "--report-dir",
        help="Optional directory for saved burn-in reports.",
    )
    operator_handoff_drill.add_argument(
        "--json",
        action="store_true",
        help="Emit the operator handoff drill report as JSON.",
    )

    personal_ops_import = subparsers.add_parser(
        "personal-ops-import",
        help="Validate a personal-ops action package and stop before mutation.",
    )
    personal_ops_import.add_argument(
        "path",
        help="Path to a saved personal-ops action review package JSON file.",
    )
    personal_ops_import.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Validate only. This is currently always true.",
    )
    personal_ops_import.add_argument(
        "--enqueue",
        action="store_true",
        help="Add valid action proposals to the local personal-ops import queue without applying them.",
    )
    personal_ops_import.add_argument(
        "--queue-path",
        help="Optional JSONL queue path for enqueued import handoff items.",
    )
    personal_ops_import.add_argument(
        "--json",
        action="store_true",
        help="Emit the import report as JSON.",
    )

    personal_ops_queue = subparsers.add_parser(
        "personal-ops-queue",
        help="List or update queued personal-ops handoff items without applying work.",
    )
    personal_ops_queue.add_argument(
        "--queue-id",
        help="Queue item id to update. Omit to list queue items.",
    )
    personal_ops_queue.add_argument(
        "--status",
        choices=["queued", "reviewed", "rejected", "snoozed", "superseded", "promoted"],
        help="Lifecycle status to set for the queue item.",
    )
    personal_ops_queue.add_argument(
        "--reason",
        help="Optional review or promotion note.",
    )
    personal_ops_queue.add_argument(
        "--snoozed-until",
        help="Required when setting --status snoozed.",
    )
    personal_ops_queue.add_argument(
        "--promotion-target",
        help="Optional target label when setting --status promoted.",
    )
    personal_ops_queue.add_argument(
        "--promotion-target-id",
        help="Optional target id when setting --status promoted.",
    )
    personal_ops_queue.add_argument(
        "--promotion-outcome",
        choices=["pending", "accepted", "rejected", "ignored"],
        help="Optional promotion outcome to record.",
    )
    personal_ops_queue.add_argument(
        "--promotion-outcome-note",
        help="Optional note describing the promotion outcome.",
    )
    personal_ops_queue.add_argument(
        "--queue-path",
        help="Optional JSONL queue path.",
    )
    personal_ops_queue.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum queue items to list.",
    )
    personal_ops_queue.add_argument(
        "--json",
        action="store_true",
        help="Emit the queue report as JSON.",
    )

    personal_ops_queue_scenario = subparsers.add_parser(
        "personal-ops-queue-scenario",
        help="Run a temporary end-to-end personal-ops queue lifecycle scenario.",
    )
    personal_ops_queue_scenario.add_argument(
        "--json",
        action="store_true",
        help="Emit the scenario report as JSON.",
    )

    personal_ops_queue_health = subparsers.add_parser(
        "personal-ops-queue-health",
        help="Report import queue maintenance state and next commands without applying work.",
    )
    personal_ops_queue_health.add_argument(
        "--queue-path",
        help="Optional JSONL queue path.",
    )
    personal_ops_queue_health.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum queue items to inspect.",
    )
    personal_ops_queue_health.add_argument(
        "--stale-after-hours",
        type=float,
        default=4.0,
        help="Age threshold for stale promoted-pending handoffs.",
    )
    personal_ops_queue_health.add_argument(
        "--json",
        action="store_true",
        help="Emit the queue health report as JSON.",
    )

    personal_ops_queue_review = subparsers.add_parser(
        "personal-ops-queue-review",
        help="Summarize queued handoff batches without applying decisions.",
    )
    personal_ops_queue_review.add_argument(
        "--queue-path",
        help="Optional JSONL queue path.",
    )
    personal_ops_queue_review.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Maximum queue items to inspect.",
    )
    personal_ops_queue_review.add_argument(
        "--stale-after-hours",
        type=float,
        default=4.0,
        help="Hours before a pending promotion is considered stale.",
    )
    personal_ops_queue_review.add_argument(
        "--json",
        action="store_true",
        help="Emit the queue review report as JSON.",
    )

    personal_ops_queue_burn_in = subparsers.add_parser(
        "personal-ops-queue-burn-in",
        help="Check queue lifecycle readiness, recent runtime noise, and live operator next steps.",
    )
    personal_ops_queue_burn_in.add_argument(
        "--minutes",
        type=int,
        default=10,
        help="Recent runtime window to inspect.",
    )
    personal_ops_queue_burn_in.add_argument(
        "--lines",
        type=int,
        default=200,
        help="Daemon log tail lines to inspect.",
    )
    personal_ops_queue_burn_in.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum queue items to inspect.",
    )
    personal_ops_queue_burn_in.add_argument(
        "--save-report",
        action="store_true",
        help="Save the burn-in report under local notification-hub runtime state.",
    )
    personal_ops_queue_burn_in.add_argument(
        "--report-dir",
        help="Optional directory for saved burn-in reports.",
    )
    personal_ops_queue_burn_in.add_argument(
        "--json",
        action="store_true",
        help="Emit the burn-in report as JSON.",
    )

    personal_ops_outcome_sync_reminder = subparsers.add_parser(
        "personal-ops-outcome-sync-reminder",
        help="Report promoted personal-ops handoffs that still need outcome sync.",
    )
    personal_ops_outcome_sync_reminder.add_argument(
        "--queue-path",
        help="Override the local import queue path.",
    )
    personal_ops_outcome_sync_reminder.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum pending promoted items to include.",
    )
    personal_ops_outcome_sync_reminder.add_argument(
        "--stale-after-hours",
        type=float,
        default=4.0,
        help="Age threshold for stale promoted-pending handoffs.",
    )
    personal_ops_outcome_sync_reminder.add_argument(
        "--json",
        action="store_true",
        help="Emit the reminder report as JSON.",
    )

    explain = subparsers.add_parser(
        "explain",
        help="Show how an event would classify, route, and deliver without sending it.",
    )
    explain.add_argument("--source", required=True, help="Event source.")
    explain.add_argument("--level", required=True, help="Source-provided level hint.")
    explain.add_argument("--title", required=True, help="Event title.")
    explain.add_argument("--body", required=True, help="Event body.")
    explain.add_argument("--project", help="Optional project name.")
    explain.add_argument(
        "--json",
        action="store_true",
        help="Emit the explanation report as JSON.",
    )

    retention = subparsers.add_parser(
        "retention",
        help="Archive older live-log events when the JSONL file grows past a threshold.",
    )
    retention.add_argument(
        "--max-events",
        type=int,
        default=2000,
        help="Keep this many most-recent events in the live log.",
    )
    retention.add_argument(
        "--keep-archives",
        type=int,
        default=10,
        help="Keep this many archive files before pruning older ones.",
    )
    retention.add_argument(
        "--json",
        action="store_true",
        help="Emit the retention report as JSON.",
    )

    bootstrap = subparsers.add_parser(
        "bootstrap-config",
        help="Copy the sample policy config into the live config location.",
    )
    bootstrap.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing live config file.",
    )
    bootstrap.add_argument(
        "--json",
        action="store_true",
        help="Emit the bootstrap report as JSON.",
    )
    return parser


def _print_doctor_report(report: dict[str, object]) -> None:
    checks = report["checks"]
    config = report["config"]
    local_api = report["local_api"]
    retention = report["retention"]
    assert isinstance(checks, dict)
    assert isinstance(config, dict)
    assert isinstance(local_api, dict)
    assert isinstance(retention, dict)

    print(f"notification-hub doctor: {report['status']}")
    print(f"- local API healthy: {checks['local_api_healthy']}")
    print(f"- LaunchAgent present: {checks['launch_agent_present']}")
    print(f"- bridge file present: {checks['bridge_file_present']}")
    print(f"- push notifier available: {checks['push_notifier_available']}")
    print(f"- Slack configured: {checks['slack_configured']}")
    print(f"- policy load OK: {checks['policy_load_ok']}")
    if "runtime_wiring_current" in checks:
        print(f"- runtime wiring current: {checks['runtime_wiring_current']}")
    print(f"- policy path: {config['path']}")
    print(f"- retention enabled: {retention['enabled']}")
    print(f"- retention interval minutes: {retention['interval_minutes']}")
    if config["load_error"] is not None:
        print(f"- policy load error: {config['load_error']}")
    print(f"- health URL: {local_api['url']}")


def _print_smoke_report(report: SmokeReport) -> None:
    print(f"notification-hub smoke: {report['status']}")
    print(f"- event URL: {report['event_url']}")
    print(f"- health URL: {report['health_url']}")
    print(f"- response status: {report['response_status']}")
    print(f"- event ID: {report['event_id']}")
    print(f"- log verified: {report['log_verified']}")
    if report["error"] is not None:
        print(f"- error: {report['error']}")


def _print_delivery_check_report(report: DeliveryCheckReport) -> None:
    print(f"notification-hub delivery-check: {report['status']}")
    print(f"- Slack requested: {report['verify_slack']}")
    print(f"- Slack OK: {report['slack_ok']}")
    print(f"- push requested: {report['verify_push']}")
    print(f"- push OK: {report['push_ok']}")
    print(f"- event ID: {report['event_id']}")
    if report["error"] is not None:
        print(f"- error: {report['error']}")


def _print_inbox_report(report: InboxReport) -> None:
    print(f"notification-hub inbox: {report['status']}")
    print(f"- window: {report['hours']} hours")
    print(f"- events seen: {report['events_seen']}")
    if report["error"] is not None:
        print(f"- error: {report['error']}")

    for key, label in _INBOX_SECTIONS:
        print(f"- {label}:")
        items = cast(list[dict[str, object]], report[key])
        if not items:
            print("  none")
        for item in items:
            project = f" ({item['project']})" if item["project"] else ""
            print(f"  - [{item['intent']}] {item['source']}{project}: {item['title']}")

    print("- noise candidates:")
    if not report["noise_candidates"]:
        print("  none")
    for item in report["noise_candidates"]:
        project = f" ({item['project']})" if item["project"] else ""
        print(f"  - x{item['count']} {item['source']}{project}: {item['title']}")

    print("- rollups:")
    if not report["rollups"]:
        print("  none")
    for item in report["rollups"]:
        project = f" ({item['project']})" if item["project"] else ""
        print(f"  - x{item['count']} [{item['intent']}] {item['source']}{project}: {item['title']}")


def _print_status_report(report: StatusReport) -> None:
    print(f"notification-hub status: {report['status']}")
    print(f"- daemon reachable: {report['daemon_reachable']}")
    print(f"- watcher active: {report['watcher_active']}")
    print(f"- runtime wiring current: {report['runtime_wiring_current']}")
    print(f"- policy config found: {report['policy_config_found']}")
    print(f"- policy warnings: {report['policy_warning_count']}")
    print(f"- retention enabled: {report['retention_enabled']}")
    print(f"- retention last status: {report['retention_last_status']}")
    print(f"- events processed: {report['events_processed']}")
    print(f"- Slack configured: {report['slack_configured']}")
    print(f"- Slack delivery failures: {report['slack_delivery_failures']}")
    print(f"- import queue queued: {report['import_queue']['queued_count']}")
    print(f"- push notifier available: {report['push_notifier_available']}")
    print(f"- next action: {report['next_action']}")


def _write_json_report(report: object, output_path: str | None) -> None:
    content = json.dumps(report, indent=2, sort_keys=True)
    if output_path is None:
        print(content)
        return

    path = Path(output_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{content}\n", encoding="utf-8")
    print(str(path))


def _print_coordination_snapshot_report(report: CoordinationSnapshotReport) -> None:
    inbox = report["inbox"]
    runtime_status = report["runtime_status"]

    print(f"notification-hub coordination-snapshot: {report['status']}")
    print(f"- schema: {report['schema_version']}")
    print(f"- bridge target: {report['bridge_target_system']}")
    print(f"- snapshot date: {report['bridge_snapshot_date']}")
    print(f"- bridge save: {report['bridge_save']['status']}")
    if report["bridge_save"]["snapshot_id"] is not None:
        print(f"- bridge snapshot ID: {report['bridge_save']['snapshot_id']}")
    print(f"- runtime: {runtime_status['status']}")
    print(f"- inbox events seen: {inbox['events_seen']}")
    print(f"- needs attention: {len(inbox['needs_attention'])}")
    print(f"- waiting or blocked: {len(inbox['waiting_or_blocked'])}")
    print(f"- ready: {len(inbox['ready'])}")
    print(f"- rollups: {len(inbox['rollups'])}")
    print(f"- noise candidates: {len(inbox['noise_candidates'])}")
    print("- follow-up:")
    for item in report["follow_up"]:
        print(f"  - {item}")
    if report["error"] is not None:
        print(f"- error: {report['error']}")


def _print_coordination_readiness_report(report: CoordinationReadinessReport) -> None:
    print(f"notification-hub coordination-readiness: {report['status']}")
    print(f"- decision: {report['decision']}")
    print(f"- summary: {report['summary']}")
    print(f"- runtime: {report['runtime_status']}")
    print(f"- policy warnings: {report['policy_warning_count']}")
    print(f"- queue: {report['queue_status']}")
    print(
        "- queued/pending/stale: "
        f"{report['queued_count']}/{report['pending_count']}/{report['stale_count']}"
    )
    print(f"- saved burn-in reports: {report['saved_burn_in_reports']}")
    print(f"- latest burn-in ready: {report['latest_burn_in_ready']}")
    print(f"- latest noise candidates: {report['latest_burn_in_noise_candidates']}")
    print(f"- next action: {report['next_action']}")
    print("- evidence:")
    for item in report["evidence"]:
        print(f"  - {item}")


def _print_coordination_console_report(report: CoordinationConsoleReport) -> None:
    readiness = report["readiness"]
    queue = report["queue_health"]
    print(f"notification-hub coordination-console: {report['status']}")
    print(f"- readiness: {readiness['decision']}")
    print(f"- active actions: {report['active_action_count']}")
    print(f"- handled actions: {report['handled_action_count']}")
    print(f"- queued: {queue['queued_count']}")
    print(f"- promoted pending: {queue['promoted_pending_count']}")
    print(f"- promoted stale: {queue['promoted_pending_stale_count']}")
    print(f"- burn-in reports: {len(report['burn_in_reports'])}")
    proposal_review = report["proposal_review"]
    outcome_quality = report["outcome_quality"]
    next_signal = report["next_signal"]
    print(f"- review mode: {proposal_review['mode']}")
    print(f"- proposal groups: {proposal_review['group_count']}")
    print(f"- handled mail: {proposal_review['handled_mail_count']}")
    print(f"- stable-key history: {proposal_review['handled_stable_key_match_count']}")
    print(f"- evidence rotations: {proposal_review['handled_evidence_rotation_count']}")
    print(f"- outcome quality: {outcome_quality['summary']}")
    print(f"- watch posture: {next_signal['watch_posture']}")
    print(f"- guide stage: {report['guide_stage']}")
    print(f"- next action: {report['next_action']}")
    if proposal_review["groups"]:
        print("- proposal groups:")
        for group in proposal_review["groups"]:
            project = f" ({group['project']})" if group["project"] else ""
            print(
                "  - "
                f"x{group['action_count']} {group['source']}{project} "
                f"{group['intent']} / {group['priority']}: "
                f"{', '.join(group['titles'])}"
            )
            print(
                "    evidence: "
                f"rich {group['rich_evidence_count']} / thin {group['thin_evidence_count']}"
            )
            print(
                "    promotion readiness: "
                f"{group['promotion_readiness']} - {group['promotion_readiness_summary']}"
            )
            routing = group.get("routing_recommendation")
            if isinstance(routing, dict):
                print(f"    route: {routing.get('decision')} - {routing.get('reason')}")
            latest_history = group.get("latest_history")
            if isinstance(latest_history, dict):
                print(
                    "    history: "
                    f"{latest_history.get('event_type')} "
                    f"({latest_history.get('status')})"
                )
    if report["handled_actions"]:
        print("- handled history:")
        for item in report["handled_actions"][:3]:
            action = item["action"]
            print(f"  - {action['title']}: {item['lineage_label']} - {item['lineage_reason']}")
    if proposal_review["group_history"]:
        print("- recent group history:")
        for item in proposal_review["group_history"][:3]:
            print(f"  - {item['event_type']} {item['group_key']} ({item['status']})")
    if report["next_commands"]:
        print("- next commands:")
        for command in report["next_commands"]:
            print(f"  - {command}")


def _print_personal_ops_action_export_report(report: PersonalOpsActionExportReport) -> None:
    print(f"notification-hub personal-ops-actions: {report['status']}")
    print(f"- schema: {report['schema_version']}")
    print(f"- window: {report['hours']} hours")
    print(f"- actions: {len(report['actions'])}")
    print(f"- dismissed: {report['dismissed_action_count']}")
    print(f"- review package: {report['review_package']['status']}")
    if report["review_package"]["path"] is not None:
        print(f"- review path: {report['review_package']['path']}")
    if report["error"] is not None:
        print(f"- error: {report['error']}")
    for action in report["actions"]:
        project = f" ({action['project']})" if action["project"] else ""
        print(
            f"  - [{action['priority']}/{action['state']}] "
            f"{action['source']}{project}: {action['title']} x{action['count']}"
        )
        print(f"    next: {action['suggested_next_action']}")
        print(
            f"    dismiss: uv run notification-hub action-proposal-dismiss {action['dismissal_key']}"
        )


def _print_action_proposal_dismiss_report(report: ActionProposalDismissReport) -> None:
    print(f"notification-hub action-proposal-dismiss: {report['status']}")
    print(f"- path: {report['path']}")
    if report["dismissal"] is not None:
        print(f"- dismissal: {report['dismissal']['dismissal_key']}")
        print(f"- reason: {report['dismissal']['reason']}")
    if report["error"] is not None:
        print(f"- error: {report['error']}")


def _print_action_proposal_dismissal_list_report(
    report: ActionProposalDismissalListReport,
) -> None:
    print(f"notification-hub action-proposal-dismissals: {report['status']}")
    print(f"- path: {report['path']}")
    print(f"- dismissals: {report['dismissal_count']}")
    if not report["dismissals"]:
        print("- items: none")
        return
    print("- items:")
    for dismissal in report["dismissals"]:
        state = "active" if dismissal["active"] else "inactive"
        title = dismissal["title"] or dismissal["dismissal_key"]
        print(f"  - [{state}] {dismissal['dismissal_key']}: {title}")
        print(f"    reason: {dismissal['reason']}")
        if dismissal["deleted_at"] is not None:
            print(f"    removed: {dismissal['deleted_at']}")


def _print_action_proposal_undismiss_report(report: ActionProposalUndismissReport) -> None:
    print(f"notification-hub action-proposal-undismiss: {report['status']}")
    print(f"- path: {report['path']}")
    print(f"- dismissal: {report['dismissal_key']}")
    print(f"- removed: {report['removed']}")
    if report["error"] is not None:
        print(f"- error: {report['error']}")


def _print_action_proposal_group_outcome_report(
    report: ActionProposalGroupOutcomeReport,
) -> None:
    print(f"notification-hub action-proposal-group-outcome: {report['status']}")
    print(f"- group: {report['group_key']}")
    print(f"- outcome: {report['outcome']}")
    if report["group_history"] is not None:
        print(f"- recorded: {report['group_history']['recorded_at']}")
    print(f"- next action: {report['next_action']}")
    if report["error"] is not None:
        print(f"- error: {report['error']}")


def _print_operator_daily_state_report(report: OperatorDailyStateReport) -> None:
    console = report["coordination_console"]
    queue = report["queue_health"]["health"]
    print(f"notification-hub operator-daily-state: {report['status']}")
    print(f"- generated: {report['generated_at']}")
    print(f"- window: {report['hours']} hours")
    print(f"- runtime: {report['runtime']['status']}")
    print(f"- queue: {queue['status']}")
    print(
        f"- queued/pending/stale: {queue['queued_count']}/{queue['promoted_pending_count']}/{queue['promoted_pending_stale_count']}"
    )
    print(f"- next signal: {console['next_signal']['title']} ({console['next_signal']['status']})")
    print(f"- active actions: {console['active_action_count']}")
    print(f"- outcome quality: {report['outcome_quality_summary']}")
    print(f"- dismissals: {len(report['dismissals'])}")
    print(f"- burn-in: {report['burn_in']['status']}")
    report_file = report["report_file"]
    if report_file.get("requested"):
        print(f"- report file: {report_file.get('status')}")
        if report_file.get("path") is not None:
            print(f"- report path: {report_file.get('path')}")
    print(f"- next action: {report['next_action']}")


def _print_operator_handoff_drill_report(report: OperatorHandoffDrillReport) -> None:
    print(f"notification-hub operator-handoff-drill: {report['status']}")
    print(f"- generated: {report['generated_at']}")
    print(f"- scenario: {report['scenario']['status']}")
    print(
        f"- rich evidence ready: {report['scenario']['rich_evidence_ready']} "
        f"({report['scenario']['evidence_quality']})"
    )
    print(f"- queue burn-in: {report['queue_burn_in']['status']}")
    print(f"- ready for live promotion: {report['queue_burn_in']['ready_for_live_promotion']}")
    print(f"- next action: {report['next_action']}")
    print("- review steps:")
    for step in report["review_steps"]:
        print(f"  - {step}")


def _print_operator_review_session_report(report: OperatorReviewSessionReport) -> None:
    print(f"notification-hub operator-review-session: {report['status']}")
    print(f"- generated: {report['generated_at']}")
    print(f"- window: {report['hours']} hours")
    print(f"- group history: {report['group_history_count']}")
    print(f"- queue items: {report['queue_item_count']}")
    print(
        "- saved/queued/dismissed/outcomes: "
        f"{report['saved_count']}/{report['queued_count']}/"
        f"{report['dismissed_count']}/{report['outcome_count']}"
    )
    print(
        "- reviewed/active/pending: "
        f"{report['reviewed_count']}/{report['active_queue_count']}/"
        f"{report['pending_promotion_count']}"
    )
    if report["route_counts"]:
        routes = ", ".join(
            f"{route}={count}" for route, count in sorted(report["route_counts"].items())
        )
        print(f"- routes: {routes}")
    report_file = report["report_file"]
    if report_file.get("requested"):
        print(f"- report file: {report_file.get('status')}")
        if report_file.get("path") is not None:
            print(f"- report path: {report_file.get('path')}")
    print(f"- next action: {report['next_action']}")


def _print_operator_review_session_retention_report(
    report: OperatorReviewSessionRetentionReport,
) -> None:
    mode = "dry run" if report["dry_run"] else "apply"
    print(f"notification-hub operator-review-session-retention: {report['status']}")
    print(f"- mode: {mode}")
    print(f"- report dir: {report['report_dir']}")
    print(f"- keep: {report['keep']}")
    print(f"- total reports: {report['total_count']}")
    print(f"- prune candidates: {report['candidate_count']}")
    print(f"- deleted: {report['deleted_count']}")
    if report["candidate_reports"]:
        print("- oldest candidates:")
        for item in report["candidate_reports"][:5]:
            print(f"  - {item['name']}")
    if report["error"] is not None:
        print(f"- error: {report['error']}")
    print(f"- next action: {report['next_action']}")


def _print_action_export_retention_report(report: ActionExportRetentionReport) -> None:
    mode = "dry run" if report["dry_run"] else "apply"
    print(f"notification-hub action-export-retention: {report['status']}")
    print(f"- mode: {mode}")
    print(f"- export dir: {report['export_dir']}")
    print(f"- keep: {report['keep']}")
    print(f"- total files: {report['total_count']}")
    print(f"- prune candidates: {report['candidate_count']}")
    print(f"- deleted: {report['deleted_count']}")
    if report["candidate_files"]:
        print("- oldest candidates:")
        for name in report["candidate_files"][:5]:
            print(f"  - {name}")
    if report["error"] is not None:
        print(f"- error: {report['error']}")
    print(f"- next action: {report['next_action']}")


def _print_action_package_validation_report(report: ActionPackageValidationReport) -> None:
    print(f"notification-hub validate-action-package: {report['status']}")
    print(f"- path: {report['path']}")
    print(f"- schema: {report['schema_version']}")
    print(f"- actions: {report['valid_action_count']}/{report['action_count']} valid")
    print(f"- warnings: {report['warning_count']}")
    print(f"- errors: {report['error_count']}")
    for warning in report["warnings"]:
        print(f"- warning: {warning}")
    for error in report["errors"]:
        print(f"- error: {error}")


def _print_personal_ops_import_report(report: PersonalOpsImportReport) -> None:
    print(f"notification-hub personal-ops-import: {report['status']}")
    print(f"- path: {report['path']}")
    print(f"- dry run: {report['dry_run']}")
    print(f"- applied: {report['applied']}")
    print(f"- enqueued: {report['enqueued']}")
    print(f"- queued actions: {report['queued_count']}")
    print(f"- skipped actions: {report['skipped_count']}")
    if report["queue_path"] is not None:
        print(f"- queue path: {report['queue_path']}")
    print(f"- valid actions: {report['validation']['valid_action_count']}")
    print(f"- next action: {report['next_action']}")
    if report["error"] is not None:
        print(f"- error: {report['error']}")


def _print_personal_ops_queue_report(report: dict[str, object]) -> None:
    health = cast(PersonalOpsImportQueueHealthReport, report["health"])
    print(f"notification-hub personal-ops-queue: {report['status']}")
    print(f"- queue path: {health['queue_path']}")
    print(f"- queued: {health['queued_count']}")
    print(f"- reviewed: {health['reviewed_count']}")
    print(f"- rejected: {health['rejected_count']}")
    print(f"- snoozed: {health['snoozed_count']}")
    print(f"- promoted: {health['promoted_count']}")
    print(f"- promoted pending: {health['promoted_pending_count']}")
    print(f"- promoted pending stale: {health['promoted_pending_stale_count']}")
    print(f"- promoted accepted: {health['promoted_accepted_count']}")
    print(f"- promoted rejected: {health['promoted_rejected_count']}")
    print(f"- next action: {health['next_action']}")
    update = cast(PersonalOpsImportQueueUpdateReport | None, report.get("update"))
    if update is not None:
        print(f"- updated: {update['updated']}")
        if update["error"] is not None:
            print(f"- error: {update['error']}")
    items = cast(list[PersonalOpsImportQueueItemReport], report.get("items") or [])
    if not items:
        print("- items: none")
        return
    print("- items:")
    for item in items:
        print(
            f"  - {item['queue_id']} [{item['status']}] "
            f"{item['priority']}/{item['state']}: {item['title']}"
        )
        print(f"    package: {item['source_package_name']}")
        if item["snoozed_until"] is not None:
            print(f"    snoozed until: {item['snoozed_until']}")
        if item["promotion_target"] is not None:
            print(f"    promotion target: {item['promotion_target']}")
        if item["promotion_target_id"] is not None:
            print(f"    promotion target id: {item['promotion_target_id']}")
        if item["promotion_outcome"] is not None:
            print(f"    promotion outcome: {item['promotion_outcome']}")


def _print_personal_ops_queue_health_report(
    report: PersonalOpsImportQueueHealthCheckReport,
) -> None:
    health = report["health"]
    print(f"notification-hub personal-ops-queue-health: {report['status']}")
    print(f"- queue path: {health['queue_path']}")
    print(f"- queued: {health['queued_count']}")
    print(f"- promoted pending: {health['promoted_pending_count']}")
    print(f"- promoted pending stale: {health['promoted_pending_stale_count']}")
    print(f"- promoted accepted: {health['promoted_accepted_count']}")
    print(f"- promoted rejected: {health['promoted_rejected_count']}")
    print(f"- next action: {health['next_action']}")
    if report["next_commands"]:
        print("- next commands:")
        for command in report["next_commands"]:
            print(f"  - {command}")
    if report["pending_promotion_items"]:
        print("- pending promotions:")
        for item in report["pending_promotion_items"]:
            target = item["promotion_target_id"] or "missing-target-id"
            print(f"  - {item['queue_id']} -> {target}: {item['title']}")
    if report["queued_items"]:
        print("- queued items:")
        for item in report["queued_items"]:
            print(f"  - {item['queue_id']}: {item['title']}")


def _print_personal_ops_queue_review_report(report: PersonalOpsQueueReviewReport) -> None:
    print(f"notification-hub personal-ops-queue-review: {report['status']}")
    print(f"- queued: {report['queued_count']}")
    print(f"- operator decisions: {report['operator_decision_count']}")
    print(f"- batches: {report['batch_count']}")
    print(f"- next action: {report['next_action']}")
    if report["next_commands"]:
        print("- next commands:")
        for command in report["next_commands"][:3]:
            print(f"  - {command}")
    if not report["batches"]:
        print("- batches: none")
        return
    print("- batches:")
    for batch in report["batches"]:
        print(f"  - {batch['item_count']}x {batch['title']} ({batch['priority']}/{batch['state']})")
        if batch["first_queue_id"] is not None:
            print(f"    first queue id: {batch['first_queue_id']}")
        for summary in batch["summaries"][:3]:
            print(f"    - {summary}")


def _print_personal_ops_outcome_sync_reminder_report(
    report: PersonalOpsOutcomeSyncReminderReport,
) -> None:
    print(f"notification-hub personal-ops-outcome-sync-reminder: {report['status']}")
    print(f"- should remind: {report['should_remind']}")
    print(f"- pending outcomes: {report['pending_count']}")
    print(f"- stale outcomes: {report['stale_count']}")
    print(f"- next action: {report['next_action']}")
    if report["next_commands"]:
        print("- next commands:")
        for command in report["next_commands"]:
            print(f"  - {command}")
    if not report["reminders"]:
        print("- reminders: none")
        return
    print("- reminders:")
    for item in report["reminders"]:
        target = item["promotion_target_id"] or "missing-target-id"
        outcome = item["promotion_outcome"] or "pending"
        print(f"  - {item['queue_id']} -> {target} [{outcome}]: {item['title']}")


def _print_personal_ops_queue_burn_in_report(report: PersonalOpsQueueBurnInReport) -> None:
    health = report["queue_health"]["health"]
    runtime_health = report["runtime_burn_in"]["health"]
    print(f"notification-hub personal-ops-queue-burn-in: {report['status']}")
    print(f"- ready for live promotion: {report['ready_for_live_promotion']}")
    print(f"- queue status: {health['status']}")
    print(f"- queued: {health['queued_count']}")
    print(f"- promoted pending: {health['promoted_pending_count']}")
    print(f"- promoted pending stale: {health['promoted_pending_stale_count']}")
    print(f"- scenario: {report['scenario']['status']}")
    print(f"- runtime health: {runtime_health['status']}")
    print(f"- outcome sync posture: {report['outcome_sync_posture']}")
    print(f"- next action: {report['next_action']}")
    report_file = report.get("report_file") or {}
    if report_file.get("requested"):
        print(f"- report file: {report_file.get('status')}")
        if report_file.get("path") is not None:
            print(f"- report path: {report_file.get('path')}")
    print("- operator steps:")
    for step in report["operator_steps"]:
        print(f"  - {step}")


def _print_personal_ops_queue_scenario_report(report: PersonalOpsQueueScenarioReport) -> None:
    print(f"notification-hub personal-ops-queue-scenario: {report['status']}")
    print(f"- queued: {report['queued_count']}")
    print(f"- queue id: {report['queue_id']}")
    print(f"- review status: {report['review_status']}")
    print(f"- promotion status: {report['promotion_status']}")
    print(f"- promotion outcome: {report['promotion_outcome']}")
    print(f"- final promoted accepted: {report['final_health']['promoted_accepted_count']}")
    print(f"- next action: {report['next_action']}")
    if report["error"] is not None:
        print(f"- error: {report['error']}")


def _print_logs_report(report: LogsReport) -> None:
    print(f"notification-hub logs: {report['status']}")
    print(f"- events log: {report['events_log']}")
    print(f"- stdout log: {report['stdout_log']}")
    print(f"- stderr log: {report['stderr_log']}")
    if report["missing_paths"]:
        print(f"- missing paths: {len(report['missing_paths'])}")
    if report["error"] is not None:
        print(f"- error: {report['error']}")

    summary = report["daemon_summary"]
    print("- daemon summary:")
    print(f"  accepted event posts: {summary['accepted_event_posts']}")
    print(f"  rejected event posts: {summary['rejected_event_posts']}")
    print(f"  validation errors: {summary['validation_error_count']}")
    print(f"  Slack delivery failures: {summary['slack_delivery_failure_count']}")
    if summary["access_status_counts"]:
        counts = ", ".join(
            f"{status}={count}" for status, count in sorted(summary["access_status_counts"].items())
        )
        print(f"  status counts: {counts}")

    print("- recent events:")
    for event in report["recent_events"]:
        project = f" ({event['project']})" if event["project"] else ""
        print(
            f"  - {event['timestamp']} [{event['level']}] {event['source']}{project}: {event['title']}"
        )

    print("- stdout tail:")
    for line in report["stdout_tail"]:
        print(f"  {line}")

    print("- stderr tail:")
    for line in report["stderr_tail"]:
        print(f"  {line}")


def _print_burn_in_report(report: BurnInReport) -> None:
    print(f"notification-hub burn-in: {report['status']}")
    print(f"- window: {report['minutes']} minutes")
    print(f"- events seen: {report['events_seen']}")
    print(f"- accepted event posts: {report['accepted_event_posts']}")
    print(f"- rejected event posts: {report['rejected_event_posts']}")
    print(f"- validation errors: {report['validation_error_count']}")
    print(f"- Slack delivery failures: {report['health']['slack_delivery_failure_count']}")
    print(f"- health: {report['health']['status']}")
    print(f"- Slack-eligible events: {report['slack_eligible_events']}")
    if report["error"] is not None:
        print(f"- error: {report['error']}")
    print("- noise candidates:")
    if not report["noise_candidates"]:
        print("  none")
    for item in report["noise_candidates"]:
        project = f" ({item['project']})" if item["project"] else ""
        print(f"  - x{item['count']} {item['source']}{project} [{item['level']}]: {item['title']}")
    print("- noise rule suggestions:")
    if not report["noise_rule_suggestions"]:
        print("  none")
    for suggestion in report["noise_rule_suggestions"]:
        print(f"  - {suggestion}")
    print("- Slack volume:")
    if not report["slack_volume"]:
        print("  none")
    for item in report["slack_volume"]:
        print(f"  - x{item['count']} {item['source']} [{item['level']}]")


def _print_verify_runtime_report(report: VerifyRuntimeReport) -> None:
    checks = report["checks"]
    smoke = report["smoke"]

    print(f"notification-hub verify-runtime: {report['status']}")
    print(f"- read only: {report['read_only']}")
    print(f"- health URL: {report['health_url']}")
    print(f"- doctor OK: {checks['doctor_ok']}")
    print(f"- policy check OK: {checks['policy_check_ok']}")
    print(f"- health details reachable: {checks['health_details_reachable']}")
    print(f"- runtime wiring current: {checks['runtime_wiring_current']}")
    print(f"- recent runtime health OK: {checks['recent_runtime_health_ok']}")
    print(f"- delivery check OK: {checks['delivery_check_ok']}")
    print(f"- import queue queued: {report['import_queue']['queued_count']}")
    print(f"- smoke included: {report['include_smoke']}")
    delivery_check = report["delivery_check"]
    if delivery_check is not None:
        print(f"- delivery check event ID: {delivery_check['event_id']}")
        print(f"- Slack OK: {delivery_check['slack_ok']}")
        print(f"- push OK: {delivery_check['push_ok']}")
    if smoke is not None:
        print(f"- smoke OK: {checks['smoke_ok']}")
        print(f"- smoke event ID: {smoke['event_id']}")


def _print_policy_check_report(report: PolicyCheckReport) -> None:
    drift = report["policy_drift"]
    print(f"notification-hub policy-check: {report['status']}")
    print(f"- config found: {report['config_found']}")
    print(f"- config path: {report['config_path']}")
    print(f"- sample path: {report['example_path']}")
    print(f"- warning count: {report['warning_count']}")
    print(f"- suggestion count: {report['suggestion_count']}")
    print(f"- policy drift: {drift['status']}")
    print(f"- missing sample noise rules: {drift['missing_sample_noise_rule_count']}")
    print(f"- extra live noise rules: {drift['extra_live_noise_rule_count']}")
    if report["load_error"] is not None:
        print(f"- load error: {report['load_error']}")
    if drift["error"] is not None:
        print(f"- drift error: {drift['error']}")
    if drift["status"] != "ok":
        print(f"- drift next action: {drift['next_action']}")
    for rule in drift["missing_sample_noise_rules"][:3]:
        print(f"- missing sample noise rule: {rule}")
    for warning, suggestion in zip(report["warnings"], report["suggestions"], strict=False):
        print(f"- warning: {warning}")
        print(f"- suggestion: {suggestion}")


def _print_explain_report(report: dict[str, object]) -> None:
    event = report["event"]
    classification = report["classification"]
    routing = report["routing"]
    delivery = report["delivery"]
    assert isinstance(event, dict)
    assert isinstance(classification, dict)
    assert isinstance(routing, dict)
    assert isinstance(delivery, dict)

    print("notification-hub explain: ok")
    print(f"- source: {event['source']}")
    print(f"- input level: {classification['input_level']}")
    print(f"- classified level: {classification['output_level']}")
    print(f"- classification reason: {classification['reason']}")
    if classification["matched_keyword"] is not None:
        print(f"- matched keyword: {classification['matched_keyword']}")
    print(f"- routing reason: {routing['reason']}")
    print(f"- final level: {routing['final_level']}")
    matched_rule_indices = cast(list[object], routing["matched_rule_indices"])
    if matched_rule_indices:
        print(f"- matched rules: {matched_rule_indices}")
    print(f"- push would send: {delivery['push']}")
    print(f"- slack would send: {delivery['slack']}")


def _print_retention_report(report: RetentionReport) -> None:
    print(f"notification-hub retention: {report['status']}")
    print(f"- rotated: {report['rotated']}")
    print(f"- events before: {report['events_before']}")
    print(f"- events after: {report['events_after']}")
    print(f"- archived events: {report['archived_events']}")
    if report["archive_path"] is not None:
        print(f"- archive path: {report['archive_path']}")
    deleted_archives = report["deleted_archives"]
    if deleted_archives:
        print(f"- deleted archives: {len(deleted_archives)}")


def _print_bootstrap_report(report: BootstrapConfigReport) -> None:
    print(f"notification-hub bootstrap-config: {report['status']}")
    print(f"- copied: {report['copied']}")
    print(f"- sample path: {report['example_path']}")
    print(f"- config path: {report['config_path']}")
    if report["error"] is not None:
        print(f"- error: {report['error']}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        report = collect_doctor_report()
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_doctor_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "smoke":
        report = run_smoke_check()
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_smoke_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "status":
        report = run_status()
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_status_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "logs":
        report = run_logs(events=args.events, lines=args.lines)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_logs_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "burn-in":
        report = run_burn_in(minutes=args.minutes, lines=args.lines)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_burn_in_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "verify-runtime":
        report = run_verify_runtime(
            include_smoke=args.include_smoke,
            verify_slack=args.verify_slack,
            verify_push=args.verify_push,
        )
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_verify_runtime_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "delivery-check":
        if not args.slack and not args.push:
            parser.error("delivery-check requires --slack and/or --push")
        report = run_delivery_check(verify_slack=args.slack, verify_push=args.push)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_delivery_check_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "inbox":
        report = run_inbox(hours=args.hours, limit=args.limit)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_inbox_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "coordination-snapshot":
        report = run_coordination_snapshot(
            hours=args.hours,
            limit=args.limit,
            save_bridge_db=args.save_bridge_db,
            bridge_db_path=Path(args.bridge_db_path).expanduser() if args.bridge_db_path else None,
        )
        if args.output is not None:
            _write_json_report(report, args.output)
        elif args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_coordination_snapshot_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "coordination-readiness":
        report = run_coordination_readiness(limit=args.limit)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_coordination_readiness_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "coordination-console":
        report = run_coordination_console(hours=args.hours, limit=args.limit)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_coordination_console_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "personal-ops-actions":
        report = run_personal_ops_action_export(
            hours=args.hours,
            limit=args.limit,
            save_review_package=args.save_review_package,
            review_dir=Path(args.review_dir).expanduser() if args.review_dir else None,
        )
        if args.output is not None:
            _write_json_report(report, args.output)
        elif args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_personal_ops_action_export_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "validate-action-package":
        report = validate_action_package(Path(args.path))
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_action_package_validation_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "action-proposal-dismiss":
        actions = run_personal_ops_action_export(hours=24, limit=100, include_dismissed=True)
        matched = next(
            (
                action
                for action in actions["actions"]
                if action["dismissal_key"] == args.dismissal_key
            ),
            None,
        )
        report = dismiss_action_proposal(
            dismissal_key=args.dismissal_key,
            reason=args.reason,
            source=matched["source"] if matched is not None else None,
            project=matched["project"] if matched is not None else None,
            intent=matched["intent"] if matched is not None else None,
            title=matched["title"] if matched is not None else None,
            body=matched["signal_body"] if matched is not None else None,
            evidence_event_id=matched["evidence_event_id"] if matched is not None else None,
        )
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_action_proposal_dismiss_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "action-proposal-dismissals":
        report = run_action_proposal_dismissal_list(
            limit=args.limit,
            dismissal_key=args.dismissal_key,
            include_inactive=args.include_inactive,
        )
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_action_proposal_dismissal_list_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "action-proposal-undismiss":
        report = undismiss_action_proposal(
            dismissal_key=args.dismissal_key,
            reason=args.reason,
        )
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_action_proposal_undismiss_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "action-proposal-group-outcome":
        report = record_action_proposal_group_outcome(
            group_key=args.group_key,
            outcome=args.outcome,
            reason=args.reason,
            hours=args.hours,
            limit=args.limit,
        )
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_action_proposal_group_outcome_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "operator-daily-state":
        report = run_operator_daily_state(
            hours=args.hours,
            limit=args.limit,
            save_report=args.save_report,
            report_dir=Path(args.report_dir).expanduser() if args.report_dir else None,
        )
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_operator_daily_state_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "operator-review-session":
        report = run_operator_review_session(
            hours=args.hours,
            limit=args.limit,
            save_report=args.save_report,
            report_dir=Path(args.report_dir).expanduser() if args.report_dir else None,
        )
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_operator_review_session_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "action-export-retention":
        report = prune_action_export_files(
            keep=args.keep,
            dry_run=not args.apply,
            export_dir=Path(args.export_dir).expanduser() if args.export_dir else None,
        )
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_action_export_retention_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "operator-review-session-retention":
        report = prune_operator_review_session_reports(
            keep=args.keep,
            dry_run=not args.apply,
            report_dir=Path(args.report_dir).expanduser() if args.report_dir else None,
        )
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_operator_review_session_retention_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "operator-handoff-drill":
        report = run_operator_handoff_drill(
            save_burn_in_report=args.save_burn_in_report,
            report_dir=Path(args.report_dir).expanduser() if args.report_dir else None,
        )
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_operator_handoff_drill_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "personal-ops-import":
        report = run_personal_ops_import_stub(
            path=Path(args.path),
            dry_run=args.dry_run,
            enqueue=args.enqueue,
            queue_path=Path(args.queue_path).expanduser() if args.queue_path else None,
        )
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_personal_ops_import_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "personal-ops-queue":
        queue_path = Path(args.queue_path).expanduser() if args.queue_path else None
        update = None
        if args.queue_id is not None or args.status is not None:
            if args.queue_id is None or args.status is None:
                print("--queue-id and --status must be provided together", file=sys.stderr)
                return 2
            update = update_personal_ops_import_queue_item(
                queue_id=args.queue_id,
                status=args.status,
                reason=args.reason,
                snoozed_until=args.snoozed_until,
                promotion_target=args.promotion_target,
                promotion_target_id=args.promotion_target_id,
                promotion_outcome=args.promotion_outcome,
                promotion_outcome_note=args.promotion_outcome_note,
                queue_path=queue_path,
            )
        queue_report: dict[str, object] = {
            "status": update["status"] if update is not None else "ok",
            "health": summarize_personal_ops_import_queue(queue_path=queue_path),
            "items": list_personal_ops_import_queue(queue_path=queue_path, limit=args.limit),
            "update": update,
        }
        if args.json:
            print(json.dumps(queue_report, indent=2, sort_keys=True))
        else:
            _print_personal_ops_queue_report(queue_report)
        return 0 if queue_report["status"] == "ok" else 1

    if args.command == "personal-ops-queue-scenario":
        report = run_personal_ops_queue_scenario()
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_personal_ops_queue_scenario_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "personal-ops-queue-health":
        report = run_personal_ops_import_queue_health_check(
            queue_path=Path(args.queue_path).expanduser() if args.queue_path else None,
            limit=args.limit,
            stale_after_hours=args.stale_after_hours,
        )
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_personal_ops_queue_health_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "personal-ops-queue-review":
        report = run_personal_ops_queue_review(
            queue_path=Path(args.queue_path).expanduser() if args.queue_path else None,
            limit=args.limit,
            stale_after_hours=args.stale_after_hours,
        )
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_personal_ops_queue_review_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "personal-ops-queue-burn-in":
        report = run_personal_ops_queue_burn_in(
            minutes=args.minutes,
            lines=args.lines,
            limit=args.limit,
            save_report=args.save_report,
            report_dir=Path(args.report_dir).expanduser() if args.report_dir else None,
        )
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_personal_ops_queue_burn_in_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "personal-ops-outcome-sync-reminder":
        report = run_personal_ops_outcome_sync_reminder(
            queue_path=Path(args.queue_path).expanduser() if args.queue_path else None,
            limit=args.limit,
            stale_after_hours=args.stale_after_hours,
        )
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_personal_ops_outcome_sync_reminder_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "policy-check":
        report = run_policy_check()
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_policy_check_report(report)
        return 0 if report["status"] != "degraded" else 1

    if args.command == "explain":
        event = Event(
            source=args.source,
            level=args.level,
            title=args.title,
            body=args.body,
            project=args.project,
        )
        report = build_event_explanation_report(event)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_explain_report(report)
        return 0

    if args.command == "bootstrap-config":
        report = bootstrap_policy_config(force=args.force)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_bootstrap_report(report)
        return 0 if report["status"] == "ok" else 1

    report = run_retention(max_events=args.max_events, keep_archives=args.keep_archives)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_retention_report(report)
    return 0 if report["status"] == "ok" else 1


def doctor_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["doctor", *forwarded])


def smoke_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["smoke", *forwarded])


def status_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["status", *forwarded])


def logs_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["logs", *forwarded])


def burn_in_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["burn-in", *forwarded])


def verify_runtime_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["verify-runtime", *forwarded])


def delivery_check_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["delivery-check", *forwarded])


def inbox_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["inbox", *forwarded])


def coordination_snapshot_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["coordination-snapshot", *forwarded])


def coordination_readiness_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["coordination-readiness", *forwarded])


def coordination_console_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["coordination-console", *forwarded])


def personal_ops_actions_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["personal-ops-actions", *forwarded])


def validate_action_package_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["validate-action-package", *forwarded])


def action_proposal_dismiss_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["action-proposal-dismiss", *forwarded])


def action_proposal_dismissals_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["action-proposal-dismissals", *forwarded])


def action_proposal_undismiss_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["action-proposal-undismiss", *forwarded])


def operator_daily_state_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["operator-daily-state", *forwarded])


def operator_review_session_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["operator-review-session", *forwarded])


def operator_review_session_retention_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["operator-review-session-retention", *forwarded])


def action_export_retention_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["action-export-retention", *forwarded])


def operator_handoff_drill_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["operator-handoff-drill", *forwarded])


def personal_ops_import_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["personal-ops-import", *forwarded])


def personal_ops_queue_health_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["personal-ops-queue-health", *forwarded])


def personal_ops_queue_burn_in_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["personal-ops-queue-burn-in", *forwarded])


def personal_ops_outcome_sync_reminder_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["personal-ops-outcome-sync-reminder", *forwarded])


def policy_check_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["policy-check", *forwarded])


def explain_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["explain", *forwarded])


def retention_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["retention", *forwarded])


def bootstrap_config_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["bootstrap-config", *forwarded])
