"""CLI report rendering helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from notification_hub.operations import (
    ActionExportRetentionReport,
    ActionPackageValidationReport,
    ActionProposalDismissalListReport,
    ActionProposalDismissReport,
    ActionProposalGroupOutcomeReport,
    ActionProposalUndismissReport,
    BootstrapConfigReport,
    BurnInReport,
    CoordinationConsoleReport,
    CoordinationReadinessReport,
    CoordinationSnapshotReport,
    DeliveryCheckReport,
    InboxReport,
    LogsReport,
    OperatorDailyStateReport,
    OperatorHandoffDrillReport,
    OperatorReviewSessionReport,
    OperatorReviewSessionRetentionReport,
    PersonalOpsActionExportReport,
    PersonalOpsImportQueueHealthCheckReport,
    PersonalOpsImportQueueHealthReport,
    PersonalOpsImportQueueItemReport,
    PersonalOpsImportQueueUpdateReport,
    PersonalOpsImportReport,
    PersonalOpsOutcomeSyncReminderReport,
    PersonalOpsQueueBurnInReport,
    PersonalOpsQueueReviewReport,
    PersonalOpsQueueScenarioReport,
    PolicyCheckReport,
    RetentionReport,
    SmokeReport,
    StatusReport,
    VerifyRuntimeReport,
)

_INBOX_SECTIONS: tuple[
    tuple[str, str],
    ...,
] = (
    ("needs_attention", "needs attention"),
    ("waiting_or_blocked", "waiting or blocked"),
    ("ready", "ready"),
    ("completed", "completed"),
)


def print_doctor_report(report: dict[str, object]) -> None:
    checks = report["checks"]
    config = report["config"]
    local_api = report["local_api"]
    retention = report["retention"]
    raw_durable_inbox = report.get("durable_inbox", {})
    assert isinstance(checks, dict)
    assert isinstance(config, dict)
    assert isinstance(local_api, dict)
    assert isinstance(retention, dict)
    durable_inbox: dict[str, object] = (
        cast(dict[str, object], raw_durable_inbox) if isinstance(raw_durable_inbox, dict) else {}
    )

    print(f"notification-hub doctor: {report['status']}")
    print(f"- local API healthy: {checks['local_api_healthy']}")
    print(f"- LaunchAgent present: {checks['launch_agent_present']}")
    print(f"- bridge file present: {checks['bridge_file_present']}")
    print(f"- push notifier available: {checks['push_notifier_available']}")
    print(f"- Slack configured: {checks['slack_configured']}")
    print(f"- policy load OK: {checks['policy_load_ok']}")
    if "runtime_wiring_current" in checks:
        print(f"- runtime wiring current: {checks['runtime_wiring_current']}")
    if "durable_inbox_ok" in checks:
        print(f"- durable inbox OK: {checks['durable_inbox_ok']}")
    if durable_inbox:
        print(f"- durable inbox status: {durable_inbox.get('status')}")
        print(f"- durable dead letters: {durable_inbox.get('dead_letter_count')}")
        print(f"- durable recent dead letters: {durable_inbox.get('recent_dead_letter_count')}")
    print(f"- policy path: {config['path']}")
    print(f"- retention enabled: {retention['enabled']}")
    print(f"- retention interval minutes: {retention['interval_minutes']}")
    if config["load_error"] is not None:
        print(f"- policy load error: {config['load_error']}")
    print(f"- health URL: {local_api['url']}")


def print_smoke_report(report: SmokeReport) -> None:
    print(f"notification-hub smoke: {report['status']}")
    print(f"- event URL: {report['event_url']}")
    print(f"- health URL: {report['health_url']}")
    print(f"- response status: {report['response_status']}")
    print(f"- event ID: {report['event_id']}")
    print(f"- log verified: {report['log_verified']}")
    if report["error"] is not None:
        print(f"- error: {report['error']}")


def print_delivery_check_report(report: DeliveryCheckReport) -> None:
    print(f"notification-hub delivery-check: {report['status']}")
    print(f"- Slack requested: {report['verify_slack']}")
    print(f"- Slack OK: {report['slack_ok']}")
    print(f"- push requested: {report['verify_push']}")
    print(f"- push OK: {report['push_ok']}")
    print(f"- event ID: {report['event_id']}")
    if report["error"] is not None:
        print(f"- error: {report['error']}")


def print_inbox_report(report: InboxReport) -> None:
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

    print("- near-rollup singles (first-occurrence, not yet repeated):")
    if not report["near_rollup_singles"]:
        print("  none")
    for item in report["near_rollup_singles"]:
        project = f" ({item['project']})" if item["project"] else ""
        print(f"  - [{item['intent']}] {item['source']}{project}: {item['title']}")


def print_status_report(report: StatusReport) -> None:
    print(f"notification-hub status: {report['status']}")
    print(f"- daemon reachable: {report['daemon_reachable']}")
    print(f"- watcher active: {report['watcher_active']}")
    print(f"- runtime wiring current: {report['runtime_wiring_current']}")
    print(f"- policy config found: {report['policy_config_found']}")
    print(f"- policy warnings: {report['policy_warning_count']}")
    print(f"- retention enabled: {report['retention_enabled']}")
    print(f"- retention last status: {report['retention_last_status']}")
    print(f"- events processed: {report['events_processed']}")
    durable_inbox = report["durable_inbox"]
    print(f"- durable inbox: {durable_inbox.get('status')}")
    print(
        "- durable queued/retry/dead: "
        f"{durable_inbox.get('queued_count')}/"
        f"{durable_inbox.get('retry_scheduled_count')}/"
        f"{durable_inbox.get('dead_letter_count')}"
    )
    print(f"- Slack configured: {report['slack_configured']}")
    print(f"- Slack delivery failures: {report['slack_delivery_failures']}")
    if report["visible_slack_delivery_failures"] != report["slack_delivery_failures"]:
        print(f"- visible Slack delivery failures: {report['visible_slack_delivery_failures']}")
    latest_delivery_check = report["latest_delivery_check"]
    if latest_delivery_check["last_slack_ok_at"] is not None:
        print(f"- latest Slack delivery check OK: {latest_delivery_check['last_slack_ok_at']}")
    print(f"- import queue queued: {report['import_queue']['queued_count']}")
    print(f"- push notifier available: {report['push_notifier_available']}")
    print(f"- next action: {report['next_action']}")


def write_json_report(report: object, output_path: str | None) -> None:
    content = json.dumps(report, indent=2, sort_keys=True)
    if output_path is None:
        print(content)
        return

    path = Path(output_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{content}\n", encoding="utf-8")
    print(str(path))


def print_coordination_snapshot_report(report: CoordinationSnapshotReport) -> None:
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


def print_coordination_readiness_report(report: CoordinationReadinessReport) -> None:
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


def print_coordination_console_report(report: CoordinationConsoleReport) -> None:
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
    print(f"- rich follow-up review: {proposal_review['rich_follow_up_review_count']}")
    print(f"- stable-key history: {proposal_review['handled_stable_key_match_count']}")
    print(f"- evidence rotations: {proposal_review['handled_evidence_rotation_count']}")
    print(f"- outcome quality: {outcome_quality['summary']}")
    print(f"- watch posture: {next_signal['watch_posture']}")
    print(f"- next signal: {next_signal['title']} ({next_signal['status']})")
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


def print_personal_ops_action_export_report(report: PersonalOpsActionExportReport) -> None:
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


def print_action_proposal_dismiss_report(report: ActionProposalDismissReport) -> None:
    print(f"notification-hub action-proposal-dismiss: {report['status']}")
    print(f"- path: {report['path']}")
    if report["dismissal"] is not None:
        print(f"- dismissal: {report['dismissal']['dismissal_key']}")
        print(f"- reason: {report['dismissal']['reason']}")
    if report["error"] is not None:
        print(f"- error: {report['error']}")


def print_action_proposal_dismissal_list_report(
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


def print_action_proposal_undismiss_report(report: ActionProposalUndismissReport) -> None:
    print(f"notification-hub action-proposal-undismiss: {report['status']}")
    print(f"- path: {report['path']}")
    print(f"- dismissal: {report['dismissal_key']}")
    print(f"- removed: {report['removed']}")
    if report["error"] is not None:
        print(f"- error: {report['error']}")


def print_action_proposal_group_outcome_report(
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


def print_operator_daily_state_report(report: OperatorDailyStateReport) -> None:
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


def print_operator_handoff_drill_report(report: OperatorHandoffDrillReport) -> None:
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


def print_operator_review_session_report(report: OperatorReviewSessionReport) -> None:
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


def print_operator_review_session_retention_report(
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


def print_action_export_retention_report(report: ActionExportRetentionReport) -> None:
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


def print_action_package_validation_report(report: ActionPackageValidationReport) -> None:
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


def print_personal_ops_import_report(report: PersonalOpsImportReport) -> None:
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


def print_personal_ops_queue_report(report: dict[str, object]) -> None:
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


def print_personal_ops_queue_health_report(
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


def print_personal_ops_queue_review_report(report: PersonalOpsQueueReviewReport) -> None:
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


def print_personal_ops_outcome_sync_reminder_report(
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


def print_personal_ops_queue_burn_in_report(report: PersonalOpsQueueBurnInReport) -> None:
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


def print_personal_ops_queue_scenario_report(report: PersonalOpsQueueScenarioReport) -> None:
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


def print_logs_report(report: LogsReport) -> None:
    durable_inbox = report["durable_inbox"] if "durable_inbox" in report else {}
    print(f"notification-hub logs: {report['status']}")
    print(f"- events log: {report['events_log']}")
    print(f"- stdout log: {report['stdout_log']}")
    print(f"- stderr log: {report['stderr_log']}")
    print(f"- durable inbox: {durable_inbox.get('status')}")
    print(f"- durable dead letters: {durable_inbox.get('dead_letter_count')}")
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


def print_burn_in_report(report: BurnInReport) -> None:
    durable_inbox = report["durable_inbox"] if "durable_inbox" in report else {}
    print(f"notification-hub burn-in: {report['status']}")
    print(f"- window: {report['minutes']} minutes")
    print(f"- events seen: {report['events_seen']}")
    print(f"- accepted event posts: {report['accepted_event_posts']}")
    print(f"- rejected event posts: {report['rejected_event_posts']}")
    print(f"- validation errors: {report['validation_error_count']}")
    print(f"- Slack delivery failures: {report['health']['slack_delivery_failure_count']}")
    print(f"- health: {report['health']['status']}")
    print(f"- durable inbox: {durable_inbox.get('status')}")
    print(f"- durable dead letters: {durable_inbox.get('dead_letter_count')}")
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


def print_verify_runtime_report(report: VerifyRuntimeReport) -> None:
    checks = report["checks"]
    smoke = report["smoke"]

    print(f"notification-hub verify-runtime: {report['status']}")
    print(f"- read only: {report['read_only']}")
    print(f"- health URL: {report['health_url']}")
    print(f"- doctor OK: {checks['doctor_ok']}")
    print(f"- policy check OK: {checks['policy_check_ok']}")
    print(f"- health details reachable: {checks['health_details_reachable']}")
    print(f"- runtime wiring current: {checks['runtime_wiring_current']}")
    print(f"- durable inbox OK: {checks['durable_inbox_ok']}")
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


def print_policy_check_report(report: PolicyCheckReport) -> None:
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


def print_explain_report(report: dict[str, object]) -> None:
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


def print_retention_report(report: RetentionReport) -> None:
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


def print_bootstrap_report(report: BootstrapConfigReport) -> None:
    print(f"notification-hub bootstrap-config: {report['status']}")
    print(f"- copied: {report['copied']}")
    print(f"- sample path: {report['example_path']}")
    print(f"- config path: {report['config_path']}")
    if report["error"] is not None:
        print(f"- error: {report['error']}")
