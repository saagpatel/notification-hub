"""Isolated reconciliation planning and approval-boundary tests."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

import notification_hub.reconciliation as reconciliation_module
from notification_hub.durable_inbox import (
    claim_next_due_event,
    enqueue_event,
    get_event,
    record_channel_reconciliation,
    record_channel_state,
    record_processing_outcome_unknown,
)
from notification_hub.models import StoredEvent
from notification_hub.reconciliation import (
    ACTION_KIND,
    REQUIRED_READBACK,
    SUPERSESSION_ACTION_KIND,
    SUPERSESSION_REQUIRED_READBACK,
    ReconciliationAuthorizationError,
    apply_reconciliation_plan,
    apply_reconciliation_receipt_supersession_plan,
    build_reconciliation_plan,
    build_reconciliation_receipt_supersession_plan,
    canonical_json,
    finalize_reconciliation_claim,
    load_readback_file,
    sha256_json,
)

from tests.envelope_support import (  # noqa: E402
    SHARED_ENVELOPE_MODULE,
    requires_shared_envelope_module,
)

pytestmark = requires_shared_envelope_module


def _unknown_event(db_path: Path, event_id: str = "fixture:reconcile:1") -> StoredEvent:
    event = StoredEvent(
        event_id=event_id,
        source="codex",
        level="normal",
        title="Fixture reconciliation",
        body="Isolated provider readback only.",
    )
    enqueue_event(event, path=db_path)
    claimed = claim_next_due_event(path=db_path)
    assert claimed is not None
    record_channel_state(
        event_id,
        "slack",
        "outcome_unknown",
        path=db_path,
        destination_ref="slack:webhook:transport:response_unknown",
        error_category="outcome_unknown",
    )
    record_processing_outcome_unknown(
        claimed,
        RuntimeError("fixture response lost"),
        path=db_path,
    )
    return event


def _readback(event_id: str, provider_reference: str) -> dict[str, object]:
    return {
        "event_id": event_id,
        "channel": "slack",
        "provider_outcome": "accepted",
        "provider_reference": provider_reference,
    }


def _write_envelope(path: Path, plan: dict[str, object], *, expires_delta: int = 300) -> None:
    now = datetime.now(UTC)
    issued_at = (
        now - timedelta(minutes=2)
        if expires_delta < 0
        else now - timedelta(seconds=1)
    )
    targets = plan["canonical_targets"]
    assert isinstance(targets, dict)
    payload = {
        "schema": "IrreversibleActionEnvelopeV1",
        "action_id": "fixture.notification.reconcile.0001",
        "action_kind": ACTION_KIND,
        "principal": {"id": "fixture-operator", "kind": "test-fixture"},
        "canonical_targets": targets,
        "source_revision": plan["source_revision"],
        "artifact_digest": plan["plan_digest"],
        "bounds": {"allowed_effect_count": 1},
        "issued_at": issued_at.isoformat(),
        "expires_at": (now + timedelta(seconds=expires_delta)).isoformat(),
        "one_shot": True,
        "provider_idempotency_key": "fixture.notification.reconcile.0001",
        "preconditions": {
            "unknown_evidence_digest": targets["unknown_evidence_digest"],
            "original_provider_reference": targets["original_provider_reference"],
        },
        "required_readback": REQUIRED_READBACK,
        "receipt_requirements": {
            "schema": "IrreversibleActionReceiptV1",
            "provider_reference": True,
            "readback_result": True,
            "terminal_outcome": True,
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)


def _mutate_envelope(path: Path, **updates: object) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    payload.update(updates)
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)


def _write_historical_consumed_claim(
    envelope_path: Path,
    claim_state_dir: Path,
    *,
    plan: dict[str, object] | None = None,
    claim_after_expiry: bool = False,
) -> None:
    anchor = datetime.now(UTC) - timedelta(days=1)
    payload = json.loads(envelope_path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    payload["issued_at"] = (anchor - timedelta(seconds=30)).isoformat()
    payload["expires_at"] = (anchor + timedelta(seconds=30)).isoformat()
    envelope_path.write_text(json.dumps(payload), encoding="utf-8")
    envelope_path.chmod(0o600)
    claimed_at = anchor + timedelta(seconds=60) if claim_after_expiry else anchor
    claim = {
        "schema": "IrreversibleActionClaimV1",
        "action_id": payload["action_id"],
        "action_kind": payload["action_kind"],
        "envelope_digest": sha256_json(payload),
        "claimed_at": claimed_at.isoformat().replace("+00:00", "Z"),
    }
    claim_state_dir.mkdir(mode=0o700, exist_ok=True)
    with reconciliation_module._action_execution_lock(
        claim_state_dir,
        str(payload["action_id"]),
        exclusive=True,
    ):
        pass
    claim_path = claim_state_dir / f"{payload['action_id']}.claim.json"
    claim_path.write_text(json.dumps(claim), encoding="utf-8")
    claim_path.chmod(0o600)
    if plan is not None:
        plan_path = claim_state_dir / f"{payload['action_id']}.plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        plan_path.chmod(0o600)


def _write_supersession_envelope(
    path: Path,
    plan: dict[str, object],
) -> None:
    now = datetime.now(UTC)
    targets = plan["canonical_targets"]
    assert isinstance(targets, dict)
    readback = plan["readback_result"]
    assert isinstance(readback, dict)
    payload = {
        "schema": "IrreversibleActionEnvelopeV1",
        "action_id": "fixture.notification.supersede.0001",
        "action_kind": SUPERSESSION_ACTION_KIND,
        "principal": {"id": "fixture-operator", "kind": "test-fixture"},
        "canonical_targets": targets,
        "source_revision": plan["source_revision"],
        "artifact_digest": plan["plan_digest"],
        "bounds": {"allowed_effect_count": 1},
        "issued_at": (now - timedelta(seconds=1)).isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
        "one_shot": True,
        "provider_idempotency_key": "fixture.notification.supersede.0001",
        "preconditions": {
            "original_authority_receipt_digest": targets[
                "original_authority_receipt_digest"
            ],
            "resolved_readback_digest": sha256_json(readback),
        },
        "required_readback": SUPERSESSION_REQUIRED_READBACK,
        "receipt_requirements": {
            "schema": "IrreversibleActionReceiptV1",
            "provider_reference": True,
            "readback_result": True,
            "terminal_outcome": True,
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)


def _outcome_unknown_authority_fixture(
    tmp_path: Path,
) -> tuple[Path, StoredEvent, dict[str, object], Path, Path]:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    _write_envelope(envelope, plan)
    with (
        patch(
            "notification_hub.reconciliation.record_channel_reconciliation",
            side_effect=sqlite3.OperationalError("fixture apply unavailable"),
        ),
        patch(
            "notification_hub.reconciliation.read_channel_reconciliation_state",
            side_effect=sqlite3.OperationalError("fixture readback unavailable"),
        ),
        pytest.raises(
            ReconciliationAuthorizationError,
            match="terminal outcome=outcome_unknown",
        ),
    ):
        apply_reconciliation_plan(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )
    receipt_path = claims / "fixture.notification.reconcile.0001.receipt.json"
    return db_path, event, plan, claims, receipt_path


def test_readback_file_must_be_owner_private_regular_file(tmp_path: Path) -> None:
    readback = tmp_path / "readback.json"
    readback.write_text('{"provider_outcome":"accepted"}', encoding="utf-8")
    readback.chmod(0o600)
    assert load_readback_file(readback) == {"provider_outcome": "accepted"}

    readback.chmod(0o640)
    with pytest.raises(ValueError, match="owner-private"):
        load_readback_file(readback)

    readback.chmod(0o600)
    link = tmp_path / "readback-link.json"
    link.symlink_to(readback)
    with pytest.raises(ValueError, match="non-symlink"):
        load_readback_file(link)


def test_plan_is_read_only_and_binds_canonical_database_target(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    before_bytes = db_path.read_bytes()
    before_mtime = db_path.stat().st_mtime_ns
    provider_reference = "slack:history:fixture-message-1"

    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )

    targets = plan["canonical_targets"]
    assert isinstance(targets, dict)
    assert plan["schema"] == "ChannelReconciliationPlanV2"
    assert targets["database_path"] == str(db_path.resolve())
    assert targets["event_id"] == event.event_id
    assert str(plan["plan_digest"]).startswith("sha256:")
    assert db_path.read_bytes() == before_bytes
    assert db_path.stat().st_mtime_ns == before_mtime


def test_apply_rejects_missing_stale_or_expired_authority_without_mutation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    missing = tmp_path / "missing-envelope.json"
    with pytest.raises(ReconciliationAuthorizationError):
        apply_reconciliation_plan(
            plan,
            envelope_path=missing,
            claim_state_dir=tmp_path / "claims-missing",
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    expired = tmp_path / "expired-envelope.json"
    _write_envelope(expired, plan, expires_delta=-1)
    with pytest.raises(ReconciliationAuthorizationError, match="not currently valid"):
        apply_reconciliation_plan(
            plan,
            envelope_path=expired,
            claim_state_dir=tmp_path / "claims-expired",
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    changed_reference = "slack:history:fixture-message-2"
    changed_plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=changed_reference,
        readback_result=_readback(event.event_id, changed_reference),
        database_path=db_path,
    )
    stale = tmp_path / "stale-envelope.json"
    _write_envelope(stale, plan)
    with pytest.raises(ReconciliationAuthorizationError, match="mismatch"):
        apply_reconciliation_plan(
            changed_plan,
            envelope_path=stale,
            claim_state_dir=tmp_path / "claims-stale",
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    stored = get_event(event.event_id, path=db_path)
    assert stored is not None
    assert stored.status == "reconciliation_required"
    assert not (tmp_path / "claims-missing").exists()
    assert not (tmp_path / "claims-expired").exists()
    assert not (tmp_path / "claims-stale").exists()


@pytest.mark.parametrize(
    ("authority_update", "error"),
    [
        ({"one_shot": False}, "must be one-shot"),
        ({"bounds": {"allowed_effect_count": 2}}, "must allow one effect"),
        ({"preconditions": {"unknown_evidence_digest": "sha256:" + ("0" * 64)}}, "preconditions mismatch"),
        ({"required_readback": ["event_id"]}, "readback contract mismatch"),
    ],
)
def test_apply_rejects_broad_or_mismatched_authority_before_claim(
    tmp_path: Path,
    authority_update: dict[str, object],
    error: str,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    _write_envelope(envelope, plan)
    before_bytes = db_path.read_bytes()
    before_mtime = db_path.stat().st_mtime_ns
    _mutate_envelope(envelope, **authority_update)

    with pytest.raises(ReconciliationAuthorizationError, match=error):
        apply_reconciliation_plan(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    assert db_path.read_bytes() == before_bytes
    assert db_path.stat().st_mtime_ns == before_mtime
    stored = get_event(event.event_id, path=db_path)
    assert stored is not None
    assert stored.status == "reconciliation_required"
    assert not claims.exists()


def test_apply_rejects_unsafe_execution_lock_before_claim_or_mutation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    claims.mkdir(mode=0o700)
    redirected = tmp_path / "redirected-lock"
    redirected.write_text("", encoding="utf-8")
    redirected.chmod(0o600)
    lock_path = claims / "fixture.notification.reconcile.0001.execution.lock"
    lock_path.symlink_to(redirected)
    _write_envelope(envelope, plan)

    with (
        patch(
            "notification_hub.reconciliation.record_channel_reconciliation"
        ) as forbidden_mutation,
        pytest.raises(
            ReconciliationAuthorizationError,
            match="regular non-symlink file",
        ),
    ):
        apply_reconciliation_plan(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    forbidden_mutation.assert_not_called()
    assert not (claims / "fixture.notification.reconcile.0001.claim.json").exists()
    assert redirected.read_text(encoding="utf-8") == ""


def test_apply_rejects_unsafe_lock_identity_before_claim_or_mutation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    claims.mkdir(mode=0o700)
    redirected = tmp_path / "redirected-identity"
    redirected.write_text("{}\n", encoding="utf-8")
    redirected.chmod(0o600)
    identity_path = (
        claims
        / "fixture.notification.reconcile.0001.execution.lock.identity.json"
    )
    identity_path.symlink_to(redirected)
    _write_envelope(envelope, plan)
    before_database = db_path.read_bytes()

    with (
        patch(
            "notification_hub.reconciliation.record_channel_reconciliation"
        ) as forbidden_mutation,
        pytest.raises(
            ReconciliationAuthorizationError,
            match="owner-private immutable evidence",
        ),
    ):
        apply_reconciliation_plan(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    forbidden_mutation.assert_not_called()
    assert db_path.read_bytes() == before_database
    assert not (claims / "fixture.notification.reconcile.0001.plan.json").exists()
    assert not (claims / "fixture.notification.reconcile.0001.claim.json").exists()
    assert redirected.read_text(encoding="utf-8") == "{}\n"


def test_identity_link_failure_stops_before_claim_and_allows_safe_retry(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    _write_envelope(envelope, plan)
    before_database = db_path.read_bytes()
    action_id = "fixture.notification.reconcile.0001"

    with (
        patch(
            "notification_hub.reconciliation.os.link",
            side_effect=OSError("fixture identity link failure"),
        ),
        patch(
            "notification_hub.reconciliation.record_channel_reconciliation"
        ) as forbidden_mutation,
        pytest.raises(OSError, match="fixture identity link failure"),
    ):
        apply_reconciliation_plan(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    forbidden_mutation.assert_not_called()
    assert db_path.read_bytes() == before_database
    assert not (claims / f"{action_id}.execution.lock.identity.json").exists()
    assert not (claims / f"{action_id}.plan.json").exists()
    assert not (claims / f"{action_id}.claim.json").exists()
    assert not (claims / f"{action_id}.receipt.json").exists()

    report = apply_reconciliation_plan(
        plan,
        envelope_path=envelope,
        claim_state_dir=claims,
        envelope_module_path=SHARED_ENVELOPE_MODULE,
    )

    authority_receipt = json.loads(
        Path(str(report["authority_receipt_path"])).read_text(encoding="utf-8")
    )
    assert report["status"] == "applied"
    assert authority_receipt["action_id"] == action_id
    assert authority_receipt["target"] == plan["canonical_targets"]
    assert authority_receipt["artifact_digest"] == plan["plan_digest"]
    assert authority_receipt["provider_reference"] == provider_reference
    receipt_readback = authority_receipt["readback_result"]
    assert isinstance(receipt_readback, dict)
    plan_readback = plan["readback_result"]
    assert isinstance(plan_readback, dict)
    assert all(receipt_readback[key] == value for key, value in plan_readback.items())
    assert authority_receipt["terminal_outcome"] == "reconciled_succeeded"


def test_identity_directory_fsync_failure_preserves_complete_identity_for_retry(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    _write_envelope(envelope, plan)
    before_database = db_path.read_bytes()
    action_id = "fixture.notification.reconcile.0001"
    original_fsync = reconciliation_module.os.fsync
    fsync_calls = 0

    def fail_directory_fsync(descriptor: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        if fsync_calls == 2:
            raise OSError("fixture identity directory fsync failure")
        original_fsync(descriptor)

    with (
        patch(
            "notification_hub.reconciliation.os.fsync",
            side_effect=fail_directory_fsync,
        ),
        patch(
            "notification_hub.reconciliation.record_channel_reconciliation"
        ) as forbidden_mutation,
        pytest.raises(OSError, match="fixture identity directory fsync failure"),
    ):
        apply_reconciliation_plan(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    forbidden_mutation.assert_not_called()
    assert db_path.read_bytes() == before_database
    identity_path = claims / f"{action_id}.execution.lock.identity.json"
    identity_before = identity_path.read_bytes()
    assert not (claims / f"{action_id}.plan.json").exists()
    assert not (claims / f"{action_id}.claim.json").exists()
    assert not (claims / f"{action_id}.receipt.json").exists()

    report = apply_reconciliation_plan(
        plan,
        envelope_path=envelope,
        claim_state_dir=claims,
        envelope_module_path=SHARED_ENVELOPE_MODULE,
    )

    authority_receipt = json.loads(
        Path(str(report["authority_receipt_path"])).read_text(encoding="utf-8")
    )
    assert report["status"] == "applied"
    assert identity_path.read_bytes() == identity_before
    assert authority_receipt["action_id"] == action_id
    assert authority_receipt["artifact_digest"] == plan["plan_digest"]
    assert authority_receipt["terminal_outcome"] == "reconciled_succeeded"


def test_plan_link_failure_stops_before_claim_and_allows_exact_retry(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    _write_envelope(envelope, plan)
    before_database = db_path.read_bytes()
    action_id = "fixture.notification.reconcile.0001"
    original_link = reconciliation_module.os.link

    def fail_plan_link(source: object, destination: object) -> None:
        if str(destination).endswith(".plan.json"):
            raise OSError("fixture plan link failure")
        original_link(source, destination)

    with (
        patch(
            "notification_hub.reconciliation.os.link",
            side_effect=fail_plan_link,
        ),
        patch(
            "notification_hub.reconciliation.record_channel_reconciliation"
        ) as forbidden_mutation,
        pytest.raises(
            ReconciliationAuthorizationError,
            match="fixture plan link failure",
        ),
    ):
        apply_reconciliation_plan(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    forbidden_mutation.assert_not_called()
    assert db_path.read_bytes() == before_database
    assert (claims / f"{action_id}.execution.lock.identity.json").exists()
    assert not (claims / f"{action_id}.plan.json").exists()
    assert not (claims / f"{action_id}.claim.json").exists()
    assert not (claims / f"{action_id}.receipt.json").exists()

    report = apply_reconciliation_plan(
        plan,
        envelope_path=envelope,
        claim_state_dir=claims,
        envelope_module_path=SHARED_ENVELOPE_MODULE,
    )

    authority_receipt = json.loads(
        Path(str(report["authority_receipt_path"])).read_text(encoding="utf-8")
    )
    assert report["status"] == "applied"
    assert authority_receipt["action_id"] == action_id
    assert authority_receipt["artifact_digest"] == plan["plan_digest"]
    assert authority_receipt["terminal_outcome"] == "reconciled_succeeded"


def test_plan_directory_fsync_failure_preserves_complete_plan_for_retry(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    _write_envelope(envelope, plan)
    before_database = db_path.read_bytes()
    action_id = "fixture.notification.reconcile.0001"
    original_fsync = reconciliation_module.os.fsync
    fsync_calls = 0

    def fail_plan_directory_fsync(descriptor: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        if fsync_calls == 4:
            raise OSError("fixture plan directory fsync failure")
        original_fsync(descriptor)

    with (
        patch(
            "notification_hub.reconciliation.os.fsync",
            side_effect=fail_plan_directory_fsync,
        ),
        patch(
            "notification_hub.reconciliation.record_channel_reconciliation"
        ) as forbidden_mutation,
        pytest.raises(
            ReconciliationAuthorizationError,
            match="fixture plan directory fsync failure",
        ),
    ):
        apply_reconciliation_plan(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    forbidden_mutation.assert_not_called()
    assert db_path.read_bytes() == before_database
    plan_path = claims / f"{action_id}.plan.json"
    plan_before = plan_path.read_bytes()
    assert json.loads(plan_before) == plan
    assert not (claims / f"{action_id}.claim.json").exists()
    assert not (claims / f"{action_id}.receipt.json").exists()

    report = apply_reconciliation_plan(
        plan,
        envelope_path=envelope,
        claim_state_dir=claims,
        envelope_module_path=SHARED_ENVELOPE_MODULE,
    )

    authority_receipt = json.loads(
        Path(str(report["authority_receipt_path"])).read_text(encoding="utf-8")
    )
    assert report["status"] == "applied"
    assert plan_path.read_bytes() == plan_before
    assert authority_receipt["action_id"] == action_id
    assert authority_receipt["artifact_digest"] == plan["plan_digest"]
    assert authority_receipt["terminal_outcome"] == "reconciled_succeeded"


def test_apply_claims_once_and_emits_plan_bound_receipt(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    _write_envelope(envelope, plan)

    report = apply_reconciliation_plan(
        plan,
        envelope_path=envelope,
        claim_state_dir=claims,
        envelope_module_path=SHARED_ENVELOPE_MODULE,
    )

    receipt = report["receipt"]
    assert isinstance(receipt, dict)
    assert report["status"] == "applied"
    assert receipt["action_id"] == "fixture.notification.reconcile.0001"
    assert receipt["artifact_digest"] == plan["plan_digest"]
    assert (
        receipt["provider_idempotency_key"]
        == "fixture.notification.reconcile.0001"
    )
    stored = get_event(event.event_id, path=db_path)
    assert stored is not None
    assert stored.status == "reconciled_succeeded"
    assert len(list(claims.glob("*.claim.json"))) == 1
    authority_receipt = json.loads(
        Path(str(report["authority_receipt_path"])).read_text(encoding="utf-8")
    )
    assert authority_receipt["action_id"] == "fixture.notification.reconcile.0001"
    assert authority_receipt["artifact_digest"] == plan["plan_digest"]
    assert authority_receipt["terminal_outcome"] == "reconciled_succeeded"
    assert authority_receipt["effect_count"] == 1
    plan_artifact = Path(str(report["plan_artifact_path"]))
    assert json.loads(plan_artifact.read_text(encoding="utf-8")) == plan
    assert report["recovered_after_error"] is False
    with pytest.raises(ReconciliationAuthorizationError, match="already been claimed"):
        apply_reconciliation_plan(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )


def test_reconciliation_envelope_cannot_replay_in_a_second_claim_directory(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    first_claims = tmp_path / "claims"
    second_claims = tmp_path / "claims-b"
    _write_envelope(envelope, plan)

    apply_reconciliation_plan(
        plan,
        envelope_path=envelope,
        claim_state_dir=first_claims,
        envelope_module_path=SHARED_ENVELOPE_MODULE,
    )
    with pytest.raises(
        ReconciliationAuthorizationError,
        match="claim state directory does not match",
    ):
        apply_reconciliation_plan(
            plan,
            envelope_path=envelope,
            claim_state_dir=second_claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    assert not second_claims.exists()


def test_post_claim_failure_emits_failed_before_effect_receipt(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    _write_envelope(envelope, plan)
    before_bytes = db_path.read_bytes()
    before_mtime = db_path.stat().st_mtime_ns

    with (
        patch(
            "notification_hub.reconciliation.record_channel_reconciliation",
            side_effect=sqlite3.OperationalError("fixture failed before transaction"),
        ) as apply_attempt,
        pytest.raises(
            ReconciliationAuthorizationError,
            match="terminal outcome=failed_before_effect",
        ),
    ):
        apply_reconciliation_plan(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    apply_attempt.assert_called_once()
    assert db_path.read_bytes() == before_bytes
    assert db_path.stat().st_mtime_ns == before_mtime
    stored = get_event(event.event_id, path=db_path)
    assert stored is not None
    assert stored.status == "reconciliation_required"
    authority_receipt = json.loads(
        (claims / "fixture.notification.reconcile.0001.receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert authority_receipt["terminal_outcome"] == "failed_before_effect"
    assert authority_receipt["effect_count"] == 0


def test_post_commit_error_is_recovered_by_readback_without_retry(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    _write_envelope(envelope, plan)
    real_apply = reconciliation_module.record_channel_reconciliation

    def commit_then_fail(*args: object, **kwargs: object) -> None:
        real_apply(*args, **kwargs)  # type: ignore[arg-type]
        raise sqlite3.OperationalError("fixture response lost after commit")

    with patch(
        "notification_hub.reconciliation.record_channel_reconciliation",
        side_effect=commit_then_fail,
    ) as apply_attempt:
        report = apply_reconciliation_plan(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    apply_attempt.assert_called_once()
    assert report["status"] == "applied"
    assert report["recovered_after_error"] is True
    stored = get_event(event.event_id, path=db_path)
    assert stored is not None
    assert stored.status == "reconciled_succeeded"
    authority_receipt = json.loads(
        Path(str(report["authority_receipt_path"])).read_text(encoding="utf-8")
    )
    assert authority_receipt["terminal_outcome"] == "reconciled_succeeded"
    assert authority_receipt["effect_count"] == 1


def test_unreadable_post_claim_state_emits_outcome_unknown_and_never_retries(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    _write_envelope(envelope, plan)

    with (
        patch(
            "notification_hub.reconciliation.record_channel_reconciliation",
            side_effect=sqlite3.OperationalError("fixture apply result unavailable"),
        ) as apply_attempt,
        patch(
            "notification_hub.reconciliation.read_channel_reconciliation_state",
            side_effect=sqlite3.OperationalError("fixture readback unavailable"),
        ) as readback_attempt,
        pytest.raises(
            ReconciliationAuthorizationError,
            match="terminal outcome=outcome_unknown",
        ),
    ):
        apply_reconciliation_plan(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    apply_attempt.assert_called_once()
    readback_attempt.assert_called_once()
    stored = get_event(event.event_id, path=db_path)
    assert stored is not None
    assert stored.status == "reconciliation_required"
    authority_receipt = json.loads(
        (claims / "fixture.notification.reconcile.0001.receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert authority_receipt["terminal_outcome"] == "outcome_unknown"
    assert authority_receipt["effect_count"] == 1
    assert "database_readback_error" in authority_receipt["readback_result"]


def test_finalize_expired_consumed_claim_without_replaying_mutation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    _write_envelope(envelope, plan)
    _write_historical_consumed_claim(envelope, claims, plan=plan)
    before_db = db_path.read_bytes()
    before_mtime = db_path.stat().st_mtime_ns

    with patch(
        "notification_hub.reconciliation.record_channel_reconciliation"
    ) as forbidden_apply:
        report = finalize_reconciliation_claim(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    forbidden_apply.assert_not_called()
    assert report["status"] == "finalized"
    assert report["terminal_outcome"] == "failed_before_effect"
    assert db_path.read_bytes() == before_db
    assert db_path.stat().st_mtime_ns == before_mtime
    receipt_path = Path(str(report["authority_receipt_path"]))
    before_receipt = receipt_path.read_bytes()
    replay = finalize_reconciliation_claim(
        plan,
        envelope_path=envelope,
        claim_state_dir=claims,
        envelope_module_path=SHARED_ENVELOPE_MODULE,
    )
    assert replay["status"] == "already_finalized"
    assert receipt_path.read_bytes() == before_receipt


def test_finalize_after_abrupt_apply_termination_uses_persisted_plan(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    _write_envelope(envelope, plan)

    with (
        patch(
            "notification_hub.reconciliation.record_channel_reconciliation",
            side_effect=SystemExit("fixture abrupt termination"),
        ),
        pytest.raises(SystemExit, match="fixture abrupt termination"),
    ):
        apply_reconciliation_plan(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    plan_path = claims / "fixture.notification.reconcile.0001.plan.json"
    assert json.loads(plan_path.read_text(encoding="utf-8")) == plan
    assert (claims / "fixture.notification.reconcile.0001.claim.json").exists()
    assert not (claims / "fixture.notification.reconcile.0001.receipt.json").exists()
    with patch(
        "notification_hub.reconciliation.record_channel_reconciliation"
    ) as forbidden_apply:
        report = finalize_reconciliation_claim(
            load_readback_file(plan_path),
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )
    forbidden_apply.assert_not_called()
    assert report["status"] == "finalized"
    assert report["terminal_outcome"] == "failed_before_effect"


def test_finalize_rejects_missing_persisted_plan_before_database_read(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    _write_envelope(envelope, plan)
    _write_historical_consumed_claim(envelope, claims)

    with (
        patch(
            "notification_hub.reconciliation.read_channel_reconciliation_state"
        ) as forbidden_database_read,
        pytest.raises(ValueError, match="regular non-symlink file"),
    ):
        finalize_reconciliation_claim(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    forbidden_database_read.assert_not_called()
    assert not (claims / "fixture.notification.reconcile.0001.receipt.json").exists()


def test_finalize_rejects_changed_persisted_plan_before_database_read(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    _write_envelope(envelope, plan)
    _write_historical_consumed_claim(envelope, claims, plan=plan)
    plan_path = claims / "fixture.notification.reconcile.0001.plan.json"
    changed_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    changed_plan["effect_count"] = 2
    plan_path.write_text(json.dumps(changed_plan), encoding="utf-8")
    plan_path.chmod(0o600)

    with (
        patch(
            "notification_hub.reconciliation.read_channel_reconciliation_state"
        ) as forbidden_database_read,
        pytest.raises(
            ReconciliationAuthorizationError,
            match="persisted reconciliation plan does not match",
        ),
    ):
        finalize_reconciliation_claim(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    forbidden_database_read.assert_not_called()
    assert not (claims / "fixture.notification.reconcile.0001.receipt.json").exists()


def test_finalize_rejects_symlinked_persisted_plan_before_database_read(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    _write_envelope(envelope, plan)
    _write_historical_consumed_claim(envelope, claims)
    external_plan = tmp_path / "external-plan.json"
    external_plan.write_text(json.dumps(plan), encoding="utf-8")
    external_plan.chmod(0o600)
    plan_path = claims / "fixture.notification.reconcile.0001.plan.json"
    plan_path.symlink_to(external_plan)

    with (
        patch(
            "notification_hub.reconciliation.read_channel_reconciliation_state"
        ) as forbidden_database_read,
        pytest.raises(ValueError, match="regular non-symlink file"),
    ):
        finalize_reconciliation_claim(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    forbidden_database_read.assert_not_called()
    assert not (claims / "fixture.notification.reconcile.0001.receipt.json").exists()


def test_concurrent_finalizers_converge_on_first_valid_terminal_receipt(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    _write_envelope(envelope, plan)
    _write_historical_consumed_claim(envelope, claims, plan=plan)
    barrier = threading.Barrier(2)
    assignment_lock = threading.Lock()
    classification_index = 0

    def conflicting_classification(**kwargs: object) -> tuple[
        str,
        int,
        dict[str, object],
        None,
    ]:
        nonlocal classification_index
        with assignment_lock:
            assigned = classification_index
            classification_index += 1
        barrier.wait(timeout=5)
        readback = _readback(event.event_id, provider_reference)
        if assigned == 0:
            readback["database_event_status"] = "reconciliation_required"
            return "failed_before_effect", 0, readback, None
        readback["database_readback_error"] = "fixture state changed during race"
        return "outcome_unknown", 1, readback, None

    def finalize_once() -> dict[str, object]:
        return finalize_reconciliation_claim(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    with (
        patch(
            "notification_hub.reconciliation._classify_post_claim_state",
            side_effect=conflicting_classification,
        ),
        patch(
            "notification_hub.reconciliation.record_channel_reconciliation"
        ) as forbidden_mutation,
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        futures = [executor.submit(finalize_once) for _ in range(2)]
        reports = [future.result(timeout=10) for future in futures]

    forbidden_mutation.assert_not_called()
    assert sorted(str(report["status"]) for report in reports) == [
        "already_finalized",
        "finalized",
    ]
    receipt_paths = {
        str(report["authority_receipt_path"])
        for report in reports
    }
    assert receipt_paths == {
        str(claims / "fixture.notification.reconcile.0001.receipt.json")
    }
    assert len(list(claims.glob("*.receipt.json"))) == 1
    receipt = json.loads(
        (claims / "fixture.notification.reconcile.0001.receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert reports[0]["authority_receipt"] == receipt
    assert reports[1]["authority_receipt"] == receipt
    assert receipt["terminal_outcome"] in {
        "failed_before_effect",
        "outcome_unknown",
    }
    assert {
        str(report["terminal_outcome"])
        for report in reports
    } == {receipt["terminal_outcome"]}


def test_finalizer_refuses_while_claimed_apply_is_still_executing(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    _write_envelope(envelope, plan)
    mutation_entered = threading.Event()
    allow_mutation = threading.Event()

    def paused_mutation(*args: object, **kwargs: object) -> dict[str, object]:
        mutation_entered.set()
        if not allow_mutation.wait(timeout=10):
            raise AssertionError("fixture timed out waiting to release apply")
        return record_channel_reconciliation(*args, **kwargs)

    with (
        patch(
            "notification_hub.reconciliation.record_channel_reconciliation",
            side_effect=paused_mutation,
        ),
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        apply_future = executor.submit(
            apply_reconciliation_plan,
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )
        assert mutation_entered.wait(timeout=5)
        assert (claims / "fixture.notification.reconcile.0001.claim.json").exists()
        try:
            with pytest.raises(
                ReconciliationAuthorizationError,
                match="still executing",
            ):
                finalize_reconciliation_claim(
                    plan,
                    envelope_path=envelope,
                    claim_state_dir=claims,
                    envelope_module_path=SHARED_ENVELOPE_MODULE,
                )
            assert not (
                claims / "fixture.notification.reconcile.0001.receipt.json"
            ).exists()
            stored_during_apply = get_event(event.event_id, path=db_path)
            assert stored_during_apply is not None
            assert stored_during_apply.status == "reconciliation_required"
        finally:
            allow_mutation.set()
        apply_report = apply_future.result(timeout=10)

    assert apply_report["status"] == "applied"
    finalized = finalize_reconciliation_claim(
        plan,
        envelope_path=envelope,
        claim_state_dir=claims,
        envelope_module_path=SHARED_ENVELOPE_MODULE,
    )
    assert finalized["status"] == "already_finalized"
    assert finalized["terminal_outcome"] == "reconciled_succeeded"


def test_finalizer_recovers_after_lock_holder_process_terminates(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    _write_envelope(envelope, plan)
    _write_historical_consumed_claim(envelope, claims, plan=plan)
    action_id = "fixture.notification.reconcile.0001"
    ready_path = tmp_path / "lock-holder-ready"
    child_script = """
