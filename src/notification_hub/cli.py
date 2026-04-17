"""Operator-facing command entrypoints."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from notification_hub.diagnostics import collect_doctor_report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="notification-hub-doctor")
    parser.add_argument(
        "command",
        nargs="?",
        default="doctor",
        choices=("doctor",),
        help="Run the built-in doctor checks.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the doctor report as JSON.",
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    report = collect_doctor_report()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_doctor_report(report)

    return 0 if report["status"] == "ok" else 1
