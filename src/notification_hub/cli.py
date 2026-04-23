"""Operator-facing command entrypoints."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import cast

from notification_hub.diagnostics import collect_doctor_report
from notification_hub.models import Event
from notification_hub.operations import (
    BootstrapConfigReport,
    PolicyCheckReport,
    RetentionReport,
    SmokeReport,
    VerifyRuntimeReport,
    bootstrap_policy_config,
    run_policy_check,
    run_retention,
    run_smoke_check,
    run_verify_runtime,
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
    print(f"- smoke included: {report['include_smoke']}")
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

    if args.command == "verify-runtime":
        report = run_verify_runtime(include_smoke=args.include_smoke)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_verify_runtime_report(report)
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


def verify_runtime_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["verify-runtime", *forwarded])


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