import signal
import sys
from pathlib import Path

from notification_hub.reconciliation import (
    _action_execution_lock,
    _private_directory,
)

state_dir = _private_directory(Path(sys.argv[1]), create=False)
with _action_execution_lock(state_dir, sys.argv[2], exclusive=True):
    ready_path = Path(sys.argv[3])
    ready_path.write_text("locked\\n", encoding="utf-8")
    ready_path.chmod(0o600)
    signal.pause()
"""
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            child_script,
            str(claims),
            action_id,
            str(ready_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not ready_path.exists():
            if child.poll() is not None:
                stdout, stderr = child.communicate()
                pytest.fail(
                    "lock-holder process exited before acquiring the lock: "
                    f"stdout={stdout!r} stderr={stderr!r}"
                )
            if time.monotonic() >= deadline:
                pytest.fail("lock-holder process did not acquire the lock")
            time.sleep(0.01)

        plan_path = claims / f"{action_id}.plan.json"
        claim_path = claims / f"{action_id}.claim.json"
        lock_path = claims / f"{action_id}.execution.lock"
        plan_before = plan_path.read_bytes()
        claim_before = claim_path.read_bytes()
        lock_before = lock_path.read_bytes()

        with patch(
            "notification_hub.reconciliation.record_channel_reconciliation"
        ) as forbidden_mutation:
            with pytest.raises(
                ReconciliationAuthorizationError,
                match="still executing",
            ):
                finalize_reconciliation_claim(
                    plan,
                    envelope_path=envelope,
                    claim_state_dir=claims,
                    envelope_module_path=SHARED_ENVELOPE_MODULE,
                )
            forbidden_mutation.assert_not_called()
            assert not (claims / f"{action_id}.receipt.json").exists()

            child.terminate()
            child.wait(timeout=5)

            report = finalize_reconciliation_claim(
                plan,
                envelope_path=envelope,
                claim_state_dir=claims,
                envelope_module_path=SHARED_ENVELOPE_MODULE,
            )
            forbidden_mutation.assert_not_called()

        assert report["status"] == "finalized"
        assert report["terminal_outcome"] == "failed_before_effect"
        assert plan_path.read_bytes() == plan_before
        assert claim_path.read_bytes() == claim_before
        assert lock_path.read_bytes() == lock_before
        assert len(list(claims.glob("*.receipt.json"))) == 1
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


def test_process_termination_after_identity_link_allows_safe_retry(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    plan_input = tmp_path / "plan-input.json"
    ready_path = tmp_path / "identity-linked-ready"
    _write_envelope(envelope, plan)
    plan_input.write_text(json.dumps(plan), encoding="utf-8")
    plan_input.chmod(0o600)
    action_id = "fixture.notification.reconcile.0001"
    child_script = """
