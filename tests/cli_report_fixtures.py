"""Shared CLI report fixtures for command and wrapper tests."""

from __future__ import annotations


def coordination_snapshot_report() -> dict[str, object]:
    return {
        "status": "ok",
        "schema_version": "notification-hub.coordination_snapshot.v1",
        "generated_at": "2026-05-09T00:00:00+00:00",
        "bridge_target_system": "codex",
        "bridge_snapshot_date": "2026-05-09",
        "bridge_snapshot": {
            "active_projects": {},
            "coordination": {"events_seen": 1},
            "runtime": {"status": "ok"},
            "follow_up": ["No immediate operator action needed."],
        },
        "bridge_save": {
            "attempted": False,
            "status": "not_requested",
            "db_path": None,
            "snapshot_id": None,
            "snapshot_date": None,
            "error": None,
        },
        "inbox": {
            "status": "ok",
            "hours": 12,
            "events_seen": 1,
            "needs_attention": [],
            "waiting_or_blocked": [],
            "ready": [],
            "completed": [],
            "rollups": [],
            "noise_candidates": [],
            "error": None,
        },
        "runtime_status": {
            "status": "ok",
            "health_url": "http://127.0.0.1:9199/health/details",
            "daemon_reachable": True,
            "watcher_active": True,
            "events_processed": 12,
            "uptime_seconds": 123.4,
            "policy_config_found": True,
            "policy_warning_count": 0,
            "retention_enabled": True,
            "retention_last_status": "ok",
            "runtime_wiring_current": True,
            "push_notifier_available": True,
            "slack_configured": True,
            "slack_delivery_failures": 0,
            "next_action": "No action needed.",
        },
        "follow_up": ["No immediate operator action needed."],
        "error": None,
    }


def personal_ops_action_export_report() -> dict[str, object]:
    return {
        "status": "ok",
        "schema_version": "notification-hub.personal_ops_action_export.v1",
        "generated_at": "2026-05-09T00:00:00+00:00",
        "hours": 12,
        "actions": [
            {
                "action_id": "notification-hub:personal-ops:mail:waiting_on_user:approval-requested",
                "source": "personal-ops",
                "project": "mail",
                "intent": "waiting_on_user",
                "priority": "high",
                "state": "waiting",
                "title": "Approval Requested",
                "summary": "2 repeated personal-ops events: Console reply needed",
                "suggested_next_action": "Review the waiting item and approve, reply, or dismiss it.",
                "evidence_event_id": "abc123",
                "evidence_timestamp": "2026-05-09T00:00:00+00:00",
                "count": 2,
            }
        ],
        "review_package": {
            "requested": False,
            "status": "not_requested",
            "path": None,
            "error": None,
        },
        "inbox": {
            "status": "ok",
            "hours": 12,
            "events_seen": 2,
            "needs_attention": [],
            "waiting_or_blocked": [],
            "ready": [],
            "completed": [],
            "rollups": [],
            "noise_candidates": [],
            "error": None,
        },
        "error": None,
    }


def burn_in_report(
    *,
    status: str = "ok",
    slack_delivery_failure_count: int = 0,
) -> dict[str, object]:
    return {
        "status": "ok",
        "minutes": 10,
        "events_seen": 0,
        "accepted_event_posts": 0,
        "rejected_event_posts": 0,
        "validation_error_count": 0,
        "health": {
            "accepted_event_posts": 0,
            "rejected_event_posts": 0,
            "validation_error_count": 0,
            "slack_delivery_failure_count": slack_delivery_failure_count,
            "status": status,
        },
        "noise_candidates": [],
        "noise_rule_suggestions": [],
        "repeated_signatures": [],
        "slack_eligible_events": 0,
        "slack_volume": [],
        "daemon_summary": {
            "access_status_counts": {},
            "accepted_event_posts": 0,
            "rejected_event_posts": 0,
            "validation_error_count": 0,
            "recent_validation_errors": [],
            "slack_delivery_failure_count": slack_delivery_failure_count,
            "recent_slack_delivery_failures": [],
        },
        "error": None,
    }


def import_queue_health(
    *,
    queued_count: int = 0,
    promoted_pending_count: int = 0,
    promoted_pending_stale_count: int = 0,
) -> dict[str, object]:
    needs_queue_attention = queued_count > 0 or promoted_pending_count > 0
    if queued_count:
        next_action = "Review queued personal-ops handoff items."
    elif promoted_pending_stale_count:
        next_action = "Resolve the matching personal-ops suggestion, record the promotion outcome, then rerun notification-hub personal-ops-queue-health."
    elif promoted_pending_count:
        next_action = "Resolve promoted personal-ops handoff outcomes."
    else:
        next_action = "No queued personal-ops handoff items."
    return {
        "status": "warn" if needs_queue_attention else "ok",
        "queue_path": "/tmp/personal-ops-import-queue.jsonl",
        "total_count": queued_count + promoted_pending_count,
        "queued_count": queued_count,
        "reviewed_count": 0,
        "rejected_count": 0,
        "snoozed_count": 0,
        "superseded_count": 0,
        "promoted_count": promoted_pending_count,
        "promoted_pending_count": promoted_pending_count,
        "promoted_pending_stale_count": promoted_pending_stale_count,
        "promoted_accepted_count": 0,
        "promoted_rejected_count": 0,
        "promoted_ignored_count": 0,
        "needs_outcome_sync": promoted_pending_count > 0,
        "needs_review": queued_count > 0,
        "oldest_queued_at": "2026-05-09T10:00:00+00:00" if queued_count else None,
        "oldest_queued_age_seconds": 60.0 if queued_count else None,
        "oldest_promoted_pending_at": "2026-05-09T08:00:00+00:00"
        if promoted_pending_count
        else None,
        "oldest_promoted_pending_age_seconds": 7200.0 if promoted_pending_count else None,
        "stale_after_hours": 4.0,
        "next_action": next_action,
    }


