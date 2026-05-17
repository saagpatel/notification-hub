"""Tests for review proposal endpoints."""

from __future__ import annotations

from unittest.mock import patch

from httpx import AsyncClient


async def test_review_action_proposal_group_package_endpoint_is_read_only(
    client: AsyncClient,
) -> None:
    with patch(
        "notification_hub.server.save_action_proposal_group_package",
        return_value={
            "status": "ok",
            "group_key": "personal-ops:mail:waiting_on_user:high:waiting",
            "action_count": 2,
            "review_package": {"status": "ok", "path": "/tmp/actions.json"},
            "import_result": None,
            "next_action": "Inspect the saved group package before queueing it.",
            "applied": False,
            "error": None,
        },
    ) as mock_save_group:
        resp = await client.post(
            "/review/action-proposal-group/package",
            json={"group_key": "personal-ops:mail:waiting_on_user:high:waiting", "hours": 4},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["action_count"] == 2
    assert data["applied"] is False
    mock_save_group.assert_called_once_with(
        group_key="personal-ops:mail:waiting_on_user:high:waiting",
        route=None,
        hours=4,
        limit=25,
        enqueue=False,
    )


async def test_review_action_proposal_group_queue_endpoint_stays_non_applying(
    client: AsyncClient,
) -> None:
    with patch(
        "notification_hub.server.save_action_proposal_group_package",
        return_value={
            "status": "ok",
            "group_key": "personal-ops:mail:waiting_on_user:high:waiting",
            "action_count": 2,
            "review_package": {"status": "ok", "path": "/tmp/actions.json"},
            "import_result": {"status": "ok", "queued_count": 2},
            "next_action": "Review the queued personal-ops handoff items.",
            "applied": False,
            "error": None,
        },
    ) as mock_queue_group:
        resp = await client.post(
            "/review/action-proposal-group/queue",
            json={"group_key": "personal-ops:mail:waiting_on_user:high:waiting"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["import_result"]["queued_count"] == 2
    assert data["applied"] is False
    mock_queue_group.assert_called_once_with(
        group_key="personal-ops:mail:waiting_on_user:high:waiting",
        route=None,
        hours=24,
        limit=25,
        enqueue=True,
    )


async def test_review_action_proposal_group_dismiss_endpoint_is_local_only(
    client: AsyncClient,
) -> None:
    with patch(
        "notification_hub.server.dismiss_action_proposal_group",
        return_value={
            "status": "ok",
            "group_key": "personal-ops:mail:waiting_on_user:high:waiting",
            "dismissed_count": 2,
            "dismissals": [],
            "next_action": "Group dismissed from the local console.",
            "applied": False,
            "error": None,
        },
    ) as mock_dismiss_group:
        resp = await client.post(
            "/review/action-proposal-group/dismiss",
            json={
                "group_key": "personal-ops:mail:waiting_on_user:high:waiting",
                "reason": "known noise",
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["dismissed_count"] == 2
    assert data["applied"] is False
    mock_dismiss_group.assert_called_once_with(
        group_key="personal-ops:mail:waiting_on_user:high:waiting",
        reason="known noise",
        route=None,
        hours=24,
        limit=25,
    )


async def test_review_action_proposal_group_route_posts_are_local_only(
    client: AsyncClient,
) -> None:
    with patch(
        "notification_hub.server.save_action_proposal_group_package",
        return_value={
            "status": "ok",
            "group_key": "personal-ops:mail:waiting_on_user:high:waiting",
            "action_count": 1,
            "review_package": {"status": "ok", "path": "/tmp/actions.json"},
            "import_result": {"status": "ok", "queued_count": 1},
            "next_action": "Review the queued personal-ops handoff items.",
            "applied": False,
            "error": None,
        },
    ) as mock_queue_group:
        resp = await client.post(
            "/review/action-proposal-group/queue",
            json={
                "group_key": "personal-ops:mail:waiting_on_user:high:waiting",
                "route": "promote",
            },
        )

    assert resp.status_code == 200
    assert resp.json()["applied"] is False
    mock_queue_group.assert_called_once_with(
        group_key="personal-ops:mail:waiting_on_user:high:waiting",
        route="promote",
        hours=24,
        limit=25,
        enqueue=True,
    )

    with patch(
        "notification_hub.server.dismiss_action_proposal_group",
        return_value={
            "status": "ok",
            "group_key": "personal-ops:mail:waiting_on_user:high:waiting",
            "dismissed_count": 1,
            "dismissals": [],
            "next_action": "Group dismissed from the local console.",
            "applied": False,
            "error": None,
        },
    ) as mock_dismiss_group:
        resp = await client.post(
            "/review/action-proposal-group/dismiss",
            json={
                "group_key": "personal-ops:mail:waiting_on_user:high:waiting",
                "route": "suppress",
                "reason": "already covered",
            },
        )

    assert resp.status_code == 200
    assert resp.json()["applied"] is False
    mock_dismiss_group.assert_called_once_with(
        group_key="personal-ops:mail:waiting_on_user:high:waiting",
        reason="already covered",
        route="suppress",
        hours=24,
        limit=25,
    )


async def test_review_action_proposal_group_outcome_endpoint_is_local_only(
    client: AsyncClient,
) -> None:
    with patch(
        "notification_hub.server.record_action_proposal_group_outcome",
        return_value={
            "status": "ok",
            "group_key": "personal-ops:mail:waiting_on_user:high:waiting",
            "outcome": "needs_follow_up",
            "group_history": {"event_type": "outcome", "outcome": "needs_follow_up"},
            "next_action": "Group outcome recorded locally.",
            "applied": False,
            "error": None,
        },
    ) as mock_outcome_group:
        resp = await client.post(
            "/review/action-proposal-group/outcome",
            json={
                "group_key": "personal-ops:mail:waiting_on_user:high:waiting",
                "outcome": "needs_follow_up",
                "reason": "follow up",
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["outcome"] == "needs_follow_up"
    assert data["applied"] is False
    mock_outcome_group.assert_called_once_with(
        group_key="personal-ops:mail:waiting_on_user:high:waiting",
        outcome="needs_follow_up",
        reason="follow up",
        hours=24,
        limit=25,
    )


async def test_review_outcome_sync_reminder_endpoint_reports_pending(
    client: AsyncClient,
) -> None:
    with patch(
        "notification_hub.server.run_personal_ops_outcome_sync_reminder",
        return_value={
            "status": "warn",
            "should_remind": True,
            "pending_count": 1,
            "stale_count": 1,
            "reminders": [],
            "next_commands": [
                'personal-ops suggestion accept|reject SUGGESTION_ID --note "..."',
                "uv run notification-hub personal-ops-queue --queue-id QUEUE_ID "
                "--status promoted --promotion-target-id SUGGESTION_ID "
                '--promotion-outcome accepted|rejected --promotion-outcome-note "..."',
            ],
            "next_action": "Resolve stale promoted personal-ops handoff outcomes before promoting more work.",
            "applied": False,
        },
    ) as mock_reminder:
        resp = await client.get("/review/outcome-sync-reminder?limit=3&stale_after_hours=2")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "warn"
    assert data["should_remind"] is True
    assert data["applied"] is False
    mock_reminder.assert_called_once_with(limit=3, stale_after_hours=2.0)


async def test_review_action_proposal_dismiss_endpoint_persists_dismissal(
    client: AsyncClient,
) -> None:
    with (
        patch(
            "notification_hub.server.run_personal_ops_action_export",
            return_value={
                "status": "ok",
                "schema_version": "notification-hub.personal_ops_action_export.v1",
                "generated_at": "2026-05-10T04:40:00+00:00",
                "hours": 24,
                "actions": [
                    {
                        "action_id": "action-1",
                        "dismissal_key": "proposal:personal-ops:mail:waiting-on-user:abc",
                        "source": "personal-ops",
                        "project": "mail",
                        "intent": "waiting_on_user",
                        "priority": "high",
                        "state": "waiting",
                        "title": "Approval Requested",
                        "summary": "2 repeated personal-ops events: Test draft",
                        "signal_level": "urgent",
                        "signal_body": "Test draft",
                        "suggested_next_action": "Review the waiting item.",
                        "evidence_event_id": "event-1",
                        "evidence_timestamp": "2026-05-10T04:40:00+00:00",
                        "count": 2,
                    }
                ],
                "dismissed_action_count": 0,
                "dismissals": [],
                "review_package": {"status": "not_requested"},
                "inbox": {},
                "error": None,
            },
        ) as mock_actions,
        patch(
            "notification_hub.server.dismiss_action_proposal",
            return_value={
                "status": "ok",
                "path": "/tmp/dismissals.jsonl",
                "dismissal": {
                    "dismissal_key": "proposal:personal-ops:mail:waiting-on-user:abc",
                    "dismissed_at": "2026-05-10T04:41:00+00:00",
                    "deleted_at": None,
                    "active": True,
                    "reason": "known test signal",
                    "source": "personal-ops",
                    "project": "mail",
                    "intent": "waiting_on_user",
                    "title": "Approval Requested",
                    "body": "Test draft",
                    "evidence_event_id": "event-1",
                },
                "applied": False,
                "error": None,
            },
        ) as mock_dismiss,
    ):
        resp = await client.post(
            "/review/action-proposal/proposal%3Apersonal-ops%3Amail%3Awaiting-on-user%3Aabc/dismiss",
            json={"reason": "known test signal"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["applied"] is False
    mock_actions.assert_called_once_with(hours=24, limit=100, include_dismissed=True)
    mock_dismiss.assert_called_once_with(
        dismissal_key="proposal:personal-ops:mail:waiting-on-user:abc",
        reason="known test signal",
        source="personal-ops",
        project="mail",
        intent="waiting_on_user",
        title="Approval Requested",
        body="Test draft",
        evidence_event_id="event-1",
    )


async def test_review_action_proposal_dismissals_endpoint_lists_dismissals(
    client: AsyncClient,
) -> None:
    with patch(
        "notification_hub.server.run_action_proposal_dismissal_list",
        return_value={
            "status": "ok",
            "path": "/tmp/dismissals.jsonl",
            "dismissal_count": 1,
            "dismissals": [
                {
                    "dismissal_key": "proposal:abc",
                    "dismissed_at": "2026-05-10T04:41:00+00:00",
                    "deleted_at": None,
                    "active": True,
                    "reason": "known noise",
                    "source": "personal-ops",
                    "project": "mail",
                    "intent": "waiting_on_user",
                    "title": "Approval Requested",
                    "body": "Test draft",
                    "evidence_event_id": "event-1",
                }
            ],
            "applied": False,
        },
    ) as mock_list:
        resp = await client.get(
            "/review/action-proposal-dismissals?limit=3&dismissal_key=proposal%3Aabc&include_inactive=true"
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["dismissals"][0]["dismissal_key"] == "proposal:abc"
    assert data["applied"] is False
    mock_list.assert_called_once_with(
        limit=3,
        dismissal_key="proposal:abc",
        include_inactive=True,
    )


async def test_review_action_proposal_undismiss_endpoint_adds_tombstone(
    client: AsyncClient,
) -> None:
    with patch(
        "notification_hub.server.undismiss_action_proposal",
        return_value={
            "status": "ok",
            "path": "/tmp/dismissals.jsonl",
            "dismissal_key": "proposal:abc",
            "removed": True,
            "applied": False,
            "error": None,
        },
    ) as mock_undismiss:
        resp = await client.post(
            "/review/action-proposal/proposal%3Aabc/undismiss",
            json={"reason": "useful again"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["removed"] is True
    assert data["applied"] is False
    mock_undismiss.assert_called_once_with(
        dismissal_key="proposal:abc",
        reason="useful again",
    )