import os
import signal
import sys
from pathlib import Path
from unittest.mock import patch

import notification_hub.reconciliation as reconciliation

plan = reconciliation.load_readback_file(Path(sys.argv[1]))
envelope_path = Path(sys.argv[2])
claims = Path(sys.argv[3])
envelope_module = Path(sys.argv[4])
ready_path = Path(sys.argv[5])
original_link = os.link

def pause_after_identity_link(source, destination, *args, **kwargs):
    result = original_link(source, destination, *args, **kwargs)
    if str(destination).endswith(".execution.lock.identity.json"):
        ready_path.write_text("identity-linked\\n", encoding="utf-8")
        ready_path.chmod(0o600)
        signal.pause()
    return result

with patch.object(reconciliation.os, "link", side_effect=pause_after_identity_link):
    reconciliation.apply_reconciliation_plan(
        plan,
        envelope_path=envelope_path,
        claim_state_dir=claims,
        envelope_module_path=envelope_module,
    )
"""
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            child_script,
            str(plan_input),
            str(envelope),
            str(claims),
            str(SHARED_ENVELOPE_MODULE),
            str(ready_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not ready_path.exists():
            if child.poll() is not None:
                stdout, stderr = child.communicate()
                pytest.fail(
                    "identity-publication process exited before interruption: "
                    f"stdout={stdout!r} stderr={stderr!r}"
                )
            if time.monotonic() >= deadline:
                pytest.fail("identity-publication process did not reach link boundary")
            time.sleep(0.01)

        lock_path = claims / f"{action_id}.execution.lock"
        identity_path = claims / f"{action_id}.execution.lock.identity.json"
        identity_before = identity_path.read_bytes()
        identity = json.loads(identity_before)
        lock_metadata = lock_path.stat()
        assert identity["device"] == lock_metadata.st_dev
        assert identity["inode"] == lock_metadata.st_ino
        assert not (claims / f"{action_id}.plan.json").exists()
        assert not (claims / f"{action_id}.claim.json").exists()
        assert not (claims / f"{action_id}.receipt.json").exists()

        child.terminate()
        child.wait(timeout=5)

        report = apply_reconciliation_plan(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

        authority_receipt_path = Path(str(report["authority_receipt_path"]))
        receipt_before = authority_receipt_path.read_bytes()
        authority_receipt = json.loads(receipt_before)
        assert report["status"] == "applied"
        assert identity_path.read_bytes() == identity_before
        assert authority_receipt["action_id"] == action_id
        assert authority_receipt["target"] == plan["canonical_targets"]
        assert authority_receipt["artifact_digest"] == plan["plan_digest"]
        assert authority_receipt["provider_reference"] == provider_reference
        assert authority_receipt["terminal_outcome"] == "reconciled_succeeded"

        finalized = finalize_reconciliation_claim(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )
        assert finalized["status"] == "already_finalized"
        assert authority_receipt_path.read_bytes() == receipt_before
        assert len(list(claims.glob("*.receipt.json"))) == 1
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


def test_process_termination_after_plan_link_rejects_change_and_allows_exact_retry(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    plan_input = tmp_path / "plan-input.json"
    ready_path = tmp_path / "plan-linked-ready"
    _write_envelope(envelope, plan)
    plan_input.write_text(json.dumps(plan), encoding="utf-8")
    plan_input.chmod(0o600)
    before_database = db_path.read_bytes()
    action_id = "fixture.notification.reconcile.0001"
    child_script = """
