"""Shared fixtures for coordination console tests."""

from __future__ import annotations

from notification_hub.operations import CoordinationConsoleActionReport


def coordination_action_report(
    *,
    evidence_timestamp: str = "2026-05-10T04:50:00+00:00",
    outcome_recorded_at: str | None = "2026-05-10T04:00:00+00:00",
) -> CoordinationConsoleActionReport:
    return {
        "action": {
            "action_id": "action-follow-up-rich",
            "dismissal_key": "proposal:personal-ops:mail:waiting-on-user:stable",
            "source": "personal-ops",
            "project": "mail",
            "intent": "waiting_on_user",
            "priority": "high",
            "state": "waiting",
            "title": "Approval Requested",
            "summary": "Rich approval draft needs inspection.",
            "signal_level": "urgent",
            "signal_body": "Approval draft",
            "suggested_next_action": "Review the waiting item.",
            "evidence_event_id": "event-follow-up-rich",
            "evidence_timestamp": evidence_timestamp,
            "evidence_context": {
                "thread_id": "thread-rich",
                "draft_id": "draft-rich",
                "approval_id": "approval-rich",
            },
            "evidence_quality": "rich",
            "count": 4,
        },
        "lineage_status": "follow_up",
        "lineage_label": "Needs follow-up",
        "lineage_next_action": "Evidence was inspected and needs operator follow-up.",
        "lineage_reason": "Latest needs_follow_up group outcome matched this action id.",
        "lineage_history_event_type": "outcome",
        "lineage_history_recorded_at": outcome_recorded_at,
        "lineage_history_outcome": "needs_follow_up",
        "stable_key_matched": False,
        "evidence_event_rotated": False,
        "previous_action_id": "action-follow-up-rich",
        "queue_id": None,
        "queue_status": None,
        "promotion_outcome": None,
        "promotion_target_id": None,
    }


def coordination_status(
    *,
    status: str = "ok",
    policy_warning_count: int = 0,
    queued_count: int = 0,
    promoted_count: int = 3,
    promoted_pending_count: int = 0,
    promoted_pending_stale_count: int = 0,
) -> dict[str, object]:
    return {
        "status": status,
        "health_url": "http://127.0.0.1:9199/health/details",
        "daemon_reachable": True,
        "watcher_active": True,
        "events_processed": 12,
        "uptime_seconds": 123.4,
        "policy_config_found": True,
        "policy_warning_count": policy_warning_count,
        "retention_enabled": True,
        "retention_last_status": "ok",
        "runtime_wiring_current": True,
        "push_notifier_available": True,
        "slack_configured": True,
        "slack_delivery_failures": 0,
        "import_queue": {
            "status": "warn" if promoted_pending_stale_count else "ok",
            "queue_path": "/tmp/queue.jsonl",
            "total_count": promoted_count + queued_count,
            "queued_count": queued_count,
            "reviewed_count": 0,
            "rejected_count": 0,
            "snoozed_count": 0,
            "superseded_count": 0,
            "promoted_count": promoted_count,
            "promoted_pending_count": promoted_pending_count,
            "promoted_pending_stale_count": promoted_pending_stale_count,
            "promoted_accepted_count": promoted_count,
            "promoted_rejected_count": 0,
            "promoted_ignored_count": 0,
            "needs_outcome_sync": promoted_pending_count > 0,
            "needs_review": queued_count > 0,
            "oldest_queued_at": None,
            "oldest_queued_age_seconds": None,
            "oldest_promoted_pending_at": None,
            "oldest_promoted_pending_age_seconds": None,
            "stale_after_hours": 4.0,
            "next_action": "No queued personal-ops handoff items.",
        },
        "next_action": "No action needed.",
    }


def coordination_burn_in_report(*, noise_candidate_count: int = 0) -> dict[str, object]:
    return {
        "path": "/tmp/report.json",
        "name": "personal-ops-queue-burn-in-20260510-040904.json",
        "modified_at": "2026-05-10T04:09:04+00:00",
        "size_bytes": 200,
        "status": "ok",
        "generated_at": "2026-05-10T04:09:04+00:00",
        "ready_for_live_promotion": noise_candidate_count == 0,
        "queued_count": 0,
        "pending_count": 0,
        "stale_count": 0,
        "runtime_status": "ok",
        "noise_candidate_count": noise_candidate_count,
        "next_action": "Queue loop is ready.",
    }
