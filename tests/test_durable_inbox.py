"""Tests for durable inbox persistence and retry/dead-letter lifecycle."""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import notification_hub.durable_inbox as durable_inbox
from notification_hub.durable_inbox import (
    IdempotencyConflictError,
    MissingEventAuthorityError,
    accepted_channels,
    channel_outage_summary,
    channel_state_counts,
    channels_in_state,
    claim_next_due_event,
    collect_health,
    disposition_dead_letter,
    disposition_partial_deliveries_for_channel,
    enqueue_event,
    get_channel_receipts,
    get_channel_reconciliation_receipt,
    get_channel_state,
    get_event,
    init_schema,
    mark_delivered,
    partial_deliveries_for_channel,
    prune_retained_events,
    recent_channel_acceptance_times,
    reclaim_stale_processing,
    record_channel_reconciliation,
    record_channel_state,
    record_processing_deferred,
    record_processing_failure,
    record_processing_outcome_unknown,
    retry_delay_seconds,
    unknown_delivery_evidence_digest,
)
from notification_hub.models import StoredEvent


def test_init_schema_closes_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    class TrackingConnection:
        closed = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def executescript(self, _sql: str) -> None:
            pass

        def execute(self, _sql: str):
            return self

        def fetchall(self):
            return [
                (0, "payload_digest"),
                (1, "dead_letter_disposition"),
                (2, "dead_letter_disposition_ref"),
                (3, "dead_letter_dispositioned_at"),
                (4, "acceptance_receipt"),
                (5, "delivery_receipt"),
                (6, "observation_receipt"),
                (7, "terminal_disposition"),
                (8, "backoff_until"),
                (9, "last_error_category"),
            ]

        def close(self) -> None:
            self.closed = True

    connection = TrackingConnection()
    monkeypatch.setattr(durable_inbox, "_connect", lambda _path=None: connection)

    durable_inbox.init_schema()

    assert connection.closed is True


def _event(event_id: str = "evt1") -> StoredEvent:
    return StoredEvent(
        event_id=event_id,
        source="codex",
        level="info",
        title="Durable inbox test",
        body="Persist me before ack.",
        project="notification-hub",
        classified_level="info",
        producer="fixture-producer",
        required_destinations=["log"],
    )