import os
import signal
import sys
from pathlib import Path
from unittest.mock import patch

import notification_hub.reconciliation as reconciliation

plan = reconciliation.load_readback_file(Path(sys.argv[1]))
envelope_path = Path(sys.argv[2])
claims = Path(sys.argv[3])
envelope_module = Path(sys.argv[4])
ready_path = Path(sys.argv[5])
original_link = os.link

def pause_after_plan_link(source, destination, *args, **kwargs):
    result = original_link(source, destination, *args, **kwargs)
    if str(destination).endswith(".plan.json"):
        ready_path.write_text("plan-linked\\n", encoding="utf-8")
        ready_path.chmod(0o600)
        signal.pause()
    return result

with patch.object(reconciliation.os, "link", side_effect=pause_after_plan_link):
    reconciliation.apply_reconciliation_plan(
        plan,
        envelope_path=envelope_path,
        claim_state_dir=claims,
        envelope_module_path=envelope_module,
    )
"""
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            child_script,
            str(plan_input),
            str(envelope),
            str(claims),
            str(SHARED_ENVELOPE_MODULE),
            str(ready_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not ready_path.exists():
            if child.poll() is not None:
                stdout, stderr = child.communicate()
                pytest.fail(
                    "plan-publication process exited before interruption: "
                    f"stdout={stdout!r} stderr={stderr!r}"
                )
            if time.monotonic() >= deadline:
                pytest.fail("plan-publication process did not reach link boundary")
            time.sleep(0.01)

        plan_path = claims / f"{action_id}.plan.json"
        identity_path = claims / f"{action_id}.execution.lock.identity.json"
        plan_before = plan_path.read_bytes()
        identity_before = identity_path.read_bytes()
        assert json.loads(plan_before) == plan
        assert not (claims / f"{action_id}.claim.json").exists()
        assert not (claims / f"{action_id}.receipt.json").exists()

        child.terminate()
        child.wait(timeout=5)

        changed_provider_reference = "slack:history:changed-message-2"
        changed_plan = build_reconciliation_plan(
            event.event_id,
            "slack",
            terminal_outcome="reconciled_succeeded",
            provider_reference=changed_provider_reference,
            readback_result=_readback(
                event.event_id,
                changed_provider_reference,
            ),
            database_path=db_path,
        )
        _write_envelope(envelope, changed_plan)
        with (
            patch(
                "notification_hub.reconciliation.record_channel_reconciliation"
            ) as forbidden_mutation,
            pytest.raises(
                ReconciliationAuthorizationError,
                match="different plan artifact",
            ),
        ):
            apply_reconciliation_plan(
                changed_plan,
                envelope_path=envelope,
                claim_state_dir=claims,
                envelope_module_path=SHARED_ENVELOPE_MODULE,
            )

        forbidden_mutation.assert_not_called()
        assert db_path.read_bytes() == before_database
        assert plan_path.read_bytes() == plan_before
        assert identity_path.read_bytes() == identity_before
        assert not (claims / f"{action_id}.claim.json").exists()
        assert not (claims / f"{action_id}.receipt.json").exists()

        _write_envelope(envelope, plan)
        report = apply_reconciliation_plan(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

        authority_receipt_path = Path(str(report["authority_receipt_path"]))
        receipt_before = authority_receipt_path.read_bytes()
        authority_receipt = json.loads(receipt_before)
        assert report["status"] == "applied"
        assert plan_path.read_bytes() == plan_before
        assert identity_path.read_bytes() == identity_before
        assert authority_receipt["action_id"] == action_id
        assert authority_receipt["target"] == plan["canonical_targets"]
        assert authority_receipt["artifact_digest"] == plan["plan_digest"]
        assert authority_receipt["provider_reference"] == provider_reference
        assert authority_receipt["terminal_outcome"] == "reconciled_succeeded"

        finalized = finalize_reconciliation_claim(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )
        assert finalized["status"] == "already_finalized"
        assert authority_receipt_path.read_bytes() == receipt_before
        assert len(list(claims.glob("*.receipt.json"))) == 1
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


def test_process_termination_after_real_claim_finalizes_without_mutation_replay(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    plan_input = tmp_path / "plan-input.json"
    ready_path = tmp_path / "claim-published-ready"
    _write_envelope(envelope, plan)
    plan_input.write_text(json.dumps(plan), encoding="utf-8")
    plan_input.chmod(0o600)
    before_database = db_path.read_bytes()
    before_database_mtime = db_path.stat().st_mtime_ns
    action_id = "fixture.notification.reconcile.0001"
    child_script = """
import signal
import sys
from pathlib import Path
from unittest.mock import patch

import notification_hub.reconciliation as reconciliation

plan = reconciliation.load_readback_file(Path(sys.argv[1]))
envelope_path = Path(sys.argv[2])
claims = Path(sys.argv[3])
envelope_module = Path(sys.argv[4])
ready_path = Path(sys.argv[5])
load_envelope, claim_envelope, emit_receipt = (
    reconciliation._load_envelope_functions(envelope_module)
)

def claim_then_pause(envelope, state_dir):
    claim_path = claim_envelope(envelope, state_dir)
    ready_path.write_text("claim-published\\n", encoding="utf-8")
    ready_path.chmod(0o600)
    signal.pause()
    return claim_path

with patch.object(
    reconciliation,
    "_load_envelope_functions",
    return_value=(load_envelope, claim_then_pause, emit_receipt),
):
    reconciliation.apply_reconciliation_plan(
        plan,
        envelope_path=envelope_path,
        claim_state_dir=claims,
        envelope_module_path=envelope_module,
    )
"""
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            child_script,
            str(plan_input),
            str(envelope),
            str(claims),
            str(SHARED_ENVELOPE_MODULE),
            str(ready_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not ready_path.exists():
            if child.poll() is not None:
                stdout, stderr = child.communicate()
                pytest.fail(
                    "claim-publication process exited before interruption: "
                    f"stdout={stdout!r} stderr={stderr!r}"
                )
            if time.monotonic() >= deadline:
                pytest.fail("claim-publication process did not reach claim boundary")
            time.sleep(0.01)

        plan_path = claims / f"{action_id}.plan.json"
        claim_path = claims / f"{action_id}.claim.json"
        lock_path = claims / f"{action_id}.execution.lock"
        identity_path = claims / f"{action_id}.execution.lock.identity.json"
        plan_before = plan_path.read_bytes()
        claim_before = claim_path.read_bytes()
        lock_before = lock_path.read_bytes()
        identity_before = identity_path.read_bytes()
        assert json.loads(plan_before) == plan
        assert not (claims / f"{action_id}.receipt.json").exists()
        assert db_path.read_bytes() == before_database
        assert db_path.stat().st_mtime_ns == before_database_mtime

        with (
            patch(
                "notification_hub.reconciliation.record_channel_reconciliation"
            ) as forbidden_mutation,
            patch(
                "notification_hub.reconciliation.read_channel_reconciliation_state"
            ) as forbidden_database_read,
            pytest.raises(
                ReconciliationAuthorizationError,
                match="still executing",
            ),
        ):
            finalize_reconciliation_claim(
                plan,
                envelope_path=envelope,
                claim_state_dir=claims,
                envelope_module_path=SHARED_ENVELOPE_MODULE,
            )
        forbidden_mutation.assert_not_called()
        forbidden_database_read.assert_not_called()

        child.terminate()
        child.wait(timeout=5)

        with patch(
            "notification_hub.reconciliation.record_channel_reconciliation"
        ) as forbidden_mutation:
            report = finalize_reconciliation_claim(
                plan,
                envelope_path=envelope,
                claim_state_dir=claims,
                envelope_module_path=SHARED_ENVELOPE_MODULE,
            )
        forbidden_mutation.assert_not_called()

        authority_receipt_path = Path(str(report["authority_receipt_path"]))
        receipt_before = authority_receipt_path.read_bytes()
        authority_receipt = json.loads(receipt_before)
        assert report["status"] == "finalized"
        assert report["terminal_outcome"] == "failed_before_effect"
        assert plan_path.read_bytes() == plan_before
        assert claim_path.read_bytes() == claim_before
        assert lock_path.read_bytes() == lock_before
        assert identity_path.read_bytes() == identity_before
        assert db_path.read_bytes() == before_database
        assert db_path.stat().st_mtime_ns == before_database_mtime
        assert authority_receipt["action_id"] == action_id
        assert authority_receipt["target"] == plan["canonical_targets"]
        assert authority_receipt["artifact_digest"] == plan["plan_digest"]
        assert authority_receipt["provider_reference"] == provider_reference
        assert authority_receipt["terminal_outcome"] == "failed_before_effect"
        assert authority_receipt["effect_count"] == 0

        replay = finalize_reconciliation_claim(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )
        assert replay["status"] == "already_finalized"
        assert authority_receipt_path.read_bytes() == receipt_before
        assert len(list(claims.glob("*.receipt.json"))) == 1
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


def test_process_termination_after_database_commit_recovers_without_replay(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    plan_input = tmp_path / "plan-input.json"
    ready_path = tmp_path / "database-committed-ready"
    _write_envelope(envelope, plan)
    plan_input.write_text(json.dumps(plan), encoding="utf-8")
    plan_input.chmod(0o600)
    before_database = db_path.read_bytes()
    action_id = "fixture.notification.reconcile.0001"
    child_script = """
import signal
import sys
from pathlib import Path
from unittest.mock import patch

import notification_hub.reconciliation as reconciliation

plan = reconciliation.load_readback_file(Path(sys.argv[1]))
envelope_path = Path(sys.argv[2])
claims = Path(sys.argv[3])
envelope_module = Path(sys.argv[4])
ready_path = Path(sys.argv[5])
load_envelope, claim_envelope, emit_receipt = (
    reconciliation._load_envelope_functions(envelope_module)
)

def pause_before_authority_receipt(*args, **kwargs):
    ready_path.write_text("database-committed\\n", encoding="utf-8")
    ready_path.chmod(0o600)
    signal.pause()
    return emit_receipt(*args, **kwargs)

with patch.object(
    reconciliation,
    "_load_envelope_functions",
    return_value=(load_envelope, claim_envelope, pause_before_authority_receipt),
):
    reconciliation.apply_reconciliation_plan(
        plan,
        envelope_path=envelope_path,
        claim_state_dir=claims,
        envelope_module_path=envelope_module,
    )
