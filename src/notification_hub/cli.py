"""Operator-facing command entrypoints."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from notification_hub.diagnostics import collect_doctor_report
from notification_hub.models import Event
from notification_hub.operations import (
    ACTION_PROPOSAL_REVIEW_WINDOW_HOURS,
    bootstrap_policy_config,
    dismiss_action_proposal,
    list_personal_ops_import_queue,
    prune_action_export_files,
    prune_operator_review_session_reports,
    record_action_proposal_group_outcome,
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
    run_policy_check,
    run_retention,
    run_smoke_check,
    run_status,
    run_verify_runtime,
    summarize_personal_ops_import_queue,
    undismiss_action_proposal,
    update_personal_ops_import_queue_item,
    validate_action_package,
)
from notification_hub.cli_reports import (
    print_action_export_retention_report,
    print_action_package_validation_report,
    print_action_proposal_dismiss_report,
    print_action_proposal_dismissal_list_report,
    print_action_proposal_group_outcome_report,
    print_action_proposal_undismiss_report,
    print_bootstrap_report,
    print_burn_in_report,
    print_coordination_console_report,
    print_coordination_readiness_report,
    print_coordination_snapshot_report,
    print_delivery_check_report,
    print_doctor_report,
    print_explain_report,
    print_inbox_report,
    print_logs_report,
    print_operator_daily_state_report,
    print_operator_handoff_drill_report,
    print_operator_review_session_report,
    print_operator_review_session_retention_report,
    print_personal_ops_action_export_report,
    print_personal_ops_import_report,
    print_personal_ops_outcome_sync_reminder_report,
    print_personal_ops_queue_burn_in_report,
    print_personal_ops_queue_health_report,
    print_personal_ops_queue_report,
    print_personal_ops_queue_review_report,
    print_personal_ops_queue_scenario_report,
    print_policy_check_report,
    print_retention_report,
    print_smoke_report,
    print_status_report,
    print_verify_runtime_report,
    write_json_report,
)
from notification_hub.pipeline import build_event_explanation_report


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
        default=ACTION_PROPOSAL_REVIEW_WINDOW_HOURS,
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        report = collect_doctor_report()
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print_doctor_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "smoke":
        report = run_smoke_check()
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print_smoke_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "status":
        report = run_status()
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print_status_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "logs":
        report = run_logs(events=args.events, lines=args.lines)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print_logs_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "burn-in":
        report = run_burn_in(minutes=args.minutes, lines=args.lines)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print_burn_in_report(report)
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
            print_verify_runtime_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "delivery-check":
        if not args.slack and not args.push:
            parser.error("delivery-check requires --slack and/or --push")
        report = run_delivery_check(verify_slack=args.slack, verify_push=args.push)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print_delivery_check_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "inbox":
        report = run_inbox(hours=args.hours, limit=args.limit)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print_inbox_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "coordination-snapshot":
        report = run_coordination_snapshot(
            hours=args.hours,
            limit=args.limit,
            save_bridge_db=args.save_bridge_db,
            bridge_db_path=Path(args.bridge_db_path).expanduser() if args.bridge_db_path else None,
        )
        if args.output is not None:
            write_json_report(report, args.output)
        elif args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print_coordination_snapshot_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "coordination-readiness":
        report = run_coordination_readiness(limit=args.limit)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print_coordination_readiness_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "coordination-console":
        report = run_coordination_console(hours=args.hours, limit=args.limit)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print_coordination_console_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "personal-ops-actions":
        report = run_personal_ops_action_export(
            hours=args.hours,
            limit=args.limit,
            save_review_package=args.save_review_package,
            review_dir=Path(args.review_dir).expanduser() if args.review_dir else None,
        )
        if args.output is not None:
            write_json_report(report, args.output)
        elif args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print_personal_ops_action_export_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "validate-action-package":
        report = validate_action_package(Path(args.path))
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print_action_package_validation_report(report)
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
            print_action_proposal_dismiss_report(report)
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
            print_action_proposal_dismissal_list_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "action-proposal-undismiss":
        report = undismiss_action_proposal(
            dismissal_key=args.dismissal_key,
            reason=args.reason,
        )
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print_action_proposal_undismiss_report(report)
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
            print_action_proposal_group_outcome_report(report)
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
            print_operator_daily_state_report(report)
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
            print_operator_review_session_report(report)
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
            print_action_export_retention_report(report)
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
            print_operator_review_session_retention_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "operator-handoff-drill":
        report = run_operator_handoff_drill(
            save_burn_in_report=args.save_burn_in_report,
            report_dir=Path(args.report_dir).expanduser() if args.report_dir else None,
        )
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print_operator_handoff_drill_report(report)
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
            print_personal_ops_import_report(report)
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
            print_personal_ops_queue_report(queue_report)
        return 0 if queue_report["status"] == "ok" else 1

    if args.command == "personal-ops-queue-scenario":
        report = run_personal_ops_queue_scenario()
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print_personal_ops_queue_scenario_report(report)
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
            print_personal_ops_queue_health_report(report)
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
            print_personal_ops_queue_review_report(report)
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
            print_personal_ops_queue_burn_in_report(report)
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
            print_personal_ops_outcome_sync_reminder_report(report)
        return 0 if report["status"] == "ok" else 1

    if args.command == "policy-check":
        report = run_policy_check()
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print_policy_check_report(report)
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
            print_explain_report(report)
        return 0

    if args.command == "bootstrap-config":
        report = bootstrap_policy_config(force=args.force)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print_bootstrap_report(report)
        return 0 if report["status"] == "ok" else 1

    report = run_retention(max_events=args.max_events, keep_archives=args.keep_archives)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_retention_report(report)
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
