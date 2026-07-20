"""Operator-facing command entrypoints."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast

from notification_hub.cli_parser import build_parser
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
    print_delivery_check_finalization_report,
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
    print_reconciliation_report,
    print_retention_report,
    print_smoke_report,
    print_status_report,
    print_verify_runtime_report,
    write_json_report,
)
from notification_hub.delivery_check import finalize_delivery_check_claim
from notification_hub.delivery_check_supersession import (
    DeliveryCheckSupersessionAuthorizationError,
    apply_delivery_check_receipt_supersession_plan,
    build_delivery_check_receipt_supersession_plan,
    finalize_delivery_check_receipt_supersession_claim,
)
from notification_hub.diagnostics import collect_doctor_report
from notification_hub.models import Event
from notification_hub.operations import (
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
from notification_hub.pipeline import build_event_explanation_report
from notification_hub.reconciliation import (
    ReconciliationAuthorizationError,
    apply_reconciliation_plan,
    apply_reconciliation_receipt_supersession_plan,
    build_reconciliation_plan,
    build_reconciliation_receipt_supersession_plan,
    finalize_reconciliation_claim,
    load_readback_file,
)


def _emit_report(
    report: Any,
    *,
    json_output: bool,
    print_report: Callable[[Any], None],
    output_path: str | None = None,
    success_status: str | None = "ok",
) -> int:
    if output_path is not None:
        write_json_report(report, output_path)
    elif json_output:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_report(report)
    if success_status is None:
        return 0
    return 0 if report["status"] == success_status else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        report = collect_doctor_report()
        return _emit_report(report, json_output=args.json, print_report=print_doctor_report)

    if args.command == "smoke":
        report = run_smoke_check()
        return _emit_report(report, json_output=args.json, print_report=print_smoke_report)

    if args.command == "status":
        report = run_status()
        return _emit_report(report, json_output=args.json, print_report=print_status_report)

    if args.command == "logs":
        report = run_logs(events=args.events, lines=args.lines)
        return _emit_report(report, json_output=args.json, print_report=print_logs_report)

    if args.command == "burn-in":
        report = run_burn_in(minutes=args.minutes, lines=args.lines)
        return _emit_report(report, json_output=args.json, print_report=print_burn_in_report)

    if args.command == "verify-runtime":
        report = run_verify_runtime(
            include_smoke=args.include_smoke,
            verify_slack=args.verify_slack,
            verify_push=args.verify_push,
        )
        return _emit_report(report, json_output=args.json, print_report=print_verify_runtime_report)

    if args.command == "delivery-check":
        if not args.slack and not args.push:
            parser.error("delivery-check requires --slack and/or --push")
        if args.apply and (args.envelope is None or args.claim_state_dir is None):
            print(
                "--apply requires --envelope and --claim-state-dir",
                file=sys.stderr,
            )
            return 2
        try:
            report = run_delivery_check(
                verify_slack=args.slack,
                verify_push=args.push,
                apply=args.apply,
                envelope_path=Path(args.envelope) if args.envelope else None,
                claim_state_dir=(
                    Path(args.claim_state_dir) if args.claim_state_dir else None
                ),
            )
        except (OSError, ValueError) as exc:
            print(f"delivery-check failed: {exc}", file=sys.stderr)
            return 2
        return _emit_report(
            report,
            json_output=args.json,
            print_report=print_delivery_check_report,
            success_status=None,
        )

    if args.command == "finalize-delivery-check-claim":
        try:
            plan = load_readback_file(Path(args.plan_json))
            report = finalize_delivery_check_claim(
                plan,
                envelope_path=Path(args.envelope),
                claim_state_dir=Path(args.claim_state_dir),
            )
        except (OSError, ValueError) as exc:
            print(f"finalize-delivery-check-claim failed: {exc}", file=sys.stderr)
            return 2
        return _emit_report(
            report,
            json_output=args.json,
            print_report=print_delivery_check_finalization_report,
            success_status=None,
        )

    if args.command == "supersede-delivery-check-receipt":
        if args.apply and args.envelope is None:
            print("--apply requires --envelope", file=sys.stderr)
            return 2
        try:
            plan = build_delivery_check_receipt_supersession_plan(
                original_plan_path=Path(args.original_plan_json),
                original_receipt_path=Path(args.original_receipt_json),
                provider_readback_path=Path(args.provider_readback_json),
                receipt_state_dir=Path(args.claim_state_dir),
            )
            if args.apply:
                report = apply_delivery_check_receipt_supersession_plan(
                    plan,
                    envelope_path=Path(args.envelope),
                    claim_state_dir=Path(args.claim_state_dir),
                )
            else:
                report = {"status": "planned", "plan": plan}
        except (
            OSError,
            ValueError,
            DeliveryCheckSupersessionAuthorizationError,
        ) as exc:
            print(f"supersede-delivery-check-receipt failed: {exc}", file=sys.stderr)
            return 2
        return _emit_report(
            report,
            json_output=args.json,
            print_report=print_reconciliation_report,
            success_status=None,
        )

    if args.command == "finalize-delivery-check-supersession-claim":
        try:
            plan = load_readback_file(Path(args.plan_json))
            report = finalize_delivery_check_receipt_supersession_claim(
                plan,
                envelope_path=Path(args.envelope),
                claim_state_dir=Path(args.claim_state_dir),
            )
        except (
            OSError,
            ValueError,
            DeliveryCheckSupersessionAuthorizationError,
        ) as exc:
            print(
                f"finalize-delivery-check-supersession-claim failed: {exc}",
                file=sys.stderr,
            )
            return 2
        return _emit_report(
            report,
            json_output=args.json,
            print_report=print_reconciliation_report,
            success_status=None,
        )

    if args.command == "finalize-reconciliation-claim":
        try:
            plan = load_readback_file(Path(args.plan_json))
            report = finalize_reconciliation_claim(
                plan,
                envelope_path=Path(args.envelope),
                claim_state_dir=Path(args.claim_state_dir),
            )
        except (OSError, ValueError, ReconciliationAuthorizationError) as exc:
            print(f"finalize-reconciliation-claim failed: {exc}", file=sys.stderr)
            return 2
        _emit_report(
            report,
            json_output=args.json,
            print_report=print_reconciliation_report,
            success_status=None,
        )
        return 0

    if args.command == "supersede-reconciliation-receipt":
        try:
            original_plan = load_readback_file(Path(args.original_plan_json))
            plan = build_reconciliation_receipt_supersession_plan(
                original_plan,
                original_receipt_path=Path(args.original_receipt_json),
            )
            if args.apply:
                if args.envelope is None or args.claim_state_dir is None:
                    print(
                        "--apply requires --envelope and --claim-state-dir",
                        file=sys.stderr,
                    )
                    return 2
                report = apply_reconciliation_receipt_supersession_plan(
                    plan,
                    envelope_path=Path(args.envelope),
                    claim_state_dir=Path(args.claim_state_dir),
                )
            else:
                report = {"status": "planned", "plan": plan}
        except (OSError, ValueError, ReconciliationAuthorizationError) as exc:
            print(f"supersede-reconciliation-receipt failed: {exc}", file=sys.stderr)
            return 2
        _emit_report(
            report,
            json_output=args.json,
            print_report=print_reconciliation_report,
            success_status=None,
        )
        return 0

    if args.command == "reconcile-delivery":
        try:
            readback = load_readback_file(Path(args.readback_json))
            plan = build_reconciliation_plan(
                args.event_id,
                args.channel,
                terminal_outcome=args.terminal_outcome,
                provider_reference=args.provider_reference,
                readback_result=readback,
                database_path=Path(args.db_path),
                receipt_state_dir=(
                    Path(args.claim_state_dir)
                    if args.claim_state_dir is not None
                    else None
                ),
            )
            if args.apply:
                if args.envelope is None or args.claim_state_dir is None:
                    print(
                        "--apply requires --envelope and --claim-state-dir",
                        file=sys.stderr,
                    )
                    return 2
            if args.apply:
                report = apply_reconciliation_plan(
                    plan,
                    envelope_path=Path(cast(str, args.envelope)),
                    claim_state_dir=Path(cast(str, args.claim_state_dir)),
                )
            else:
                report = {"status": "planned", "plan": plan}
        except (OSError, ValueError, ReconciliationAuthorizationError) as exc:
            print(f"reconcile-delivery failed: {exc}", file=sys.stderr)
            return 2
        _emit_report(
            report,
            json_output=args.json,
            print_report=print_reconciliation_report,
            success_status=None,
        )
        return 0

    if args.command == "inbox":
        report = run_inbox(hours=args.hours, limit=args.limit)
        return _emit_report(report, json_output=args.json, print_report=print_inbox_report)

    if args.command == "coordination-snapshot":
        report = run_coordination_snapshot(
            hours=args.hours,
            limit=args.limit,
            save_bridge_db=args.save_bridge_db,
            bridge_db_path=Path(args.bridge_db_path).expanduser() if args.bridge_db_path else None,
        )
        return _emit_report(
            report,
            json_output=args.json,
            print_report=print_coordination_snapshot_report,
            output_path=args.output,
        )

    if args.command == "coordination-readiness":
        report = run_coordination_readiness(limit=args.limit)
        return _emit_report(
            report,
            json_output=args.json,
            print_report=print_coordination_readiness_report,
        )

    if args.command == "coordination-console":
        report = run_coordination_console(hours=args.hours, limit=args.limit)
        return _emit_report(
            report,
            json_output=args.json,
            print_report=print_coordination_console_report,
        )

    if args.command == "personal-ops-actions":
        report = run_personal_ops_action_export(
            hours=args.hours,
            limit=args.limit,
            save_review_package=args.save_review_package,
            review_dir=Path(args.review_dir).expanduser() if args.review_dir else None,
        )
        return _emit_report(
            report,
            json_output=args.json,
            print_report=print_personal_ops_action_export_report,
            output_path=args.output,
        )

    if args.command == "validate-action-package":
        report = validate_action_package(Path(args.path))
        return _emit_report(
            report,
            json_output=args.json,
            print_report=print_action_package_validation_report,
        )

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
        return _emit_report(
            report,
            json_output=args.json,
            print_report=print_action_proposal_dismiss_report,
        )

    if args.command == "action-proposal-dismissals":
        report = run_action_proposal_dismissal_list(
            limit=args.limit,
            dismissal_key=args.dismissal_key,
            include_inactive=args.include_inactive,
        )
        return _emit_report(
            report,
            json_output=args.json,
            print_report=print_action_proposal_dismissal_list_report,
        )

    if args.command == "action-proposal-undismiss":
        report = undismiss_action_proposal(
            dismissal_key=args.dismissal_key,
            reason=args.reason,
        )
        return _emit_report(
            report,
            json_output=args.json,
            print_report=print_action_proposal_undismiss_report,
        )

    if args.command == "action-proposal-group-outcome":
        report = record_action_proposal_group_outcome(
            group_key=args.group_key,
            outcome=args.outcome,
            reason=args.reason,
            hours=args.hours,
            limit=args.limit,
        )
        return _emit_report(
            report,
            json_output=args.json,
            print_report=print_action_proposal_group_outcome_report,
        )

    if args.command == "operator-daily-state":
        report = run_operator_daily_state(
            hours=args.hours,
            limit=args.limit,
            save_report=args.save_report,
            report_dir=Path(args.report_dir).expanduser() if args.report_dir else None,
        )
        return _emit_report(
            report,
            json_output=args.json,
            print_report=print_operator_daily_state_report,
        )

    if args.command == "operator-review-session":
        report = run_operator_review_session(
            hours=args.hours,
            limit=args.limit,
            save_report=args.save_report,
            report_dir=Path(args.report_dir).expanduser() if args.report_dir else None,
        )
        return _emit_report(
            report,
            json_output=args.json,
            print_report=print_operator_review_session_report,
        )

    if args.command == "action-export-retention":
        report = prune_action_export_files(
            keep=args.keep,
            dry_run=not args.apply,
            export_dir=Path(args.export_dir).expanduser() if args.export_dir else None,
        )
        return _emit_report(
            report,
            json_output=args.json,
            print_report=print_action_export_retention_report,
        )

    if args.command == "operator-review-session-retention":
        report = prune_operator_review_session_reports(
            keep=args.keep,
            dry_run=not args.apply,
            report_dir=Path(args.report_dir).expanduser() if args.report_dir else None,
        )
        return _emit_report(
            report,
            json_output=args.json,
            print_report=print_operator_review_session_retention_report,
        )

    if args.command == "operator-handoff-drill":
        report = run_operator_handoff_drill(
            save_burn_in_report=args.save_burn_in_report,
            report_dir=Path(args.report_dir).expanduser() if args.report_dir else None,
        )
        return _emit_report(
            report,
            json_output=args.json,
            print_report=print_operator_handoff_drill_report,
        )

    if args.command == "personal-ops-import":
        report = run_personal_ops_import_stub(
            path=Path(args.path),
            dry_run=args.dry_run,
            enqueue=args.enqueue,
            queue_path=Path(args.queue_path).expanduser() if args.queue_path else None,
        )
        return _emit_report(
            report,
            json_output=args.json,
            print_report=print_personal_ops_import_report,
        )

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
        return _emit_report(
            queue_report,
            json_output=args.json,
            print_report=print_personal_ops_queue_report,
        )

    if args.command == "personal-ops-queue-scenario":
        report = run_personal_ops_queue_scenario()
        return _emit_report(
            report,
            json_output=args.json,
            print_report=print_personal_ops_queue_scenario_report,
        )

    if args.command == "personal-ops-queue-health":
        report = run_personal_ops_import_queue_health_check(
            queue_path=Path(args.queue_path).expanduser() if args.queue_path else None,
            limit=args.limit,
            stale_after_hours=args.stale_after_hours,
        )
        return _emit_report(
            report,
            json_output=args.json,
            print_report=print_personal_ops_queue_health_report,
        )

    if args.command == "personal-ops-queue-review":
        report = run_personal_ops_queue_review(
            queue_path=Path(args.queue_path).expanduser() if args.queue_path else None,
            limit=args.limit,
            stale_after_hours=args.stale_after_hours,
        )
        return _emit_report(
            report,
            json_output=args.json,
            print_report=print_personal_ops_queue_review_report,
        )

    if args.command == "personal-ops-queue-burn-in":
        report = run_personal_ops_queue_burn_in(
            minutes=args.minutes,
            lines=args.lines,
            limit=args.limit,
            save_report=args.save_report,
            report_dir=Path(args.report_dir).expanduser() if args.report_dir else None,
        )
        return _emit_report(
            report,
            json_output=args.json,
            print_report=print_personal_ops_queue_burn_in_report,
        )

    if args.command == "personal-ops-outcome-sync-reminder":
        report = run_personal_ops_outcome_sync_reminder(
            queue_path=Path(args.queue_path).expanduser() if args.queue_path else None,
            limit=args.limit,
            stale_after_hours=args.stale_after_hours,
        )
        return _emit_report(
            report,
            json_output=args.json,
            print_report=print_personal_ops_outcome_sync_reminder_report,
        )

    if args.command == "policy-check":
        report = run_policy_check()
        _emit_report(
            report,
            json_output=args.json,
            print_report=print_policy_check_report,
            success_status=None,
        )
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
        return _emit_report(
            report,
            json_output=args.json,
            print_report=print_explain_report,
            success_status=None,
        )

    if args.command == "bootstrap-config":
        report = bootstrap_policy_config(force=args.force)
        return _emit_report(report, json_output=args.json, print_report=print_bootstrap_report)

    report = run_retention(max_events=args.max_events, keep_archives=args.keep_archives)
    return _emit_report(report, json_output=args.json, print_report=print_retention_report)


def _forward_to_command(command: str, argv: Sequence[str] | None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    return main([command, *forwarded])


def doctor_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("doctor", argv)


def smoke_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("smoke", argv)


def status_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("status", argv)


def logs_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("logs", argv)


def burn_in_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("burn-in", argv)


def verify_runtime_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("verify-runtime", argv)


def delivery_check_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("delivery-check", argv)


def finalize_delivery_check_claim_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("finalize-delivery-check-claim", argv)


def supersede_delivery_check_receipt_main(
    argv: Sequence[str] | None = None,
) -> int:
    return _forward_to_command("supersede-delivery-check-receipt", argv)


def finalize_delivery_check_supersession_claim_main(
    argv: Sequence[str] | None = None,
) -> int:
    return _forward_to_command("finalize-delivery-check-supersession-claim", argv)


def reconcile_delivery_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("reconcile-delivery", argv)


def finalize_reconciliation_claim_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("finalize-reconciliation-claim", argv)


def supersede_reconciliation_receipt_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("supersede-reconciliation-receipt", argv)


def inbox_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("inbox", argv)


def coordination_snapshot_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("coordination-snapshot", argv)


def coordination_readiness_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("coordination-readiness", argv)


def coordination_console_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("coordination-console", argv)


def personal_ops_actions_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("personal-ops-actions", argv)


def validate_action_package_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("validate-action-package", argv)


def action_proposal_dismiss_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("action-proposal-dismiss", argv)


def action_proposal_dismissals_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("action-proposal-dismissals", argv)


def action_proposal_undismiss_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("action-proposal-undismiss", argv)


def operator_daily_state_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("operator-daily-state", argv)


def operator_review_session_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("operator-review-session", argv)


def operator_review_session_retention_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("operator-review-session-retention", argv)


def action_export_retention_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("action-export-retention", argv)


def operator_handoff_drill_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("operator-handoff-drill", argv)


def personal_ops_import_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("personal-ops-import", argv)


def personal_ops_queue_health_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("personal-ops-queue-health", argv)


def personal_ops_queue_burn_in_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("personal-ops-queue-burn-in", argv)


def personal_ops_outcome_sync_reminder_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("personal-ops-outcome-sync-reminder", argv)


def policy_check_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("policy-check", argv)


def explain_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("explain", argv)


def retention_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("retention", argv)


def bootstrap_config_main(argv: Sequence[str] | None = None) -> int:
    return _forward_to_command("bootstrap-config", argv)