"""
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            child_script,
            str(plan_input),
            str(envelope),
            str(claims),
            str(SHARED_ENVELOPE_MODULE),
            str(ready_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not ready_path.exists():
            if child.poll() is not None:
                stdout, stderr = child.communicate()
                pytest.fail(
                    "post-commit process exited before interruption: "
                    f"stdout={stdout!r} stderr={stderr!r}"
                )
            if time.monotonic() >= deadline:
                pytest.fail("post-commit process did not reach receipt boundary")
            time.sleep(0.01)

        plan_path = claims / f"{action_id}.plan.json"
        claim_path = claims / f"{action_id}.claim.json"
        lock_path = claims / f"{action_id}.execution.lock"
        identity_path = claims / f"{action_id}.execution.lock.identity.json"
        plan_before = plan_path.read_bytes()
        claim_before = claim_path.read_bytes()
        lock_before = lock_path.read_bytes()
        identity_before = identity_path.read_bytes()
        assert not (claims / f"{action_id}.receipt.json").exists()
        assert db_path.read_bytes() != before_database
        committed_database = db_path.read_bytes()
        committed_database_mtime = db_path.stat().st_mtime_ns
        committed_state = reconciliation_module.read_channel_reconciliation_state(
            event.event_id,
            "slack",
            path=db_path,
        )
        committed_receipt = committed_state["receipt"]
        assert committed_state["event_status"] == "reconciled_succeeded"
        assert isinstance(committed_receipt, dict)
        assert committed_receipt["action_id"] == action_id
        assert committed_receipt["artifact_digest"] == plan["plan_digest"]
        committed_receipt_before = canonical_json(committed_receipt)

        with (
            patch(
                "notification_hub.reconciliation.record_channel_reconciliation"
            ) as forbidden_mutation,
            patch(
                "notification_hub.reconciliation.read_channel_reconciliation_state"
            ) as forbidden_database_read,
            pytest.raises(
                ReconciliationAuthorizationError,
                match="still executing",
            ),
        ):
            finalize_reconciliation_claim(
                plan,
                envelope_path=envelope,
                claim_state_dir=claims,
                envelope_module_path=SHARED_ENVELOPE_MODULE,
            )
        forbidden_mutation.assert_not_called()
        forbidden_database_read.assert_not_called()

        child.terminate()
        child.wait(timeout=5)

        with patch(
            "notification_hub.reconciliation.record_channel_reconciliation"
        ) as forbidden_mutation:
            report = finalize_reconciliation_claim(
                plan,
                envelope_path=envelope,
                claim_state_dir=claims,
                envelope_module_path=SHARED_ENVELOPE_MODULE,
            )
        forbidden_mutation.assert_not_called()

        authority_receipt_path = Path(str(report["authority_receipt_path"]))
        receipt_before = authority_receipt_path.read_bytes()
        authority_receipt = json.loads(receipt_before)
        recovered_state = reconciliation_module.read_channel_reconciliation_state(
            event.event_id,
            "slack",
            path=db_path,
        )
        assert report["status"] == "finalized"
        assert report["terminal_outcome"] == "reconciled_succeeded"
        assert plan_path.read_bytes() == plan_before
        assert claim_path.read_bytes() == claim_before
        assert lock_path.read_bytes() == lock_before
        assert identity_path.read_bytes() == identity_before
        assert db_path.read_bytes() == committed_database
        assert db_path.stat().st_mtime_ns == committed_database_mtime
        assert recovered_state["event_status"] == "reconciled_succeeded"
        assert canonical_json(recovered_state["receipt"]) == committed_receipt_before
        assert authority_receipt["action_id"] == action_id
        assert authority_receipt["target"] == plan["canonical_targets"]
        assert authority_receipt["artifact_digest"] == plan["plan_digest"]
        assert authority_receipt["provider_reference"] == provider_reference
        assert authority_receipt["terminal_outcome"] == "reconciled_succeeded"
        assert authority_receipt["effect_count"] == 1
        receipt_readback = authority_receipt["readback_result"]
        assert isinstance(receipt_readback, dict)
        assert receipt_readback["database_event_status"] == "reconciled_succeeded"
        assert receipt_readback["database_receipt_digest"] == sha256_json(
            committed_receipt
        )

        replay = finalize_reconciliation_claim(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )
        assert replay["status"] == "already_finalized"
        assert authority_receipt_path.read_bytes() == receipt_before
        assert len(list(claims.glob("*.receipt.json"))) == 1
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


def test_process_termination_after_authority_receipt_link_reuses_exact_receipt(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    plan_input = tmp_path / "plan-input.json"
    ready_path = tmp_path / "authority-receipt-linked-ready"
    _write_envelope(envelope, plan)
    plan_input.write_text(json.dumps(plan), encoding="utf-8")
    plan_input.chmod(0o600)
    action_id = "fixture.notification.reconcile.0001"
    child_script = """
import os
import signal
import sys
from pathlib import Path
from unittest.mock import patch

import notification_hub.reconciliation as reconciliation

plan = reconciliation.load_readback_file(Path(sys.argv[1]))
envelope_path = Path(sys.argv[2])
claims = Path(sys.argv[3])
envelope_module = Path(sys.argv[4])
ready_path = Path(sys.argv[5])
original_link = os.link

def pause_after_authority_receipt_link(source, destination, *args, **kwargs):
    result = original_link(source, destination, *args, **kwargs)
    if str(destination).endswith(".receipt.json"):
        ready_path.write_text("authority-receipt-linked\\n", encoding="utf-8")
        ready_path.chmod(0o600)
        signal.pause()
    return result

with patch.object(
    reconciliation.os,
    "link",
    side_effect=pause_after_authority_receipt_link,
):
    reconciliation.apply_reconciliation_plan(
        plan,
        envelope_path=envelope_path,
        claim_state_dir=claims,
        envelope_module_path=envelope_module,
    )
"""
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            child_script,
            str(plan_input),
            str(envelope),
            str(claims),
            str(SHARED_ENVELOPE_MODULE),
            str(ready_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not ready_path.exists():
            if child.poll() is not None:
                stdout, stderr = child.communicate()
                pytest.fail(
                    "receipt-publication process exited before interruption: "
                    f"stdout={stdout!r} stderr={stderr!r}"
                )
            if time.monotonic() >= deadline:
                pytest.fail("receipt publisher did not reach atomic link boundary")
            time.sleep(0.01)

        plan_path = claims / f"{action_id}.plan.json"
        claim_path = claims / f"{action_id}.claim.json"
        lock_path = claims / f"{action_id}.execution.lock"
        identity_path = claims / f"{action_id}.execution.lock.identity.json"
        authority_receipt_path = claims / f"{action_id}.receipt.json"
        plan_before = plan_path.read_bytes()
        claim_before = claim_path.read_bytes()
        lock_before = lock_path.read_bytes()
        identity_before = identity_path.read_bytes()
        receipt_before = authority_receipt_path.read_bytes()
        authority_receipt = json.loads(receipt_before)
        committed_database = db_path.read_bytes()
        committed_database_mtime = db_path.stat().st_mtime_ns
        committed_state = reconciliation_module.read_channel_reconciliation_state(
            event.event_id,
            "slack",
            path=db_path,
        )
        committed_receipt_before = canonical_json(committed_state["receipt"])
        assert committed_state["event_status"] == "reconciled_succeeded"
        assert authority_receipt["action_id"] == action_id
        assert authority_receipt["artifact_digest"] == plan["plan_digest"]
        assert authority_receipt["terminal_outcome"] == "reconciled_succeeded"
        assert authority_receipt["effect_count"] == 1

        with (
            patch(
                "notification_hub.reconciliation.record_channel_reconciliation"
            ) as forbidden_mutation,
            patch(
                "notification_hub.reconciliation.read_channel_reconciliation_state"
            ) as forbidden_database_read,
            pytest.raises(
                ReconciliationAuthorizationError,
                match="still executing",
            ),
        ):
            finalize_reconciliation_claim(
                plan,
                envelope_path=envelope,
                claim_state_dir=claims,
                envelope_module_path=SHARED_ENVELOPE_MODULE,
            )
        forbidden_mutation.assert_not_called()
        forbidden_database_read.assert_not_called()

        child.terminate()
        child.wait(timeout=5)

        with (
            patch(
                "notification_hub.reconciliation.record_channel_reconciliation"
            ) as forbidden_mutation,
            patch(
                "notification_hub.reconciliation.read_channel_reconciliation_state"
            ) as forbidden_database_read,
            patch(
                "notification_hub.reconciliation.os.link"
            ) as forbidden_publication,
        ):
            report = finalize_reconciliation_claim(
                plan,
                envelope_path=envelope,
                claim_state_dir=claims,
                envelope_module_path=SHARED_ENVELOPE_MODULE,
            )
        forbidden_mutation.assert_not_called()
        forbidden_database_read.assert_not_called()
        forbidden_publication.assert_not_called()

        recovered_state = reconciliation_module.read_channel_reconciliation_state(
            event.event_id,
            "slack",
            path=db_path,
        )
        assert report["status"] == "already_finalized"
        assert report["terminal_outcome"] == "reconciled_succeeded"
        assert report["authority_receipt"] == authority_receipt
        assert plan_path.read_bytes() == plan_before
        assert claim_path.read_bytes() == claim_before
        assert lock_path.read_bytes() == lock_before
        assert identity_path.read_bytes() == identity_before
        assert authority_receipt_path.read_bytes() == receipt_before
        assert db_path.read_bytes() == committed_database
        assert db_path.stat().st_mtime_ns == committed_database_mtime
        assert recovered_state["event_status"] == "reconciled_succeeded"
        assert canonical_json(recovered_state["receipt"]) == committed_receipt_before

        replay = finalize_reconciliation_claim(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )
        assert replay["status"] == "already_finalized"
        assert replay["authority_receipt"] == authority_receipt
        assert authority_receipt_path.read_bytes() == receipt_before
        assert len(list(claims.glob("*.receipt.json"))) == 1
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


@pytest.mark.parametrize(
    ("remove_identity", "error"),
    [
        (False, "execution lock identity does not match"),
        (True, "execution lock identity is missing"),
    ],
)
def test_finalizer_rejects_replaced_lock_inode_while_holder_is_live(
    tmp_path: Path,
    remove_identity: bool,
    error: str,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    _write_envelope(envelope, plan)
    _write_historical_consumed_claim(envelope, claims, plan=plan)
    action_id = "fixture.notification.reconcile.0001"
    ready_path = tmp_path / "lock-holder-ready"
    child_script = """
import signal
import sys
from pathlib import Path

from notification_hub.reconciliation import (
    _action_execution_lock,
    _private_directory,
)

state_dir = _private_directory(Path(sys.argv[1]), create=False)
with _action_execution_lock(state_dir, sys.argv[2], exclusive=True):
    ready_path = Path(sys.argv[3])
    ready_path.write_text("locked\\n", encoding="utf-8")
    ready_path.chmod(0o600)
    signal.pause()