def test_init_schema_creates_sqlite_database(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"

    init_schema(db_path)

    assert db_path.exists()
    assert collect_health(path=db_path)["status"] == "ok"


def test_enqueue_event_is_idempotent_by_event_id(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _event("stable-id")

    first = enqueue_event(event, path=db_path)
    second = enqueue_event(event, path=db_path)

    assert first.event_id == "stable-id"
    assert second.event_id == "stable-id"
    assert collect_health(path=db_path)["queued_count"] == 1


def test_retry_with_new_server_default_timestamp_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    first = _event("stable-id")
    enqueue_event(first, path=db_path)
    retry = _event("stable-id").model_copy(
        update={"timestamp": first.timestamp + timedelta(seconds=5)}
    )

    accepted = enqueue_event(retry, path=db_path)

    assert accepted.timestamp == first.timestamp
    assert collect_health(path=db_path)["queued_count"] == 1


def test_enqueue_event_rejects_conflicting_payload_for_same_event_id(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("stable-id"), path=db_path)
    conflicting = _event("stable-id").model_copy(update={"body": "Different payload"})

    with pytest.raises(IdempotencyConflictError, match="different payload digest"):
        enqueue_event(conflicting, path=db_path)

    assert collect_health(path=db_path)["queued_count"] == 1


def test_enqueue_event_rejects_missing_durable_authority(tmp_path: Path) -> None:
    event = _event("missing-authority").model_copy(
        update={"producer": None, "required_destinations": []}
    )

    with pytest.raises(MissingEventAuthorityError, match="destination authority"):
        enqueue_event(event, path=tmp_path / "inbox.sqlite3")


def test_concurrent_conflicting_idempotency_claims_have_one_winner(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    init_schema(db_path)
    barrier = threading.Barrier(3)
    first = _event("raced-id").model_copy(update={"body": "first principal payload"})
    second = _event("raced-id").model_copy(
        update={
            "body": "second principal payload",
            "producer": "other-producer",
        }
    )

    def submit(event: StoredEvent) -> StoredEvent:
        barrier.wait()
        return enqueue_event(event, path=db_path)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit, event) for event in (first, second)]
        barrier.wait()
        outcomes: list[StoredEvent | BaseException] = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except BaseException as exc:  # noqa: BLE001 - exact type is asserted below
                outcomes.append(exc)

    assert sum(isinstance(value, StoredEvent) for value in outcomes) == 1
    assert sum(isinstance(value, IdempotencyConflictError) for value in outcomes) == 1
    winner = get_event("raced-id", path=db_path)
    assert winner is not None
    assert winner.event.producer in {"fixture-producer", "other-producer"}


def test_claim_and_mark_processed_transition(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event(), path=db_path)

    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    assert claimed.status == "processing"
    assert claimed.attempt_count == 1

    mark_delivered(
        claimed.event_id,
        expected_attempt_count=claimed.attempt_count,
        outcome="processed",
        classified_level=claimed.event.classified_level,
        path=db_path,
    )

    stored = get_event(claimed.event_id, path=db_path)
    assert stored is not None
    assert stored.status == "processed"
    assert collect_health(path=db_path)["processed_count"] == 1


def test_channel_state_tracks_attempt_and_acceptance_without_claiming_delivery(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("channel-state"), path=db_path)

    record_channel_state("channel-state", "slack", "attempted", path=db_path)
    record_channel_state("channel-state", "slack", "accepted", path=db_path)

    assert accepted_channels("channel-state", path=db_path) == frozenset({"slack"})
    assert channel_state_counts(path=db_path) == {"accepted": 1}
    assert collect_health(path=db_path)["delivery_state_counts"] == {
        "accepted": 1,
        "attempted": 1,
        "delivered": 0,
        "observed": 0,
        "dispositioned": 0,
    }


def test_channel_failure_is_not_treated_as_accepted(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("channel-failure"), path=db_path)

    record_channel_state("channel-failure", "push", "attempted", path=db_path)
    record_channel_state(
        "channel-failure", "push", "failed", path=db_path, error_category="timeout"
    )

    assert accepted_channels("channel-failure", path=db_path) == frozenset()
    assert channel_state_counts(path=db_path) == {"failed": 1}
    assert channels_in_state("channel-failure", "failed", path=db_path) == frozenset({"push"})


def test_channel_backoff_is_persisted_and_cleared_on_retry(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("channel-backoff"), path=db_path)
    record_channel_state("channel-backoff", "slack", "attempted", path=db_path)
    record_channel_state(
        "channel-backoff",
        "slack",
        "failed",
        path=db_path,
        error_category="timeout",
        backoff_until="2026-07-12T13:00:00Z",
    )
    assert (
        get_channel_receipts("channel-backoff", "slack", path=db_path)["backoff_until"]
        == "2026-07-12T13:00:00Z"
    )
    record_channel_state("channel-backoff", "slack", "attempted", path=db_path)
    assert get_channel_receipts("channel-backoff", "slack", path=db_path)["backoff_until"] is None


def test_channel_receipts_remain_distinct_across_lifecycle(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("receipt-lifecycle"), path=db_path)
    record_channel_state(
        "receipt-lifecycle", "slack", "accepted", path=db_path, destination_ref="ack:1"
    )
    record_channel_state(
        "receipt-lifecycle", "slack", "delivered", path=db_path, destination_ref="readback:1"
    )
    record_channel_state(
        "receipt-lifecycle", "slack", "observed", path=db_path, destination_ref="operator:1"
    )

    receipts = get_channel_receipts("receipt-lifecycle", "slack", path=db_path)
    assert receipts["acceptance_receipt"] == "ack:1"
    assert receipts["delivery_receipt"] == "readback:1"
    assert receipts["observation_receipt"] == "operator:1"
    health = collect_health(path=db_path)
    assert health["attempted_count"] == 0
    assert health["observed_count"] == 1
    assert health["accepted_count"] == 0
    assert health["delivered_count"] == 0
    assert health["dispositioned_count"] == 0


def test_recent_channel_acceptance_times_reconstructs_restart_rate_history(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    now = datetime.now(UTC)
    for event_id, channel in (("recent-push", "push"), ("recent-slack", "slack")):
        enqueue_event(_event(event_id), path=db_path)
        record_channel_state(event_id, channel, "accepted", path=db_path)

    restored = recent_channel_acceptance_times(path=db_path, at=now + timedelta(seconds=1))

    assert len(restored["push"]) == 1
    assert len(restored["slack"]) == 1
    assert all(now <= value <= now + timedelta(seconds=1) for value in restored["push"])
    assert all(now <= value <= now + timedelta(seconds=1) for value in restored["slack"])


def test_reclaim_stale_processing_lease(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event(), path=db_path)
    claimed = claim_next_due_event(path=db_path, lease_seconds=-1)
    assert claimed is not None

    reclaimed = reclaim_stale_processing(path=db_path)

    assert reclaimed == 1
    stored = get_event(claimed.event_id, path=db_path)
    assert stored is not None
    assert stored.status == "retry_scheduled"


def test_reclaim_stale_unknown_outcome_requires_reconciliation_instead_of_retry(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("stale-unknown"), path=db_path)
    claimed = claim_next_due_event(path=db_path, lease_seconds=-1)
    assert claimed is not None
    record_channel_state(
        claimed.event_id,
        "slack",
        "outcome_unknown",
        path=db_path,
        destination_ref="slack:webhook:transport:response_unknown",
    )

    reclaimed = reclaim_stale_processing(path=db_path)

    assert reclaimed == 0
    stored = get_event(claimed.event_id, path=db_path)
    assert stored is not None
    assert stored.status == "reconciliation_required"
    assert claim_next_due_event(path=db_path) is None


def test_reclaim_stale_attempted_delivery_requires_reconciliation_instead_of_retry(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("stale-attempted"), path=db_path)
    claimed = claim_next_due_event(path=db_path, lease_seconds=-1)
    assert claimed is not None
    record_channel_state(claimed.event_id, "slack", "attempted", path=db_path)

    reclaimed = reclaim_stale_processing(path=db_path)

    assert reclaimed == 0
    stored = get_event(claimed.event_id, path=db_path)
    assert stored is not None
    assert stored.status == "reconciliation_required"
    assert claim_next_due_event(path=db_path) is None


def test_restart_before_first_attempt_preserves_queued_event(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("restart-before-attempt"), path=db_path)

    init_schema(db_path)  # Simulate a fresh process reopening the same durable database.
    claimed = claim_next_due_event(path=db_path)

    assert claimed is not None
    assert claimed.event_id == "restart-before-attempt"
    assert claimed.attempt_count == 1


def test_restart_after_acceptance_before_terminal_receipt_skips_accepted_channel(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("restart-after-acceptance"), path=db_path)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    record_channel_state(
        claimed.event_id,
        "slack",
        "accepted",
        path=db_path,
        destination_ref="fixture:accepted",
    )
    record_processing_failure(claimed, RuntimeError("crash before terminal receipt"), path=db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE durable_events SET next_attempt_at = ? WHERE event_id = ?",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), claimed.event_id),
        )

    init_schema(db_path)
    retried = claim_next_due_event(path=db_path)

    assert retried is not None and retried.attempt_count == 2
    assert accepted_channels(retried.event_id, path=db_path) == frozenset({"slack"})


def test_rate_limit_overflow_retry_survives_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("overflow-restart"), path=db_path)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    record_processing_failure(claimed, RuntimeError("rate_limited"), path=db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE durable_events SET next_attempt_at = ? WHERE event_id = ?",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), claimed.event_id),
        )

    init_schema(db_path)
    retried = claim_next_due_event(path=db_path)

    assert retried is not None
    assert retried.event_id == "overflow-restart"
    assert retried.attempt_count == 2


def test_retry_backoff_schedules_transient_failure(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event(), path=db_path, max_attempts=5)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None

    status = record_processing_failure(claimed, RuntimeError("temporary"), path=db_path)

    stored = get_event(claimed.event_id, path=db_path)
    assert status == "retry_scheduled"
    assert stored is not None
    assert stored.status == "retry_scheduled"
    assert stored.last_error_type == "RuntimeError"
    assert stored.next_attempt_at is not None
    assert retry_delay_seconds(1) == 5


def test_outcome_unknown_requires_reconciliation_and_cannot_be_claimed_for_retry(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("unknown-outcome"), path=db_path)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    record_channel_state(
        claimed.event_id,
        "slack",
        "outcome_unknown",
        path=db_path,
        destination_ref="slack:webhook:transport:response_unknown",
    )
    record_channel_state(
        claimed.event_id,
        "slack",
        "outcome_unknown",
        path=db_path,
        destination_ref="slack:webhook:transport:response_unknown",
    )
    with pytest.raises(ValueError, match="explicit reconciliation"):
        record_channel_state(
            claimed.event_id,
            "slack",
            "accepted",
            path=db_path,
            destination_ref="slack:webhook:http:2xx",
        )
    with pytest.raises(ValueError, match="receipt is immutable"):
        record_channel_state(
            claimed.event_id,
            "slack",
            "outcome_unknown",
            path=db_path,
            destination_ref="slack:webhook:http:5xx",
        )

    status = record_processing_outcome_unknown(
        claimed,
        RuntimeError("provider reconciliation required"),
        path=db_path,
    )

    stored = get_event(claimed.event_id, path=db_path)
    assert status == "reconciliation_required"
    assert stored is not None
    assert stored.status == "reconciliation_required"
    assert stored.next_attempt_at is None
    assert claim_next_due_event(path=db_path) is None
    assert (
        get_channel_receipts(claimed.event_id, "slack", path=db_path)["destination_ref"]
        == "slack:webhook:transport:response_unknown"
    )
    health = collect_health(path=db_path)
    assert health["status"] == "degraded"
    assert health["reconciliation_required_count"] == 1
    assert "Reconcile every outcome-unknown" in health["next_action"]
    with pytest.raises(ValueError, match="no longer matches"):
        record_processing_outcome_unknown(
            claimed,
            RuntimeError("replayed transition"),
            path=db_path,
        )
    replayed = get_event(claimed.event_id, path=db_path)
    assert replayed is not None
    assert replayed.status == "reconciliation_required"


def test_reconciliation_appends_stable_receipt_without_rewriting_unknown_evidence(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("reconcile-success"), path=db_path)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    unknown_ref = "slack:webhook:transport:response_unknown"
    record_channel_state(
        claimed.event_id,
        "slack",
        "outcome_unknown",
        path=db_path,
        destination_ref=unknown_ref,
        error_category="outcome_unknown",
    )
    record_processing_outcome_unknown(
        claimed,
        RuntimeError("provider reconciliation required"),
        path=db_path,
    )
    evidence_digest = unknown_delivery_evidence_digest(
        claimed.event_id,
        "slack",
        path=db_path,
    )
    readback = {
        "event_id": claimed.event_id,
        "channel": "slack",
        "provider_outcome": "accepted",
        "provider_reference": "slack:history:fixture-message-1",
    }

    receipt = record_channel_reconciliation(
        claimed.event_id,
        "slack",
        reconciliation_id="fixture-reconcile-success-1",
        expected_unknown_evidence_digest=evidence_digest,
        expected_original_provider_reference=unknown_ref,
        terminal_outcome="reconciled_succeeded",
        provider_reference="slack:history:fixture-message-1",
        readback_result=readback,
        artifact_digest="sha256:" + ("1" * 64),
        provider_idempotency_key="fixture-reconcile-success-1",
        path=db_path,
    )
    replay = record_channel_reconciliation(
        claimed.event_id,
        "slack",
        reconciliation_id="fixture-reconcile-success-1",
        expected_unknown_evidence_digest=evidence_digest,
        expected_original_provider_reference=unknown_ref,
        terminal_outcome="reconciled_succeeded",
        provider_reference="slack:history:fixture-message-1",
        readback_result=readback,
        artifact_digest="sha256:" + ("1" * 64),
        provider_idempotency_key="fixture-reconcile-success-1",
        path=db_path,
    )

    assert replay == receipt
    assert (
        get_channel_reconciliation_receipt(
            claimed.event_id,
            "slack",
            path=db_path,
        )
        == receipt
    )
    assert receipt["schema"] == "ChannelReconciliationReceiptV1"
    assert receipt["action_id"] == "fixture-reconcile-success-1"
    assert receipt["target"] == {
        "event_id": claimed.event_id,
        "channel": "slack",
    }
    assert receipt["original_unknown_evidence_digest"] == evidence_digest
    assert receipt["original_provider_reference"] == unknown_ref
    assert receipt["terminal_outcome"] == "reconciled_succeeded"
    assert receipt["provider_reference"] == "slack:history:fixture-message-1"
    assert receipt["readback_result"] == readback
    assert str(receipt["readback_digest"]).startswith("sha256:")
    assert get_channel_state(claimed.event_id, "slack", path=db_path) == "outcome_unknown"
    channel_receipt = get_channel_receipts(claimed.event_id, "slack", path=db_path)
    assert channel_receipt["destination_ref"] == unknown_ref
    stored = get_event(claimed.event_id, path=db_path)
    assert stored is not None
    assert stored.status == "reconciled_succeeded"
    assert claim_next_due_event(path=db_path) is None
    assert collect_health(path=db_path)["reconciliation_required_count"] == 0
    with pytest.raises(ValueError, match="no longer matches"):
        mark_delivered(
            claimed.event_id,
            expected_attempt_count=claimed.attempt_count,
            outcome="processed",
            classified_level=claimed.event.classified_level,
            path=db_path,
        )
    with pytest.raises(ValueError, match="no longer matches"):
        record_processing_failure(
            claimed,
            RuntimeError("stale worker"),
            path=db_path,
        )
    with pytest.raises(ValueError, match="no longer matches"):
        record_processing_deferred(
            claimed,
            datetime.now(UTC) + timedelta(minutes=1),
            path=db_path,
        )
    still_reconciled = get_event(claimed.event_id, path=db_path)
    assert still_reconciled is not None
    assert still_reconciled.status == "reconciled_succeeded"


def test_reconciliation_rejects_stale_binding_conflicts_and_mutation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("reconcile-conflict"), path=db_path)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    unknown_ref = "slack:webhook:http:5xx"
    record_channel_state(
        claimed.event_id,
        "slack",
        "outcome_unknown",
        path=db_path,
        destination_ref=unknown_ref,
        error_category="outcome_unknown",
    )
    record_processing_outcome_unknown(
        claimed,
        RuntimeError("provider reconciliation required"),
        path=db_path,
    )
    evidence_digest = unknown_delivery_evidence_digest(
        claimed.event_id,
        "slack",
        path=db_path,
    )
    readback = {
        "event_id": claimed.event_id,
        "channel": "slack",
        "provider_outcome": "absent",
        "provider_reference": "slack:history-query:fixture-2",
    }

    with pytest.raises(ValueError, match="evidence digest"):
        record_channel_reconciliation(
            claimed.event_id,
            "slack",
            reconciliation_id="fixture-reconcile-conflict-1",
            expected_unknown_evidence_digest="sha256:" + ("0" * 64),
            expected_original_provider_reference=unknown_ref,
            terminal_outcome="reconciled_absent",
            provider_reference="slack:history-query:fixture-2",
            readback_result=readback,
            artifact_digest="sha256:" + ("2" * 64),
            provider_idempotency_key="fixture-reconcile-conflict-1",
            path=db_path,
        )
    with pytest.raises(ValueError, match="original provider reference"):
        record_channel_reconciliation(
            claimed.event_id,
            "slack",
            reconciliation_id="fixture-reconcile-conflict-1",
            expected_unknown_evidence_digest=evidence_digest,
            expected_original_provider_reference="slack:webhook:other",
            terminal_outcome="reconciled_absent",
            provider_reference="slack:history-query:fixture-2",
            readback_result=readback,
            artifact_digest="sha256:" + ("2" * 64),
            provider_idempotency_key="fixture-reconcile-conflict-1",
            path=db_path,
        )
    changed_readback = {**readback, "provider_outcome": "accepted"}
    with pytest.raises(ValueError, match="provider_outcome"):
        record_channel_reconciliation(
            claimed.event_id,
            "slack",
            reconciliation_id="fixture-reconcile-conflict-1",
            expected_unknown_evidence_digest=evidence_digest,
            expected_original_provider_reference=unknown_ref,
            terminal_outcome="reconciled_absent",
            provider_reference="slack:history-query:fixture-2",
            readback_result=changed_readback,
            artifact_digest="sha256:" + ("2" * 64),
            provider_idempotency_key="fixture-reconcile-conflict-1",
            path=db_path,
        )

    receipt = record_channel_reconciliation(
        claimed.event_id,
        "slack",
        reconciliation_id="fixture-reconcile-conflict-1",
        expected_unknown_evidence_digest=evidence_digest,
        expected_original_provider_reference=unknown_ref,
        terminal_outcome="reconciled_absent",
        provider_reference="slack:history-query:fixture-2",
        readback_result=readback,
        artifact_digest="sha256:" + ("2" * 64),
        provider_idempotency_key="fixture-reconcile-conflict-1",
        path=db_path,
    )
    assert receipt["terminal_outcome"] == "reconciled_absent"
    with pytest.raises(ValueError, match="already has reconciliation"):
        record_channel_reconciliation(
            claimed.event_id,
            "slack",
            reconciliation_id="fixture-reconcile-conflict-2",
            expected_unknown_evidence_digest=evidence_digest,
            expected_original_provider_reference=unknown_ref,
            terminal_outcome="reconciled_absent",
            provider_reference="slack:history-query:fixture-2",
            readback_result=readback,
            artifact_digest="sha256:" + ("2" * 64),
            provider_idempotency_key="fixture-reconcile-conflict-2",
            path=db_path,
        )
    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "UPDATE channel_reconciliation_receipts "
                "SET provider_reference = 'changed' WHERE reconciliation_id = ?",
                ("fixture-reconcile-conflict-1",),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "DELETE FROM channel_reconciliation_receipts WHERE reconciliation_id = ?",
                ("fixture-reconcile-conflict-1",),
            )
        with pytest.raises(sqlite3.IntegrityError, match="outcome_unknown evidence"):
            conn.execute(
                "UPDATE channel_deliveries SET state = 'accepted' "
                "WHERE event_id = ? AND channel = 'slack'",
                (claimed.event_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="outcome_unknown evidence"):
            conn.execute(
                "DELETE FROM channel_deliveries WHERE event_id = ? AND channel = 'slack'",
                (claimed.event_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="status is terminal"):
            conn.execute(
                "UPDATE durable_events SET status = 'queued' WHERE event_id = ?",
                (claimed.event_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="evidence is append-only"):
            conn.execute(
                "DELETE FROM durable_events WHERE event_id = ?",
                (claimed.event_id,),
            )


def test_durable_deferral_survives_restart_without_consuming_attempt_budget(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("deferred-event"), path=db_path)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    retry_at = datetime.now(UTC) + timedelta(hours=2)

    record_processing_deferred(claimed, retry_at, path=db_path)

    stored = get_event("deferred-event", path=db_path)
    assert stored is not None
    assert stored.status == "retry_scheduled"
    assert stored.attempt_count == 0
    assert stored.next_attempt_at == retry_at.isoformat()
    assert claim_next_due_event(path=db_path) is None


def test_max_attempt_failure_moves_to_dead_letter(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event(), path=db_path, max_attempts=1)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None

    status = record_processing_failure(claimed, RuntimeError("permanent"), path=db_path)

    stored = get_event(claimed.event_id, path=db_path)
    assert status == "dead_lettered"
    assert stored is not None
    assert stored.status == "dead_lettered"
    assert stored.dead_lettered_at is not None
    health = collect_health(path=db_path)
    assert health["status"] == "degraded"
    assert health["dead_letter_count"] == 1
    assert health["recent_dead_letter_count"] == 1


def test_old_unresolved_dead_letters_continue_degrading_health(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event(), path=db_path, max_attempts=1)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    record_processing_failure(claimed, RuntimeError("old failure"), path=db_path)
    old_dead_lettered_at = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE durable_events
            SET dead_lettered_at = ?, updated_at = ?
            WHERE event_id = ?
            """,
            (old_dead_lettered_at, old_dead_lettered_at, claimed.event_id),
        )

    health = collect_health(path=db_path)

    assert health["status"] == "degraded"
    assert health["dead_letter_count"] == 1
    assert health["recent_dead_letter_count"] == 0
    assert "disposition every unresolved" in health["next_action"]


def test_dead_letter_disposition_clears_actionable_health_without_deleting_history(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("dead-history"), path=db_path, max_attempts=1)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    record_processing_failure(claimed, RuntimeError("permanent"), path=db_path)

    disposition_dead_letter("dead-history", "operator_reviewed", "ticket:test-1", path=db_path)

    health = collect_health(path=db_path)
    assert health["status"] == "ok"
    assert health["dead_letter_count"] == 1
    assert health["unresolved_dead_letter_count"] == 0
    assert get_event("dead-history", path=db_path) is not None


def test_schema_migration_is_additive_and_preserves_existing_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    event = _event("legacy-row").model_copy(update={"producer": None, "required_destinations": []})
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE durable_events (
                event_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 5,
                next_attempt_at TEXT, lease_until TEXT, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, processed_at TEXT, dead_lettered_at TEXT,
                last_error TEXT, last_error_type TEXT, source TEXT NOT NULL, project TEXT,
                level TEXT NOT NULL, classified_level TEXT, title TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO durable_events "
            "(event_id, payload_json, status, created_at, updated_at, source, level, title) "
            "VALUES (?, ?, 'queued', ?, ?, ?, ?, ?)",
            (
                event.event_id,
                event.model_dump_json(),
                now,
                now,
                event.source,
                event.level,
                event.title,
            ),
        )

    init_schema(db_path)

    assert get_event("legacy-row", path=db_path) is not None
    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(durable_events)")}
        channel_columns = {row[1] for row in conn.execute("PRAGMA table_info(channel_deliveries)")}
        reconciliation_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(channel_reconciliation_receipts)")
        }
        version = conn.execute(
            "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
        ).fetchone()
    assert {
        "payload_digest",
        "dead_letter_disposition",
        "dead_letter_disposition_ref",
        "dead_letter_dispositioned_at",
    } <= columns
    assert {
        "acceptance_receipt",
        "delivery_receipt",
        "observation_receipt",
        "terminal_disposition",
        "backoff_until",
        "last_error_category",
    } <= channel_columns
    assert {
        "reconciliation_id",
        "event_id",
        "channel",
        "original_unknown_evidence_digest",
        "terminal_outcome",
        "provider_reference",
        "readback_digest",
        "receipt_json",
    } <= reconciliation_columns
    assert version == ("7",)

    assert claim_next_due_event(path=db_path) is None
    quarantined = get_event("legacy-row", path=db_path)
    assert quarantined is not None
    assert quarantined.status == "dead_lettered"
    assert quarantined.last_error_type == "MissingEventAuthority"


def test_retention_preserves_delivery_history_and_unresolved_dead_letters(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    old = datetime.now(UTC) - timedelta(days=120)
    enqueue_event(_event("processed-with-receipt"), path=db_path)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    record_channel_state(claimed.event_id, "slack", "accepted", path=db_path)
    mark_delivered(
        claimed.event_id,
        expected_attempt_count=claimed.attempt_count,
        outcome="processed",
        classified_level=claimed.event.classified_level,
        path=db_path,
    )
    enqueue_event(_event("unresolved-dead"), path=db_path, max_attempts=1)
    dead = claim_next_due_event(path=db_path)
    assert dead is not None
    record_processing_failure(dead, RuntimeError("poison"), path=db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE durable_events SET processed_at = ?, dead_lettered_at = "
            "CASE WHEN status = 'dead_lettered' THEN ? ELSE dead_lettered_at END",
            (old.isoformat(), old.isoformat()),
        )

    prune_retained_events(
        path=db_path,
        now=datetime.now(UTC),
        processed_retention_days=1,
        processed_retention_rows=0,
        dead_letter_retention_days=1,
    )

    # 120 days is inside the 180-day delivery-history window, so the delivered
    # event is retained; the unresolved dead-letter has no disposition and is
    # retained regardless of window.
    assert get_event("processed-with-receipt", path=db_path) is not None
    assert get_event("unresolved-dead", path=db_path) is not None


def test_delivery_history_ages_out_past_the_retention_window(tmp_path: Path) -> None:
    """The delivered row class is now bounded: a delivered event older than the
    delivery-history window is aged out, while one inside the window survives."""
    db_path = tmp_path / "inbox.sqlite3"

    def _deliver(event_id: str) -> str:
        enqueue_event(_event(event_id), path=db_path)
        claimed = claim_next_due_event(path=db_path)
        assert claimed is not None
        record_channel_state(claimed.event_id, "slack", "accepted", path=db_path)
        mark_delivered(
            claimed.event_id,
            expected_attempt_count=claimed.attempt_count,
            outcome="processed",
            classified_level=claimed.event.classified_level,
            path=db_path,
        )
        return claimed.event_id

    old_id = _deliver("delivered-old")
    recent_id = _deliver("delivered-recent")
    now = datetime.now(UTC)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE durable_events SET processed_at = ? WHERE event_id = ?",
            ((now - timedelta(days=200)).isoformat(), old_id),
        )
        conn.execute(
            "UPDATE durable_events SET processed_at = ? WHERE event_id = ?",
            ((now - timedelta(days=30)).isoformat(), recent_id),
        )

    deleted = prune_retained_events(path=db_path, now=now)

    assert deleted == 1
    assert get_event("delivered-old", path=db_path) is None
    assert get_event("delivered-recent", path=db_path) is not None


def test_delivery_age_out_preserves_immutable_outcome_unknown_evidence(
    tmp_path: Path,
) -> None:
    """An event carrying trigger-immutable outcome_unknown evidence is never aged
    out, even long past the delivery-history window, and its evidence is intact."""
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("unknown-outcome"), path=db_path)
    claimed = claim_next_due_event(path=db_path, lease_seconds=-1)
    assert claimed is not None
    record_channel_state(
        claimed.event_id,
        "slack",
        "outcome_unknown",
        path=db_path,
        destination_ref="slack:webhook:transport:response_unknown",
    )
    now = datetime.now(UTC)
    with sqlite3.connect(db_path) as conn:
        # Force the event into the age-out's candidate status while it still carries
        # outcome_unknown evidence, so survival is proven by the outcome_unknown
        # exclusion clause itself — not merely by the status filter. Drop that clause
        # and this prune ABORTs on the trigger-protected delivery row.
        conn.execute(
            "UPDATE durable_events SET status = 'processed', processed_at = ? WHERE event_id = ?",
            ((now - timedelta(days=400)).isoformat(), claimed.event_id),
        )

    deleted = prune_retained_events(path=db_path, now=now)

    assert deleted == 0
    assert get_event("unknown-outcome", path=db_path) is not None
    assert get_channel_state(claimed.event_id, "slack", path=db_path) == "outcome_unknown"


def test_suppressed_event_is_persisted_as_terminal_state(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event(), path=db_path)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None

    mark_delivered(
        claimed.event_id,
        expected_attempt_count=claimed.attempt_count,
        outcome="suppressed",
        classified_level=claimed.event.classified_level,
        path=db_path,
    )

    health = collect_health(path=db_path)
    stored = get_event(claimed.event_id, path=db_path)
    assert stored is not None
    assert stored.status == "suppressed"
    assert health["suppressed_count"] == 1


def test_health_degrades_for_stale_backlog(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("old"), path=db_path)
    old_created_at = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE durable_events SET created_at = ? WHERE event_id = ?",
            (old_created_at, "old"),
        )

    health = collect_health(path=db_path)

    assert health["status"] == "degraded"
    assert health["oldest_pending_age_seconds"] is not None


def test_health_ignores_old_retry_scheduled_for_a_future_deferral(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("future-deferral"), path=db_path)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    retry_at = datetime.now(UTC) + timedelta(minutes=10)
    record_processing_deferred(claimed, retry_at, path=db_path)
    old_created_at = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE durable_events SET created_at = ? WHERE event_id = ?",
            (old_created_at, claimed.event_id),
        )

    health = collect_health(path=db_path)

    assert health["status"] == "ok"
    assert health["retry_scheduled_count"] == 1
    assert health["oldest_pending_age_seconds"] is not None
    assert health["oldest_pending_age_seconds"] > durable_inbox.BACKLOG_DEGRADED_AFTER_SECONDS
    assert health["next_action"] == ("Deferred events are waiting for their scheduled retry times.")


def test_health_degrades_for_retry_overdue_beyond_backlog_threshold(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("overdue-retry"), path=db_path)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    record_processing_deferred(
        claimed,
        datetime.now(UTC) + timedelta(minutes=10),
        path=db_path,
    )
    overdue_at = (
        datetime.now(UTC) - timedelta(seconds=durable_inbox.BACKLOG_DEGRADED_AFTER_SECONDS + 1)
    ).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE durable_events SET next_attempt_at = ? WHERE event_id = ?",
            (overdue_at, claimed.event_id),
        )

    health = collect_health(path=db_path)

    assert health["status"] == "degraded"
    assert health["retry_scheduled_count"] == 1
    assert health["next_action"] == (
        "Inspect the durable inbox worker; due events are not draining."
    )


def test_exhausted_attempts_with_one_accepted_channel_are_partially_delivered(
    tmp_path: Path,
) -> None:
    """The live case: Slack accepted, push could not raise a macOS notification.

    Between 2026-08-24 and 2026-09-03, 98 such events were filed as dead letters,
    so the dead-letter count read as "reached nobody" while every one of them had
    reached Slack.
    """
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("partial"), path=db_path, max_attempts=1)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    record_channel_state(claimed.event_id, "slack", "accepted", path=db_path)
    record_channel_state(claimed.event_id, "push", "failed", path=db_path)

    status = record_processing_failure(claimed, RuntimeError("push refused"), path=db_path)

    stored = get_event(claimed.event_id, path=db_path)
    assert status == "partially_delivered"
    assert stored is not None
    assert stored.status == "partially_delivered"
    assert stored.dead_lettered_at is not None  # terminal timestamp, both outcomes
    health = collect_health(path=db_path)
    assert health["partially_delivered_count"] == 1
    assert health["unresolved_partially_delivered_count"] == 1
    assert health["dead_letter_count"] == 0
    assert health["status"] == "degraded"
    # A single fresh failure is not yet an outage, so the instruction is still per-event.
    assert "partially delivered" in health["next_action"]


def test_exhausted_attempts_with_no_accepted_channel_stay_dead_lettered(
    tmp_path: Path,
) -> None:
    """The distinction only means something if the old outcome still happens."""
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("nobody"), path=db_path, max_attempts=1)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    record_channel_state(claimed.event_id, "slack", "failed", path=db_path)
    record_channel_state(claimed.event_id, "push", "failed", path=db_path)

    status = record_processing_failure(claimed, RuntimeError("both refused"), path=db_path)

    assert status == "dead_lettered"
    health = collect_health(path=db_path)
    assert health["dead_letter_count"] == 1
    assert health["partially_delivered_count"] == 0


def test_a_buffered_channel_is_not_an_acceptance(tmp_path: Path) -> None:
    """`buffered` is policy deferral, not a receipt; it must not soften the outcome."""
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("buffered-only"), path=db_path, max_attempts=1)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    record_channel_state(claimed.event_id, "push", "buffered", path=db_path)

    status = record_processing_failure(claimed, RuntimeError("never sent"), path=db_path)

    assert status == "dead_lettered"


def test_partially_delivered_events_can_be_dispositioned(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("partial-disposition"), path=db_path, max_attempts=1)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    record_channel_state(claimed.event_id, "slack", "accepted", path=db_path)
    record_processing_failure(claimed, RuntimeError("push refused"), path=db_path)

    disposition_dead_letter(
        claimed.event_id, "accepted-by-slack", "operator:2026-09-03", path=db_path
    )

    health = collect_health(path=db_path)
    assert health["partially_delivered_count"] == 1
    assert health["unresolved_partially_delivered_count"] == 0
    stored = get_event(claimed.event_id, path=db_path)
    assert stored is not None
    assert stored.status == "partially_delivered"


def test_partial_deliveries_are_retained_exactly_like_dead_letters(tmp_path: Path) -> None:
    """Retention refuses to delete any row owning a channel receipt (ADR 0003).

    A partially delivered event owns one by definition, so it is retained for the same
    reason a dead letter with receipts is, not for a new one.
    """
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("partial-retention"), path=db_path, max_attempts=1)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    record_channel_state(claimed.event_id, "slack", "accepted", path=db_path)
    record_processing_failure(claimed, RuntimeError("push refused"), path=db_path)
    disposition_dead_letter(claimed.event_id, "accepted-by-slack", "operator:test", path=db_path)
    old = (datetime.now(UTC) - timedelta(days=400)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE durable_events SET dead_lettered_at = ?, updated_at = ? WHERE event_id = ?",
            (old, old, claimed.event_id),
        )

    prune_retained_events(path=db_path)

    stored = get_event(claimed.event_id, path=db_path)
    assert stored is not None
    assert stored.status == "partially_delivered"


def _partial_via_channel(
    event_id: str, db_path: Path, *, failed: str = "push", accepted: str = "slack"
) -> str:
    """Drive one event to `partially_delivered` with a single failing channel."""
    enqueue_event(_event(event_id), path=db_path, max_attempts=1)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    record_channel_state(claimed.event_id, accepted, "accepted", path=db_path)
    record_channel_state(claimed.event_id, failed, "failed", path=db_path)
    record_processing_failure(claimed, RuntimeError(f"{failed} refused"), path=db_path)
    return claimed.event_id


def _age_failures(db_path: Path, channel: str, *, hours: int = 3) -> None:
    """Backdate a channel's failures so they read as sustained, not momentary."""
    old = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE channel_deliveries SET updated_at = ? WHERE channel = ? AND state = 'failed'",
            (old, channel),
        )


def test_a_channel_that_stopped_accepting_is_named_as_the_outage(tmp_path: Path) -> None:
    """Ninety-eight partials had one cause. Health must point at the cause, not the pile."""
    db_path = tmp_path / "inbox.sqlite3"
    for index in range(3):
        _partial_via_channel(f"outage-{index}", db_path)
    _age_failures(db_path, "push")

    outages = channel_outage_summary(path=db_path)

    assert [outage["channel"] for outage in outages] == ["push"]
    assert outages[0]["failures_since_last_acceptance"] == 3
    assert outages[0]["unresolved_partials"] == 3
    assert outages[0]["last_accepted_at"] is None
    assert outages[0]["failing_since"] is not None
    health = collect_health(path=db_path)
    assert health["status"] == "degraded"
    assert "Channel push" in health["next_action"]
    assert health["failing_channels"] == list(outages)


def test_a_channel_that_accepted_after_it_failed_is_not_an_outage(tmp_path: Path) -> None:
    """An outage is failure *since* the last acceptance, not any failure ever seen."""
    db_path = tmp_path / "inbox.sqlite3"
    _partial_via_channel("recovered", db_path)
    _age_failures(db_path, "push")
    enqueue_event(_event("later"), path=db_path, max_attempts=1)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    record_channel_state(claimed.event_id, "push", "accepted", path=db_path)

    assert channel_outage_summary(path=db_path) == ()


def test_one_recent_failure_on_a_working_channel_is_not_an_outage(tmp_path: Path) -> None:
    """Every event here fails one channel, so a bare failure test flags the healthy one too."""
    db_path = tmp_path / "inbox.sqlite3"
    _partial_via_channel("momentary", db_path)

    outages = channel_outage_summary(path=db_path)

    # Slack accepted a moment ago and push has only just started failing: neither is silent
    # long enough to be worth an operator's attention yet.
    assert outages == ()
    assert collect_health(path=db_path)["failing_channels"] == []


def test_a_dispositioned_receipt_counts_as_acceptance(tmp_path: Path) -> None:
    """`dispositioned` is a positive receipt that never writes `accepted_at`."""
    db_path = tmp_path / "inbox.sqlite3"
    _partial_via_channel("dispositioned-receipt", db_path)
    _age_failures(db_path, "push")
    enqueue_event(_event("push-took-it"), path=db_path, max_attempts=1)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    record_channel_state(claimed.event_id, "push", "dispositioned", path=db_path)

    assert channel_outage_summary(path=db_path) == ()


def test_one_disposition_resolves_every_partial_the_outage_produced(tmp_path: Path) -> None:
    """One channel outage is one operator decision, so it takes one disposition."""
    db_path = tmp_path / "inbox.sqlite3"
    expected = [_partial_via_channel(f"sweep-{index}", db_path) for index in range(3)]

    resolved = disposition_partial_deliveries_for_channel(
        "push", "push_outage_slack_delivered", "operator:2026-09-03", path=db_path
    )

    assert sorted(resolved) == sorted(expected)
    health = collect_health(path=db_path)
    assert health["partially_delivered_count"] == 3
    assert health["unresolved_partially_delivered_count"] == 0
    for event_id in expected:
        stored = get_event(event_id, path=db_path)
        assert stored is not None
        assert stored.status == "partially_delivered"  # history is resolved, never deleted


def test_a_partial_with_a_second_failing_channel_is_not_swept_up(tmp_path: Path) -> None:
    """A bulk disposition may only claim events the named outage fully explains."""
    db_path = tmp_path / "inbox.sqlite3"
    push_only = _partial_via_channel("push-only", db_path)
    enqueue_event(_event("two-failures"), path=db_path, max_attempts=1)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    record_channel_state(claimed.event_id, "slack", "accepted", path=db_path)
    record_channel_state(claimed.event_id, "push", "failed", path=db_path)
    record_channel_state(claimed.event_id, "email", "failed", path=db_path)
    record_processing_failure(claimed, RuntimeError("two refused"), path=db_path)

    resolved = disposition_partial_deliveries_for_channel(
        "push", "push_outage", "operator:test", path=db_path
    )

    assert resolved == (push_only,)
    health = collect_health(path=db_path)
    assert health["unresolved_partially_delivered_count"] == 1


def test_a_dead_letter_is_never_swept_up_by_a_channel_disposition(tmp_path: Path) -> None:
    """The dead-letter count means "reached nobody". A bulk resolve must not touch it."""
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("nobody-got-it"), path=db_path, max_attempts=1)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    record_channel_state(claimed.event_id, "push", "failed", path=db_path)
    record_processing_failure(claimed, RuntimeError("push refused"), path=db_path)

    assert (
        disposition_partial_deliveries_for_channel(
            "push", "push_outage", "operator:test", path=db_path
        )
        == ()
    )
    health = collect_health(path=db_path)
    assert health["unresolved_dead_letter_count"] == 1


def test_the_population_counted_is_the_population_resolved(tmp_path: Path) -> None:
    """An operator acts on the number they were shown, so the two must be one query."""
    db_path = tmp_path / "inbox.sqlite3"
    for index in range(4):
        _partial_via_channel(f"parity-{index}", db_path)
    _age_failures(db_path, "push")

    outage = channel_outage_summary(path=db_path)[0]
    listed = partial_deliveries_for_channel("push", path=db_path)
    resolved = disposition_partial_deliveries_for_channel(
        "push", "push_outage", "operator:test", path=db_path
    )

    assert outage["unresolved_partials"] == len(listed) == len(resolved) == 4
    assert sorted(listed) == sorted(resolved)


def test_until_bounds_a_bulk_disposition(tmp_path: Path) -> None:
    """An operator resolving a closed outage must not silently claim events after it."""
    db_path = tmp_path / "inbox.sqlite3"
    old_event = _partial_via_channel("before-cutoff", db_path)
    recent_event = _partial_via_channel("after-cutoff", db_path)
    old = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE durable_events SET dead_lettered_at = ? WHERE event_id = ?",
            (old, old_event),
        )
    cutoff = (datetime.now(UTC) - timedelta(days=1)).isoformat()

    resolved = disposition_partial_deliveries_for_channel(
        "push", "push_outage", "operator:test", until=cutoff, path=db_path
    )

    assert resolved == (old_event,)
    stored = get_event(recent_event, path=db_path)
    assert stored is not None
    assert collect_health(path=db_path)["unresolved_partially_delivered_count"] == 1