def coordination_readiness_report(status: str = "ok") -> dict[str, object]:
    return {
        "status": status,
        "decision": "ready_to_expand" if status == "ok" else "fix_noise_first",
        "summary": "Runtime, queue, and saved burn-in evidence are ready.",
        "queue_status": "ok",
        "queued_count": 0,
        "pending_count": 0,
        "stale_count": 0,
        "savedburn_in_reports": 1,
        "latest_burn_in_ready": True,
        "latest_burn_in_noise_candidates": 0,
        "runtime_status": "ok",
        "policy_warning_count": 0,
        "next_action": "Plan the next compact coordination console slice.",
        "evidence": ["runtime=ok"],
        "applied": False,
    }


def coordination_console_report(status: str = "ok") -> dict[str, object]:
    return {
        "status": status,
        "readiness": coordination_readiness_report(status=status),
        "action_count": 1,
        "active_action_count": 1,
        "handled_action_count": 0,
        "dismissal_count": 0,
        "actions": [],
        "handled_actions": [],
        "dismissals": [],
        "next_signal": {
            "status": "ready",
            "title": "Active proposal waiting",
            "summary": "The next real signal is already visible as an action proposal.",
            "qualifying_intents": ["waiting_on_user"],
            "hidden_action_count": 0,
            "dismissed_count": 0,
            "policy_covered_repeated_count": 0,
            "policy_covered_signatures": [],
            "dismissed_proposals": [],
            "next_action": "Save and validate a review package.",
        },
        "queue_health": import_queue_health(),
        "queued_items": [],
        "pending_promotion_items": [],
        "outcome_sync_reminder": {
            "status": "ok",
            "should_remind": False,
            "pending_count": 0,
            "stale_count": 0,
            "reminders": [],
            "next_commands": ["uv run notification-hub personal-ops-queue-health"],
            "next_action": "No pending promoted personal-ops handoff outcomes.",
            "applied": False,
        },
        "burn_in_reports": [],
        "guide_stage": "package_review",
        "guide_steps": [
            {
                "step": 1,
                "title": "Save review package",
                "status": "current",
                "summary": "Stage the current proposals locally for inspection.",
                "commands": ["uv run notification-hub personal-ops-actions --save-review-package"],
                "action_id": "action-1",
                "queue_id": None,
            }
        ],
        "next_commands": ["uv run notification-hub personal-ops-actions --save-review-package"],
        "next_action": "Save and validate a review package.",
        "applied": False,
    }


def operator_daily_state_report(status: str = "ok") -> dict[str, object]:
    return {
        "status": status,
        "generated_at": "2026-05-10T04:50:00+00:00",
        "hours": 24,
        "runtime": {
            "status": status,
            "daemon_reachable": True,
            "watcher_active": True,
            "runtime_wiring_current": True,
            "policy_config_found": True,
            "policy_warning_count": 0,
            "retention_enabled": True,
            "retention_last_status": "ok",
            "events_processed": 12,
            "slack_configured": True,
            "slack_delivery_failures": 0,
            "import_queue": import_queue_health(),
            "push_notifier_available": True,
            "next_action": "No action needed.",
        },
        "queue_health": {
            "status": "ok",
            "health": import_queue_health(),
            "queued_items": [],
            "pending_promotion_items": [],
            "next_commands": [],
            "applied": False,
        },
        "coordination_console": coordination_console_report(status=status),
        "burn_in": burn_in_report(status=status),
        "dismissals": [],
        "next_action": "Monitor /review for the next real handoff signal.",
        "report_file": {
            "requested": True,
            "status": "ok",
            "path": "/tmp/operator-daily-state.json",
            "error": None,
        },
        "applied": False,
    }


def operator_handoff_drill_report(status: str = "ok") -> dict[str, object]:
    scenario = {
        "status": status,
        "queue_path": "/tmp/scenario-queue.jsonl",
        "package_path": "/tmp/package.json",
        "queue_id": "queue123",
        "queued_count": 1,
        "review_status": "ok",
        "promotion_status": "ok",
        "promotion_outcome": "accepted",
        "final_health": import_queue_health(),
        "applied": True,
        "next_action": "Scenario passed; use the same lifecycle for real queued handoffs.",
        "error": None,
    }
    return {
        "status": status,
        "generated_at": "2026-05-10T04:55:00+00:00",
        "scenario": scenario,
        "queue_burn_in": {
            "status": status,
            "ready_for_live_promotion": True,
            "scenario": scenario,
            "queue_health": {"health": import_queue_health()},
            "runtime_burn_in": {"health": {"status": "ok"}},
            "outcome_sync_posture": "operator-mediated",
            "operator_steps": [],
            "next_action": "Queue loop is ready.",
            "report_file": {"status": "not_requested"},
        },
        "review_steps": ["Open /review and inspect an action proposal before saving a package."],
        "next_action": "Use the same operator-mediated lifecycle for the next real handoff.",
        "applied": False,
    }


def delivery_check_report(
    *,
    status: str = "ok",
    verify_slack: bool = False,
    verify_push: bool = False,
) -> dict[str, object]:
    return {
        "status": status,
        "verify_slack": verify_slack,
        "verify_push": verify_push,
        "slack_ok": status == "ok" if verify_slack else None,
        "push_ok": status == "ok" if verify_push else None,
        "event_id": "delivery123",
        "error": None if status == "ok" else "delivery failed",
    }