"""
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            child_script,
            str(claims),
            action_id,
            str(ready_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not ready_path.exists():
            if child.poll() is not None:
                stdout, stderr = child.communicate()
                pytest.fail(
                    "lock-holder process exited before acquiring the lock: "
                    f"stdout={stdout!r} stderr={stderr!r}"
                )
            if time.monotonic() >= deadline:
                pytest.fail("lock-holder process did not acquire the lock")
            time.sleep(0.01)

        lock_path = claims / f"{action_id}.execution.lock"
        identity_path = claims / f"{action_id}.execution.lock.identity.json"
        identity_before = identity_path.read_bytes()
        held_inode = lock_path.stat().st_ino
        lock_path.unlink()
        lock_path.write_bytes(b"")
        lock_path.chmod(0o600)
        assert lock_path.stat().st_ino != held_inode
        if remove_identity:
            identity_path.unlink()

        with patch(
            "notification_hub.reconciliation.record_channel_reconciliation"
        ) as forbidden_mutation:
            with pytest.raises(
                ReconciliationAuthorizationError,
                match=error,
            ):
                finalize_reconciliation_claim(
                    plan,
                    envelope_path=envelope,
                    claim_state_dir=claims,
                    envelope_module_path=SHARED_ENVELOPE_MODULE,
                )
            forbidden_mutation.assert_not_called()
        assert not (claims / f"{action_id}.receipt.json").exists()
        if not remove_identity:
            assert identity_path.read_bytes() == identity_before
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


def test_finalize_recovers_committed_claim_without_replaying_mutation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    _write_envelope(envelope, plan)
    _write_historical_consumed_claim(envelope, claims, plan=plan)
    targets = plan["canonical_targets"]
    assert isinstance(targets, dict)
    record_channel_reconciliation(
        event.event_id,
        "slack",
        reconciliation_id="fixture.notification.reconcile.0001",
        expected_unknown_evidence_digest=str(targets["unknown_evidence_digest"]),
        expected_original_provider_reference=str(
            targets["original_provider_reference"]
        ),
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        artifact_digest=str(plan["plan_digest"]),
        provider_idempotency_key="fixture.notification.reconcile.0001",
        path=db_path,
    )

    with patch(
        "notification_hub.reconciliation.record_channel_reconciliation"
    ) as forbidden_apply:
        report = finalize_reconciliation_claim(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    forbidden_apply.assert_not_called()
    assert report["status"] == "finalized"
    assert report["terminal_outcome"] == "reconciled_succeeded"
    receipt = json.loads(
        Path(str(report["authority_receipt_path"])).read_text(encoding="utf-8")
    )
    assert receipt["terminal_outcome"] == "reconciled_succeeded"
    assert receipt["effect_count"] == 1


def test_finalize_rejects_forged_claim_digest_without_receipt(tmp_path: Path) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    _write_envelope(envelope, plan)
    _write_historical_consumed_claim(envelope, claims, plan=plan)
    claim_path = claims / "fixture.notification.reconcile.0001.claim.json"
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    claim["envelope_digest"] = "sha256:" + ("0" * 64)
    claim_path.write_text(json.dumps(claim), encoding="utf-8")
    claim_path.chmod(0o600)

    with (
        patch(
            "notification_hub.reconciliation.record_channel_reconciliation"
        ) as forbidden_apply,
        pytest.raises(
            ReconciliationAuthorizationError,
            match="claim does not bind the exact envelope",
        ),
    ):
        finalize_reconciliation_claim(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    forbidden_apply.assert_not_called()
    assert not (claims / "fixture.notification.reconcile.0001.receipt.json").exists()


def test_finalize_rejects_claim_outside_original_authorization_window(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    _write_envelope(envelope, plan)
    _write_historical_consumed_claim(
        envelope,
        claims,
        plan=plan,
        claim_after_expiry=True,
    )

    with (
        patch(
            "notification_hub.reconciliation.record_channel_reconciliation"
        ) as forbidden_apply,
        pytest.raises(ReconciliationAuthorizationError, match="not currently valid"),
    ):
        finalize_reconciliation_claim(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    forbidden_apply.assert_not_called()
    assert not (claims / "fixture.notification.reconcile.0001.receipt.json").exists()


def test_finalize_rejects_broad_consumed_authority_without_receipt(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    _write_envelope(envelope, plan)
    _write_historical_consumed_claim(envelope, claims, plan=plan)
    payload = json.loads(envelope.read_text(encoding="utf-8"))
    payload["one_shot"] = False
    envelope.write_text(json.dumps(payload), encoding="utf-8")
    envelope.chmod(0o600)
    claim_path = claims / "fixture.notification.reconcile.0001.claim.json"
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    claim["envelope_digest"] = sha256_json(payload)
    claim_path.write_text(json.dumps(claim), encoding="utf-8")
    claim_path.chmod(0o600)

    with pytest.raises(
        ReconciliationAuthorizationError,
        match="does not carry exact reconciliation authority",
    ):
        finalize_reconciliation_claim(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    assert not (claims / "fixture.notification.reconcile.0001.receipt.json").exists()


def test_finalize_rejects_conflicting_existing_authority_receipt(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "inbox.sqlite3"
    event = _unknown_event(db_path)
    provider_reference = "slack:history:fixture-message-1"
    plan = build_reconciliation_plan(
        event.event_id,
        "slack",
        terminal_outcome="reconciled_succeeded",
        provider_reference=provider_reference,
        readback_result=_readback(event.event_id, provider_reference),
        database_path=db_path,
    )
    envelope = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    _write_envelope(envelope, plan)
    _write_historical_consumed_claim(envelope, claims, plan=plan)
    report = finalize_reconciliation_claim(
        plan,
        envelope_path=envelope,
        claim_state_dir=claims,
        envelope_module_path=SHARED_ENVELOPE_MODULE,
    )
    receipt_path = Path(str(report["authority_receipt_path"]))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["provider_reference"] = "slack:history:conflicting"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    receipt_path.chmod(0o600)

    with pytest.raises(
        ReconciliationAuthorizationError,
        match="does not match the consumed claim",
    ):
        finalize_reconciliation_claim(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )


def test_supersession_appends_new_receipt_without_mutation_replay(
    tmp_path: Path,
) -> None:
    _, _, original_plan, claims, original_receipt_path = (
        _outcome_unknown_authority_fixture(tmp_path)
    )
    original_bytes = original_receipt_path.read_bytes()
    supersession_plan = build_reconciliation_receipt_supersession_plan(
        original_plan,
        original_receipt_path=original_receipt_path,
    )
    targets = supersession_plan["canonical_targets"]
    assert isinstance(targets, dict)
    assert targets["resolved_terminal_outcome"] == "failed_before_effect"
    envelope = tmp_path / "supersession-envelope.json"
    _write_supersession_envelope(envelope, supersession_plan)

    with patch(
        "notification_hub.reconciliation.record_channel_reconciliation"
    ) as forbidden_mutation:
        report = apply_reconciliation_receipt_supersession_plan(
            supersession_plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    forbidden_mutation.assert_not_called()
    assert report["status"] == "superseded"
    assert report["supersedes_action_id"] == "fixture.notification.reconcile.0001"
    assert original_receipt_path.read_bytes() == original_bytes
    superseding_receipt = json.loads(
        Path(str(report["authority_receipt_path"])).read_text(encoding="utf-8")
    )
    assert superseding_receipt["action_id"] == "fixture.notification.supersede.0001"
    assert superseding_receipt["terminal_outcome"] == "succeeded"
    assert report["resolved_terminal_outcome"] == "failed_before_effect"
    assert (
        superseding_receipt["target"]["original_authority_receipt_digest"]
        == sha256_json(json.loads(original_bytes))
    )


def test_supersession_rejects_database_drift_before_new_claim(tmp_path: Path) -> None:
    db_path, event, original_plan, claims, original_receipt_path = (
        _outcome_unknown_authority_fixture(tmp_path)
    )
    supersession_plan = build_reconciliation_receipt_supersession_plan(
        original_plan,
        original_receipt_path=original_receipt_path,
    )
    targets = original_plan["canonical_targets"]
    assert isinstance(targets, dict)
    record_channel_reconciliation(
        event.event_id,
        "slack",
        reconciliation_id="fixture.notification.reconcile.0001",
        expected_unknown_evidence_digest=str(targets["unknown_evidence_digest"]),
        expected_original_provider_reference=str(
            targets["original_provider_reference"]
        ),
        terminal_outcome="reconciled_succeeded",
        provider_reference="slack:history:fixture-message-1",
        readback_result=_readback(
            event.event_id,
            "slack:history:fixture-message-1",
        ),
        artifact_digest=str(original_plan["plan_digest"]),
        provider_idempotency_key="fixture.notification.reconcile.0001",
        path=db_path,
    )
    envelope = tmp_path / "supersession-envelope.json"
    _write_supersession_envelope(envelope, supersession_plan)

    with pytest.raises(
        ReconciliationAuthorizationError,
        match="evidence changed after plan rendering",
    ):
        apply_reconciliation_receipt_supersession_plan(
            supersession_plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    assert not (claims / "fixture.notification.supersede.0001.claim.json").exists()


def test_supersession_rejects_changed_original_receipt_before_new_claim(
    tmp_path: Path,
) -> None:
    _, _, original_plan, claims, original_receipt_path = (
        _outcome_unknown_authority_fixture(tmp_path)
    )
    supersession_plan = build_reconciliation_receipt_supersession_plan(
        original_plan,
        original_receipt_path=original_receipt_path,
    )
    receipt = json.loads(original_receipt_path.read_text(encoding="utf-8"))
    receipt["recorded_at"] = "fixture-tampered"
    original_receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    original_receipt_path.chmod(0o600)
    envelope = tmp_path / "supersession-envelope.json"
    _write_supersession_envelope(envelope, supersession_plan)

    with pytest.raises(
        ReconciliationAuthorizationError,
        match="original authority receipt changed",
    ):
        apply_reconciliation_receipt_supersession_plan(
            supersession_plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    assert not (claims / "fixture.notification.supersede.0001.claim.json").exists()


def test_supersession_requires_new_action_id_and_bound_receipt_directory(
    tmp_path: Path,
) -> None:
    _, _, original_plan, claims, original_receipt_path = (
        _outcome_unknown_authority_fixture(tmp_path)
    )
    supersession_plan = build_reconciliation_receipt_supersession_plan(
        original_plan,
        original_receipt_path=original_receipt_path,
    )
    same_action_envelope = tmp_path / "same-action-envelope.json"
    _write_supersession_envelope(same_action_envelope, supersession_plan)
    payload = json.loads(same_action_envelope.read_text(encoding="utf-8"))
    payload["action_id"] = "fixture.notification.reconcile.0001"
    payload["provider_idempotency_key"] = "fixture.notification.reconcile.0001"
    same_action_envelope.write_text(json.dumps(payload), encoding="utf-8")
    same_action_envelope.chmod(0o600)

    with pytest.raises(
        ReconciliationAuthorizationError,
        match="must differ from the original action",
    ):
        apply_reconciliation_receipt_supersession_plan(
            supersession_plan,
            envelope_path=same_action_envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    alternate_dir = tmp_path / "alternate-claims"
    alternate_dir.mkdir(mode=0o700)
    envelope = tmp_path / "supersession-envelope.json"
    _write_supersession_envelope(envelope, supersession_plan)
    with pytest.raises(
        ReconciliationAuthorizationError,
        match="receipt directory does not match the plan",
    ):
        apply_reconciliation_receipt_supersession_plan(
            supersession_plan,
            envelope_path=envelope,
            claim_state_dir=alternate_dir,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    assert not (alternate_dir / "fixture.notification.supersede.0001.claim.json").exists()


def test_supersession_rejects_broad_authority_before_claim(tmp_path: Path) -> None:
    _, _, original_plan, claims, original_receipt_path = (
        _outcome_unknown_authority_fixture(tmp_path)
    )
    supersession_plan = build_reconciliation_receipt_supersession_plan(
        original_plan,
        original_receipt_path=original_receipt_path,
    )
    envelope = tmp_path / "supersession-envelope.json"
    _write_supersession_envelope(envelope, supersession_plan)
    payload = json.loads(envelope.read_text(encoding="utf-8"))
    payload["bounds"] = {"allowed_effect_count": 2}
    envelope.write_text(json.dumps(payload), encoding="utf-8")
    envelope.chmod(0o600)

    with pytest.raises(
        ReconciliationAuthorizationError,
        match="not exact and one-shot",
    ):
        apply_reconciliation_receipt_supersession_plan(
            supersession_plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    assert not (claims / "fixture.notification.supersede.0001.claim.json").exists()


def test_supersession_plan_requires_resolved_database_readback(
    tmp_path: Path,
) -> None:
    _, _, original_plan, _, original_receipt_path = (
        _outcome_unknown_authority_fixture(tmp_path)
    )
    with (
        patch(
            "notification_hub.reconciliation.read_channel_reconciliation_state",
            side_effect=sqlite3.OperationalError("fixture still unavailable"),
        ),
        pytest.raises(
            ReconciliationAuthorizationError,
            match="database state remains outcome_unknown",
        ),
    ):
        build_reconciliation_receipt_supersession_plan(
            original_plan,
            original_receipt_path=original_receipt_path,
        )


def test_supersession_plan_rejects_symlinked_original_receipt(
    tmp_path: Path,
) -> None:
    _, _, original_plan, _, original_receipt_path = (
        _outcome_unknown_authority_fixture(tmp_path)
    )
    receipt_link = tmp_path / "original-receipt-link.json"
    receipt_link.symlink_to(original_receipt_path)

    with pytest.raises(ValueError, match="non-symlink"):
        build_reconciliation_receipt_supersession_plan(
            original_plan,
            original_receipt_path=receipt_link,
        )


def test_finalize_supersession_after_abrupt_receipt_emission_never_replays(
    tmp_path: Path,
) -> None:
    db_path, _, original_plan, claims, original_receipt_path = (
        _outcome_unknown_authority_fixture(tmp_path)
    )
    original_bytes = original_receipt_path.read_bytes()
    before_db = db_path.read_bytes()
    before_mtime = db_path.stat().st_mtime_ns
    supersession_plan = build_reconciliation_receipt_supersession_plan(
        original_plan,
        original_receipt_path=original_receipt_path,
    )
    envelope = tmp_path / "supersession-envelope.json"
    _write_supersession_envelope(envelope, supersession_plan)
    load_envelope, claim_envelope, _ = reconciliation_module._load_envelope_functions(
        SHARED_ENVELOPE_MODULE
    )

    def abrupt_emit(*args: object, **kwargs: object) -> Path:
        raise SystemExit("fixture abrupt receipt termination")

    with (
        patch(
            "notification_hub.reconciliation._load_envelope_functions",
            return_value=(load_envelope, claim_envelope, abrupt_emit),
        ),
        pytest.raises(SystemExit, match="fixture abrupt receipt termination"),
    ):
        apply_reconciliation_receipt_supersession_plan(
            supersession_plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    plan_path = claims / "fixture.notification.supersede.0001.plan.json"
    assert json.loads(plan_path.read_text(encoding="utf-8")) == supersession_plan
    assert (claims / "fixture.notification.supersede.0001.claim.json").exists()
    assert not (claims / "fixture.notification.supersede.0001.receipt.json").exists()

    with (
        patch(
            "notification_hub.reconciliation.record_channel_reconciliation"
        ) as forbidden_mutation,
        patch(
            "notification_hub.reconciliation.read_channel_reconciliation_state"
        ) as forbidden_database_read,
    ):
        report = finalize_reconciliation_claim(
            load_readback_file(plan_path),
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    forbidden_mutation.assert_not_called()
    forbidden_database_read.assert_not_called()
    assert report["status"] == "finalized"
    assert report["terminal_outcome"] == "succeeded"
    assert report["resolved_terminal_outcome"] == "failed_before_effect"
    assert original_receipt_path.read_bytes() == original_bytes
    assert db_path.read_bytes() == before_db
    assert db_path.stat().st_mtime_ns == before_mtime
    receipt_path = Path(str(report["authority_receipt_path"]))
    before_receipt = receipt_path.read_bytes()
    replay = finalize_reconciliation_claim(
        load_readback_file(plan_path),
        envelope_path=envelope,
        claim_state_dir=claims,
        envelope_module_path=SHARED_ENVELOPE_MODULE,
    )
    assert replay["status"] == "already_finalized"
    assert receipt_path.read_bytes() == before_receipt


def test_process_termination_after_real_supersession_claim_finalizes_receipt_only(
    tmp_path: Path,
) -> None:
    db_path, _, original_plan, claims, original_receipt_path = (
        _outcome_unknown_authority_fixture(tmp_path)
    )
    supersession_plan = build_reconciliation_receipt_supersession_plan(
        original_plan,
        original_receipt_path=original_receipt_path,
    )
    envelope = tmp_path / "supersession-envelope.json"
    plan_input = tmp_path / "supersession-plan-input.json"
    ready_path = tmp_path / "supersession-claim-published-ready"
    _write_supersession_envelope(envelope, supersession_plan)
    plan_input.write_text(json.dumps(supersession_plan), encoding="utf-8")
    plan_input.chmod(0o600)

    original_action_id = "fixture.notification.reconcile.0001"
    action_id = "fixture.notification.supersede.0001"
    original_plan_path = claims / f"{original_action_id}.plan.json"
    original_receipt_before = original_receipt_path.read_bytes()
    original_plan_before = original_plan_path.read_bytes()
    database_before = db_path.read_bytes()
    database_mtime_before = db_path.stat().st_mtime_ns
    child_script = """