@pytest.mark.parametrize(
    ("channel", "disposition", "reference"),
    [("", "reason", "ref"), ("push", "  ", "ref"), ("push", "reason", "")],
)
def test_a_bulk_disposition_still_demands_a_channel_reason_and_reference(
    tmp_path: Path, channel: str, disposition: str, reference: str
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    _partial_via_channel("needs-args", db_path)

    with pytest.raises(ValueError):
        disposition_partial_deliveries_for_channel(channel, disposition, reference, path=db_path)

    assert collect_health(path=db_path)["unresolved_partially_delivered_count"] == 1


def test_an_outage_is_still_named_behind_a_higher_ranked_backlog(tmp_path: Path) -> None:
    """Push was silent for ten days behind a dead-letter backlog that outranked it."""
    db_path = tmp_path / "inbox.sqlite3"
    _partial_via_channel("outage-under-backlog", db_path)
    _age_failures(db_path, "push")
    enqueue_event(_event("reached-nobody"), path=db_path, max_attempts=1)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    record_channel_state(claimed.event_id, "slack", "failed", path=db_path)
    record_processing_failure(claimed, RuntimeError("both refused"), path=db_path)

    health = collect_health(path=db_path)

    assert health["unresolved_dead_letter_count"] == 1  # outranks the partial review
    assert "unresolved dead-lettered event" in health["next_action"]
    assert "Channel push" in health["next_action"]  # and the outage is still said out loud
    # Slack failed this one event moments ago; it is working, and must not be named too.
    assert [outage["channel"] for outage in health["failing_channels"]] == ["push"]


def test_an_already_dispositioned_event_is_not_reclaimed_or_overwritten(tmp_path: Path) -> None:
    """A bulk sweep must never replace a reason an operator already wrote for one event."""
    db_path = tmp_path / "inbox.sqlite3"
    already = _partial_via_channel("already-resolved", db_path)
    still_open = _partial_via_channel("still-open", db_path)
    disposition_dead_letter(already, "reviewed_by_hand", "ticket:41", path=db_path)

    resolved = disposition_partial_deliveries_for_channel(
        "push", "push_outage", "operator:test", path=db_path
    )

    assert resolved == (still_open,)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT dead_letter_disposition, dead_letter_disposition_ref "
            "FROM durable_events WHERE event_id = ?",
            (already,),
        ).fetchone()
    assert row == ("reviewed_by_hand", "ticket:41")


