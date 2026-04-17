"""Operator-facing command entrypoints."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from notification_hub.diagnostics import collect_doctor_report
from notification_hub.operations import (
    BootstrapConfigReport,
    RetentionReport,
    SmokeReport,
    bootstrap_policy_config,
    run_retention,
    run_smoke_check,
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
    assert isinstance(checks, dict)
    assert isinstance(config, dict)
    assert isinstance(local_api, dict)

    print(f"notification-hub doctor: {report['status']}")
    print(f"- local API healthy: {checks['local_api_healthy']}")
    print(f"- LaunchAgent present: {checks['launch_agent_present']}")
    print(f"- bridge file present: {checks['bridge_file_present']}")
    print(f"- push notifier available: {checks['push_notifier_available']}")
    print(f"- Slack configured: {checks['slack_configured']}")
    print(f"- policy load OK: {checks['policy_load_ok']}")
    print(f"- policy path: {config['path']}")
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


def retention_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["retention", *forwarded])


def bootstrap_config_main(argv: Sequence[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main(["bootstrap-config", *forwarded])