import signal
import sys
from pathlib import Path
from unittest.mock import patch

import notification_hub.reconciliation as reconciliation

plan = reconciliation.load_readback_file(Path(sys.argv[1]))
envelope_path = Path(sys.argv[2])
claims = Path(sys.argv[3])
envelope_module = Path(sys.argv[4])
ready_path = Path(sys.argv[5])
load_envelope, claim_envelope, emit_receipt = (
    reconciliation._load_envelope_functions(envelope_module)
)

def claim_then_pause(envelope, state_dir):
    claim_path = claim_envelope(envelope, state_dir)
    ready_path.write_text("supersession-claim-published\\n", encoding="utf-8")
    ready_path.chmod(0o600)
    signal.pause()
    return claim_path

with patch.object(
    reconciliation,
    "_load_envelope_functions",
    return_value=(load_envelope, claim_then_pause, emit_receipt),
):
    reconciliation.apply_reconciliation_receipt_supersession_plan(
        plan,
        envelope_path=envelope_path,
        claim_state_dir=claims,
        envelope_module_path=envelope_module,
    )
"""
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            child_script,
            str(plan_input),
            str(envelope),
            str(claims),
            str(SHARED_ENVELOPE_MODULE),
            str(ready_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not ready_path.exists():
            if child.poll() is not None:
                stdout, stderr = child.communicate()
                pytest.fail(
                    "supersession claim process exited before interruption: "
                    f"stdout={stdout!r} stderr={stderr!r}"
                )
            if time.monotonic() >= deadline:
                pytest.fail(
                    "supersession claim process did not reach claim boundary"
                )
            time.sleep(0.01)

        plan_path = claims / f"{action_id}.plan.json"
        claim_path = claims / f"{action_id}.claim.json"
        lock_path = claims / f"{action_id}.execution.lock"
        identity_path = claims / f"{action_id}.execution.lock.identity.json"
        supersession_receipt_path = claims / f"{action_id}.receipt.json"
        plan_before = plan_path.read_bytes()
        claim_before = claim_path.read_bytes()
        lock_before = lock_path.read_bytes()
        identity_before = identity_path.read_bytes()
        assert json.loads(plan_before) == supersession_plan
        assert not supersession_receipt_path.exists()
        assert original_receipt_path.read_bytes() == original_receipt_before
        assert original_plan_path.read_bytes() == original_plan_before
        assert db_path.read_bytes() == database_before
        assert db_path.stat().st_mtime_ns == database_mtime_before

        with (
            patch(
                "notification_hub.reconciliation.record_channel_reconciliation"
            ) as forbidden_mutation,
            patch(
                "notification_hub.reconciliation.read_channel_reconciliation_state"
            ) as forbidden_database_read,
            pytest.raises(
                ReconciliationAuthorizationError,
                match="still executing",
            ),
        ):
            finalize_reconciliation_claim(
                supersession_plan,
                envelope_path=envelope,
                claim_state_dir=claims,
                envelope_module_path=SHARED_ENVELOPE_MODULE,
            )
        forbidden_mutation.assert_not_called()
        forbidden_database_read.assert_not_called()

        child.terminate()
        child.wait(timeout=5)

        with (
            patch(
                "notification_hub.reconciliation.record_channel_reconciliation"
            ) as forbidden_mutation,
            patch(
                "notification_hub.reconciliation.read_channel_reconciliation_state"
            ) as forbidden_database_read,
        ):
            report = finalize_reconciliation_claim(
                supersession_plan,
                envelope_path=envelope,
                claim_state_dir=claims,
                envelope_module_path=SHARED_ENVELOPE_MODULE,
            )
        forbidden_mutation.assert_not_called()
        forbidden_database_read.assert_not_called()

        receipt_before = supersession_receipt_path.read_bytes()
        authority_receipt = json.loads(receipt_before)
        assert report["status"] == "finalized"
        assert report["terminal_outcome"] == "succeeded"
        assert report["resolved_terminal_outcome"] == "failed_before_effect"
        assert report["supersedes_action_id"] == original_action_id
        assert plan_path.read_bytes() == plan_before
        assert claim_path.read_bytes() == claim_before
        assert lock_path.read_bytes() == lock_before
        assert identity_path.read_bytes() == identity_before
        assert original_receipt_path.read_bytes() == original_receipt_before
        assert original_plan_path.read_bytes() == original_plan_before
        assert db_path.read_bytes() == database_before
        assert db_path.stat().st_mtime_ns == database_mtime_before
        assert authority_receipt["action_id"] == action_id
        assert authority_receipt["action_kind"] == SUPERSESSION_ACTION_KIND
        assert authority_receipt["target"] == supersession_plan["canonical_targets"]
        assert authority_receipt["artifact_digest"] == supersession_plan["plan_digest"]
        assert (
            authority_receipt["provider_reference"]
            == supersession_plan["canonical_targets"]["provider_reference"]
        )
        assert (
            authority_receipt["readback_result"]
            == supersession_plan["readback_result"]
        )
        assert authority_receipt["terminal_outcome"] == "succeeded"
        assert authority_receipt["effect_count"] == 1
        assert len(list(claims.glob("*.receipt.json"))) == 2

        replay = finalize_reconciliation_claim(
            supersession_plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )
        assert replay["status"] == "already_finalized"
        assert supersession_receipt_path.read_bytes() == receipt_before
        assert original_receipt_path.read_bytes() == original_receipt_before
        assert len(list(claims.glob("*.receipt.json"))) == 2
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


def test_process_termination_after_supersession_receipt_link_reuses_exact_receipt(
    tmp_path: Path,
) -> None:
    db_path, _, original_plan, claims, original_receipt_path = (
        _outcome_unknown_authority_fixture(tmp_path)
    )
    supersession_plan = build_reconciliation_receipt_supersession_plan(
        original_plan,
        original_receipt_path=original_receipt_path,
    )
    envelope = tmp_path / "supersession-envelope.json"
    plan_input = tmp_path / "supersession-plan-input.json"
    ready_path = tmp_path / "supersession-receipt-linked-ready"
    _write_supersession_envelope(envelope, supersession_plan)
    plan_input.write_text(json.dumps(supersession_plan), encoding="utf-8")
    plan_input.chmod(0o600)

    original_action_id = "fixture.notification.reconcile.0001"
    action_id = "fixture.notification.supersede.0001"
    original_plan_path = claims / f"{original_action_id}.plan.json"
    original_receipt_before = original_receipt_path.read_bytes()
    original_plan_before = original_plan_path.read_bytes()
    database_before = db_path.read_bytes()
    database_mtime_before = db_path.stat().st_mtime_ns
    child_script = """
import os
import signal
import sys
from pathlib import Path
from unittest.mock import patch

import notification_hub.reconciliation as reconciliation

plan = reconciliation.load_readback_file(Path(sys.argv[1]))
envelope_path = Path(sys.argv[2])
claims = Path(sys.argv[3])
envelope_module = Path(sys.argv[4])
ready_path = Path(sys.argv[5])
original_link = os.link

def pause_after_supersession_receipt_link(source, destination, *args, **kwargs):
    result = original_link(source, destination, *args, **kwargs)
    if str(destination).endswith(".receipt.json"):
        ready_path.write_text("supersession-receipt-linked\\n", encoding="utf-8")
        ready_path.chmod(0o600)
        signal.pause()
    return result

with patch.object(
    reconciliation.os,
    "link",
    side_effect=pause_after_supersession_receipt_link,
):
    reconciliation.apply_reconciliation_receipt_supersession_plan(
        plan,
        envelope_path=envelope_path,
        claim_state_dir=claims,
        envelope_module_path=envelope_module,
    )
"""
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            child_script,
            str(plan_input),
            str(envelope),
            str(claims),
            str(SHARED_ENVELOPE_MODULE),
            str(ready_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not ready_path.exists():
            if child.poll() is not None:
                stdout, stderr = child.communicate()
                pytest.fail(
                    "supersession receipt process exited before interruption: "
                    f"stdout={stdout!r} stderr={stderr!r}"
                )
            if time.monotonic() >= deadline:
                pytest.fail(
                    "supersession receipt publisher did not reach atomic link boundary"
                )
            time.sleep(0.01)

        plan_path = claims / f"{action_id}.plan.json"
        claim_path = claims / f"{action_id}.claim.json"
        lock_path = claims / f"{action_id}.execution.lock"
        identity_path = claims / f"{action_id}.execution.lock.identity.json"
        supersession_receipt_path = claims / f"{action_id}.receipt.json"
        plan_before = plan_path.read_bytes()
        claim_before = claim_path.read_bytes()
        lock_before = lock_path.read_bytes()
        identity_before = identity_path.read_bytes()
        receipt_before = supersession_receipt_path.read_bytes()
        authority_receipt = json.loads(receipt_before)
        assert authority_receipt["action_id"] == action_id
        assert authority_receipt["action_kind"] == SUPERSESSION_ACTION_KIND
        assert authority_receipt["target"] == supersession_plan["canonical_targets"]
        assert authority_receipt["artifact_digest"] == supersession_plan["plan_digest"]
        assert authority_receipt["terminal_outcome"] == "succeeded"
        assert authority_receipt["effect_count"] == 1
        assert len(list(claims.glob("*.receipt.json"))) == 2
        assert original_receipt_path.read_bytes() == original_receipt_before
        assert original_plan_path.read_bytes() == original_plan_before
        assert db_path.read_bytes() == database_before
        assert db_path.stat().st_mtime_ns == database_mtime_before

        with (
            patch(
                "notification_hub.reconciliation.record_channel_reconciliation"
            ) as forbidden_mutation,
            patch(
                "notification_hub.reconciliation.read_channel_reconciliation_state"
            ) as forbidden_database_read,
            pytest.raises(
                ReconciliationAuthorizationError,
                match="still executing",
            ),
        ):
            finalize_reconciliation_claim(
                supersession_plan,
                envelope_path=envelope,
                claim_state_dir=claims,
                envelope_module_path=SHARED_ENVELOPE_MODULE,
            )
        forbidden_mutation.assert_not_called()
        forbidden_database_read.assert_not_called()

        child.terminate()
        child.wait(timeout=5)

        with (
            patch(
                "notification_hub.reconciliation.record_channel_reconciliation"
            ) as forbidden_mutation,
            patch(
                "notification_hub.reconciliation.read_channel_reconciliation_state"
            ) as forbidden_database_read,
            patch(
                "notification_hub.reconciliation.os.link"
            ) as forbidden_publication,
        ):
            report = finalize_reconciliation_claim(
                supersession_plan,
                envelope_path=envelope,
                claim_state_dir=claims,
                envelope_module_path=SHARED_ENVELOPE_MODULE,
            )
        forbidden_mutation.assert_not_called()
        forbidden_database_read.assert_not_called()
        forbidden_publication.assert_not_called()

        assert report["status"] == "already_finalized"
        assert report["authority_receipt"] == authority_receipt
        assert report["terminal_outcome"] == "succeeded"
        assert report["resolved_terminal_outcome"] == "failed_before_effect"
        assert report["supersedes_action_id"] == original_action_id
        assert plan_path.read_bytes() == plan_before
        assert claim_path.read_bytes() == claim_before
        assert lock_path.read_bytes() == lock_before
        assert identity_path.read_bytes() == identity_before
        assert supersession_receipt_path.read_bytes() == receipt_before
        assert original_receipt_path.read_bytes() == original_receipt_before
        assert original_plan_path.read_bytes() == original_plan_before
        assert db_path.read_bytes() == database_before
        assert db_path.stat().st_mtime_ns == database_mtime_before

        replay = finalize_reconciliation_claim(
            supersession_plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )
        assert replay["status"] == "already_finalized"
        assert replay["authority_receipt"] == authority_receipt
        assert supersession_receipt_path.read_bytes() == receipt_before
        assert original_receipt_path.read_bytes() == original_receipt_before
        assert len(list(claims.glob("*.receipt.json"))) == 2
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


def test_concurrent_supersession_finalizers_share_one_immutable_receipt(
    tmp_path: Path,
) -> None:
    _, _, original_plan, claims, original_receipt_path = (
        _outcome_unknown_authority_fixture(tmp_path)
    )
    supersession_plan = build_reconciliation_receipt_supersession_plan(
        original_plan,
        original_receipt_path=original_receipt_path,
    )
    envelope = tmp_path / "supersession-envelope.json"
    _write_supersession_envelope(envelope, supersession_plan)
    load_envelope, claim_envelope, emit_receipt = (
        reconciliation_module._load_envelope_functions(
            SHARED_ENVELOPE_MODULE
        )
    )

    def abrupt_emit(*args: object, **kwargs: object) -> Path:
        raise SystemExit("fixture abrupt receipt termination")

    with (
        patch(
            "notification_hub.reconciliation._load_envelope_functions",
            return_value=(load_envelope, claim_envelope, abrupt_emit),
        ),
        pytest.raises(SystemExit, match="fixture abrupt receipt termination"),
    ):
        apply_reconciliation_receipt_supersession_plan(
            supersession_plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    barrier = threading.Barrier(2)

    def coordinated_emit(*args: object, **kwargs: object) -> Path:
        barrier.wait(timeout=5)
        return emit_receipt(*args, **kwargs)

    def finalize_once() -> dict[str, object]:
        return finalize_reconciliation_claim(
            supersession_plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    with (
        patch(
            "notification_hub.reconciliation._load_envelope_functions",
            return_value=(load_envelope, claim_envelope, coordinated_emit),
        ),
        patch(
            "notification_hub.reconciliation.record_channel_reconciliation"
        ) as forbidden_mutation,
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        futures = [executor.submit(finalize_once) for _ in range(2)]
        reports = [future.result(timeout=10) for future in futures]

    forbidden_mutation.assert_not_called()
    assert [report["status"] for report in reports] == ["finalized", "finalized"]
    assert {
        str(report["authority_receipt_path"])
        for report in reports
    } == {str(claims / "fixture.notification.supersede.0001.receipt.json")}
    assert len(
        list(
            claims.glob(
                "fixture.notification.supersede.0001.receipt.json"
            )
        )
    ) == 1
    receipt = json.loads(
        (claims / "fixture.notification.supersede.0001.receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert reports[0]["authority_receipt"] == receipt
    assert reports[1]["authority_receipt"] == receipt
    assert receipt["terminal_outcome"] == "succeeded"


def test_concurrent_supersession_finalizer_processes_share_one_immutable_receipt(
    tmp_path: Path,
) -> None:
    db_path, _, original_plan, claims, original_receipt_path = (
        _outcome_unknown_authority_fixture(tmp_path)
    )
    supersession_plan = build_reconciliation_receipt_supersession_plan(
        original_plan,
        original_receipt_path=original_receipt_path,
    )
    envelope = tmp_path / "supersession-envelope.json"
    plan_input = tmp_path / "supersession-plan-input.json"
    release_path = tmp_path / "release-supersession-finalizers"
    _write_supersession_envelope(envelope, supersession_plan)
    plan_input.write_text(json.dumps(supersession_plan), encoding="utf-8")
    plan_input.chmod(0o600)
    load_envelope, claim_envelope, _ = (
        reconciliation_module._load_envelope_functions(
            SHARED_ENVELOPE_MODULE
        )
    )

    def abrupt_emit(*args: object, **kwargs: object) -> Path:
        raise SystemExit("fixture abrupt receipt termination")

    with (
        patch(
            "notification_hub.reconciliation._load_envelope_functions",
            return_value=(load_envelope, claim_envelope, abrupt_emit),
        ),
        pytest.raises(SystemExit, match="fixture abrupt receipt termination"),
    ):
        apply_reconciliation_receipt_supersession_plan(
            supersession_plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    original_action_id = "fixture.notification.reconcile.0001"
    action_id = "fixture.notification.supersede.0001"
    original_plan_path = claims / f"{original_action_id}.plan.json"
    plan_path = claims / f"{action_id}.plan.json"
    claim_path = claims / f"{action_id}.claim.json"
    lock_path = claims / f"{action_id}.execution.lock"
    identity_path = claims / f"{action_id}.execution.lock.identity.json"
    supersession_receipt_path = claims / f"{action_id}.receipt.json"
    original_receipt_before = original_receipt_path.read_bytes()
    original_plan_before = original_plan_path.read_bytes()
    plan_before = plan_path.read_bytes()
    claim_before = claim_path.read_bytes()
    lock_before = lock_path.read_bytes()
    identity_before = identity_path.read_bytes()
    database_before = db_path.read_bytes()
    database_mtime_before = db_path.stat().st_mtime_ns
    child_script = """
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import notification_hub.reconciliation as reconciliation