def test_an_unparseable_cutoff_is_refused_rather_than_compared_as_text(tmp_path: Path) -> None:
    """SQLite matches `dead_lettered_at <= 'yesterday'` on every row, widening the sweep."""
    db_path = tmp_path / "inbox.sqlite3"
    _partial_via_channel("cutoff-typo", db_path)

    with pytest.raises(ValueError, match="ISO-8601"):
        disposition_partial_deliveries_for_channel(
            "push", "push_outage", "operator:test", until="yesterday", path=db_path
        )

    assert collect_health(path=db_path)["unresolved_partially_delivered_count"] == 1


def test_a_channel_awaiting_reconciliation_is_not_a_failure_to_sweep(tmp_path: Path) -> None:
    """`outcome_unknown` is protected evidence, not a failure this outage explains."""
    db_path = tmp_path / "inbox.sqlite3"
    enqueue_event(_event("unknown-outcome"), path=db_path, max_attempts=1)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    record_channel_state(claimed.event_id, "slack", "accepted", path=db_path)
    record_channel_state(
        claimed.event_id,
        "push",
        "outcome_unknown",
        destination_ref="provider:unknown-1",
        path=db_path,
    )
    record_processing_failure(claimed, RuntimeError("push outcome unknown"), path=db_path)

    assert (
        disposition_partial_deliveries_for_channel(
            "push", "push_outage", "operator:test", path=db_path
        )
        == ()
    )
    assert collect_health(path=db_path)["unresolved_partially_delivered_count"] == 1


