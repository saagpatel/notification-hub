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
    CoordinationSnapshotReport,
    DeliveryCheckReport,
    InboxReport,
    LogsReport,
    PersonalOpsActionExportReport,
    PersonalOpsImportReport,
    ActionPackageValidationReport,
    PolicyCheckReport,
    RetentionReport,
    SmokeReport,
    StatusReport,
    VerifyRuntimeReport,
    bootstrap_policy_config,
    run_burn_in,
    run_coordination_snapshot,
    run_delivery_check,
    run_inbox,
    run_logs,
    run_personal_ops_action_export,
    run_personal_ops_import_stub,
    validate_action_package,
    run_policy_check,
    run_retention,
    run_smoke_check,
    run_status,
    run_verify_runtime,
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
        "--json",
        action="store_true",
        help="Emit the import report as JSON.",
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


def _print_personal_ops_action_export_report(report: PersonalOpsActionExportReport) -> None:
    print(f"notification-hub personal-ops-actions: {report['status']}")
    print(f"- schema: {report['schema_version']}")
    print(f"- window: {report['hours']} hours")
    print(f"- actions: {len(report['actions'])}")
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
    print(f"- valid actions: {report['validation']['valid_action_count']}")
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
        print(f"  - {event['timestamp']} [{event['level']}] {event['source']}{project}: {event['title']}")

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
        print(
            f"  - x{item['count']} {item['source']}{project} "
            f"[{item['level']}]: {item['title']}"
        )
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
    print(f"notification-hub policy-check: {report['status']}")
    print(f"- config found: {report['config_found']}")
    print(f"- config path: {report['config_path']}")
    print(f"- sample path: {report['example_path']}")
    print(f"- warning count: {report['warning_count']}")
    print(f"- suggestion count: {report['suggestion_count']}")
    if report["load_error"] is not None:
        print(f"- load error: {report['load_error']}")
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

    if args.command == "personal-ops-import":
        report = run_personal_ops_import_stub(path=Path(args.path), dry_run=args.dry_run)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_personal_ops_import_report(report)
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


def personal_ops_actions_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["personal-ops-actions", *forwarded])


def validate_action_package_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["validate-action-package", *forwarded])


def personal_ops_import_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["personal-ops-import", *forwarded])


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