plan = reconciliation.load_readback_file(Path(sys.argv[1]))
envelope_path = Path(sys.argv[2])
claims = Path(sys.argv[3])
envelope_module = Path(sys.argv[4])
ready_path = Path(sys.argv[5])
release_path = Path(sys.argv[6])
report_path = Path(sys.argv[7])
load_envelope, claim_envelope, emit_receipt = (
    reconciliation._load_envelope_functions(envelope_module)
)

def coordinated_emit(*args, **kwargs):
    ready_path.write_text("ready-to-publish\\n", encoding="utf-8")
    ready_path.chmod(0o600)
    deadline = time.monotonic() + 5
    while not release_path.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("fixture release was not published")
        time.sleep(0.01)
    return emit_receipt(*args, **kwargs)

with (
    patch.object(
        reconciliation,
        "_load_envelope_functions",
        return_value=(load_envelope, claim_envelope, coordinated_emit),
    ),
    patch.object(
        reconciliation,
        "record_channel_reconciliation",
        side_effect=AssertionError("reconciliation mutation is forbidden"),
    ),
    patch.object(
        reconciliation,
        "read_channel_reconciliation_state",
        side_effect=AssertionError("database readback is forbidden"),
    ),
):
    report = reconciliation.finalize_reconciliation_claim(
        plan,
        envelope_path=envelope_path,
        claim_state_dir=claims,
        envelope_module_path=envelope_module,
    )
report_path.write_text(
    json.dumps(report, sort_keys=True) + "\\n",
    encoding="utf-8",
)
report_path.chmod(0o600)
"""
    ready_paths = [
        tmp_path / "supersession-finalizer-1-ready",
        tmp_path / "supersession-finalizer-2-ready",
    ]
    report_paths = [
        tmp_path / "supersession-finalizer-1-report.json",
        tmp_path / "supersession-finalizer-2-report.json",
    ]
    children = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                child_script,
                str(plan_input),
                str(envelope),
                str(claims),
                str(SHARED_ENVELOPE_MODULE),
                str(ready_path),
                str(release_path),
                str(report_path),
            ],
            cwd=Path(__file__).resolve().parents[1],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for ready_path, report_path in zip(
            ready_paths,
            report_paths,
            strict=True,
        )
    ]
    try:
        deadline = time.monotonic() + 5
        while not all(path.exists() for path in ready_paths):
            for child in children:
                if child.poll() is not None:
                    stdout, stderr = child.communicate()
                    pytest.fail(
                        "supersession finalizer exited before publication race: "
                        f"stdout={stdout!r} stderr={stderr!r}"
                    )
            if time.monotonic() >= deadline:
                pytest.fail(
                    "supersession finalizers did not reach publication barrier"
                )
            time.sleep(0.01)

        assert not supersession_receipt_path.exists()
        release_path.write_text("publish\n", encoding="utf-8")
        release_path.chmod(0o600)
        for child in children:
            child.wait(timeout=5)
            if child.returncode != 0:
                stdout, stderr = child.communicate()
                pytest.fail(
                    "supersession finalizer failed during publication race: "
                    f"stdout={stdout!r} stderr={stderr!r}"
                )

        reports = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in report_paths
        ]
        receipt_before = supersession_receipt_path.read_bytes()
        receipt = json.loads(receipt_before)
        assert [report["status"] for report in reports] == [
            "finalized",
            "finalized",
        ]
        assert reports[0]["authority_receipt"] == receipt
        assert reports[1]["authority_receipt"] == receipt
        assert {
            str(report["authority_receipt_path"])
            for report in reports
        } == {str(supersession_receipt_path)}
        assert receipt["action_id"] == action_id
        assert receipt["action_kind"] == SUPERSESSION_ACTION_KIND
        assert receipt["target"] == supersession_plan["canonical_targets"]
        assert receipt["artifact_digest"] == supersession_plan["plan_digest"]
        assert receipt["terminal_outcome"] == "succeeded"
        assert receipt["effect_count"] == 1
        assert len(list(claims.glob("*.receipt.json"))) == 2
        assert not list(claims.glob(".receipt-*"))
        assert original_receipt_path.read_bytes() == original_receipt_before
        assert original_plan_path.read_bytes() == original_plan_before
        assert plan_path.read_bytes() == plan_before
        assert claim_path.read_bytes() == claim_before
        assert lock_path.read_bytes() == lock_before
        assert identity_path.read_bytes() == identity_before
        assert db_path.read_bytes() == database_before
        assert db_path.stat().st_mtime_ns == database_mtime_before

        replay = finalize_reconciliation_claim(
            supersession_plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )
        assert replay["status"] == "already_finalized"
        assert replay["authority_receipt"] == receipt
        assert supersession_receipt_path.read_bytes() == receipt_before
        assert len(list(claims.glob("*.receipt.json"))) == 2
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)


def test_finalize_supersession_accepts_expired_envelope_claimed_while_valid(
    tmp_path: Path,
) -> None:
    _, _, original_plan, claims, original_receipt_path = (
        _outcome_unknown_authority_fixture(tmp_path)
    )
    supersession_plan = build_reconciliation_receipt_supersession_plan(
        original_plan,
        original_receipt_path=original_receipt_path,
    )
    envelope = tmp_path / "supersession-envelope.json"
    _write_supersession_envelope(envelope, supersession_plan)
    _write_historical_consumed_claim(
        envelope,
        claims,
        plan=supersession_plan,
    )
    action_id = "fixture.notification.supersede.0001"
    plan_path = claims / f"{action_id}.plan.json"

    report = finalize_reconciliation_claim(
        load_readback_file(plan_path),
        envelope_path=envelope,
        claim_state_dir=claims,
        envelope_module_path=SHARED_ENVELOPE_MODULE,
    )

    assert report["status"] == "finalized"
    assert report["terminal_outcome"] == "succeeded"


def test_finalize_supersession_rejects_changed_persisted_plan_without_receipt(
    tmp_path: Path,
) -> None:
    _, _, original_plan, claims, original_receipt_path = (
        _outcome_unknown_authority_fixture(tmp_path)
    )
    supersession_plan = build_reconciliation_receipt_supersession_plan(
        original_plan,
        original_receipt_path=original_receipt_path,
    )
    envelope = tmp_path / "supersession-envelope.json"
    _write_supersession_envelope(envelope, supersession_plan)
    load_envelope, claim_envelope, _ = reconciliation_module._load_envelope_functions(
        SHARED_ENVELOPE_MODULE
    )

    def abrupt_emit(*args: object, **kwargs: object) -> Path:
        raise SystemExit("fixture abrupt receipt termination")

    with (
        patch(
            "notification_hub.reconciliation._load_envelope_functions",
            return_value=(load_envelope, claim_envelope, abrupt_emit),
        ),
        pytest.raises(SystemExit, match="fixture abrupt receipt termination"),
    ):
        apply_reconciliation_receipt_supersession_plan(
            supersession_plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )
    plan_path = claims / "fixture.notification.supersede.0001.plan.json"
    changed_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    changed_plan["effect_count"] = 2
    plan_path.write_text(json.dumps(changed_plan), encoding="utf-8")
    plan_path.chmod(0o600)

    with pytest.raises(
        ReconciliationAuthorizationError,
        match="persisted supersession plan does not match",
    ):
        finalize_reconciliation_claim(
            supersession_plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    assert not (claims / "fixture.notification.supersede.0001.receipt.json").exists()


def test_finalize_supersession_rejects_broad_consumed_authority(
    tmp_path: Path,
) -> None:
    _, _, original_plan, claims, original_receipt_path = (
        _outcome_unknown_authority_fixture(tmp_path)
    )
    supersession_plan = build_reconciliation_receipt_supersession_plan(
        original_plan,
        original_receipt_path=original_receipt_path,
    )
    envelope = tmp_path / "supersession-envelope.json"
    _write_supersession_envelope(envelope, supersession_plan)
    _write_historical_consumed_claim(
        envelope,
        claims,
        plan=supersession_plan,
    )
    action_id = "fixture.notification.supersede.0001"
    plan_path = claims / f"{action_id}.plan.json"
    payload = json.loads(envelope.read_text(encoding="utf-8"))
    payload["one_shot"] = False
    envelope.write_text(json.dumps(payload), encoding="utf-8")
    envelope.chmod(0o600)
    claim_path = claims / f"{action_id}.claim.json"
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    claim["envelope_digest"] = sha256_json(payload)
    claim_path.write_text(json.dumps(claim), encoding="utf-8")
    claim_path.chmod(0o600)

    with pytest.raises(
        ReconciliationAuthorizationError,
        match="not exact and one-shot",
    ):
        finalize_reconciliation_claim(
            load_readback_file(plan_path),
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    assert not (claims / f"{action_id}.receipt.json").exists()