def test_an_offset_cutoff_is_converted_to_utc_before_it_is_compared(tmp_path: Path) -> None:
    """Rows are UTC text: `01:00+05:00` sorts above `00:00+00:00` while being earlier."""
    db_path = tmp_path / "inbox.sqlite3"
    after_cutoff = _partial_via_channel("after-offset-cutoff", db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE durable_events SET dead_lettered_at = ? WHERE event_id = ?",
            ("2026-09-03T00:30:00+00:00", after_cutoff),
        )

    # 01:00+05:00 is 20:00 UTC the previous day, so this event is after the cutoff.
    resolved = disposition_partial_deliveries_for_channel(
        "push", "push_outage", "operator:test", until="2026-09-03T01:00:00+05:00", path=db_path
    )

    assert resolved == ()
    assert collect_health(path=db_path)["unresolved_partially_delivered_count"] == 1


def test_one_failure_after_an_idle_stretch_is_not_a_sustained_outage(tmp_path: Path) -> None:
    """A stale acceptance plus a fresh failure is a transient failure, not silence."""
    db_path = tmp_path / "inbox.sqlite3"
    _partial_via_channel("idle-then-failed", db_path)
    stale = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE channel_deliveries SET accepted_at = ?, updated_at = ? "
            "WHERE channel = 'slack' AND state = 'accepted'",
            (stale, stale),
        )
        conn.execute(
            "UPDATE channel_deliveries SET accepted_at = ?, updated_at = ?, state = 'accepted' "
            "WHERE channel = 'push' AND event_id = 'idle-then-failed'",
            (stale, stale),
        )
    # Push accepted three days ago and has just failed once on a new event.
    _partial_via_channel("fresh-failure", db_path)

    assert [outage["channel"] for outage in channel_outage_summary(path=db_path)] == []
