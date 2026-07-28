"""Fresh-authority delivery-check receipt supersession tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from notification_hub.channels import ChannelDeliveryResult
from notification_hub.delivery_check import (
    ACTION_KIND,
    REQUIRED_READBACK,
    SOURCE_REVISION,
    apply_delivery_check_plan,
    build_delivery_check_plan,
)
from notification_hub.delivery_check_supersession import (
    PROVIDER_READBACK_SCHEMA,
    SUPERSESSION_ACTION_KIND,
    SUPERSESSION_REQUIRED_READBACK,
    DeliveryCheckSupersessionAuthorizationError,
    apply_delivery_check_receipt_supersession_plan,
    build_delivery_check_receipt_supersession_plan,
    finalize_delivery_check_receipt_supersession_claim,
)

SHARED_ENVELOPE_MODULE = Path(
    "/Users/d/.codex/scripts/security/irreversible_action_envelope.py"
)


def _write_original_envelope(path: Path, plan: dict[str, object]) -> None:
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "schema": "IrreversibleActionEnvelopeV1",
        "action_id": "fixture.notification.delivery-check.original",
        "action_kind": ACTION_KIND,
        "principal": {"id": "fixture-operator", "kind": "test-fixture"},
        "canonical_targets": plan["canonical_targets"],
        "source_revision": SOURCE_REVISION,
        "artifact_digest": plan["plan_digest"],
        "bounds": {"allowed_effect_count": 1},
        "issued_at": (now - timedelta(seconds=1)).isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
        "one_shot": True,
        "provider_idempotency_key": "fixture.notification.delivery-check.original",
        "preconditions": {
            "live_smoke_policy_required": True,
            "plan_digest": plan["plan_digest"],
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


def _unknown_original(tmp_path: Path) -> tuple[Path, Path]:
    claims = tmp_path / "original-claims"
    plan = build_delivery_check_plan(
        verify_slack=True,
        receipt_state_dir=claims,
    )
    envelope = tmp_path / "original-envelope.json"
    _write_original_envelope(envelope, plan)
    with (
        patch("notification_hub.delivery_check.live_smoke_authorized", return_value=True),
        patch(
            "notification_hub.delivery_check.send_slack_with_result",
            return_value=ChannelDeliveryResult(
                False,
                receipt="slack:webhook:response_unknown",
                outcome_unknown=True,
            ),
        ),
    ):
        report = apply_delivery_check_plan(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )
    return (
        Path(str(report["plan_artifact_path"])),
        Path(str(report["authority_receipt_path"])),
    )


def _write_provider_readback(
    path: Path,
    *,
    outcome: str = "accepted",
) -> None:
    payload: dict[str, object] = {
        "schema": PROVIDER_READBACK_SCHEMA,
        "original_action_id": "fixture.notification.delivery-check.original",
        "channel": "slack",
        "provider_outcome": outcome,
        "provider_reference": "slack:history:fixture-message-1",
        "observed_at": datetime.now(UTC).isoformat(),
        "evidence": {"message_id": "fixture-message-1"},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)


def _write_supersession_envelope(
    path: Path,
    plan: dict[str, object],
) -> None:
    now = datetime.now(UTC)
    targets = plan["canonical_targets"]
    assert isinstance(targets, dict)
    payload: dict[str, object] = {
        "schema": "IrreversibleActionEnvelopeV1",
        "action_id": "fixture.notification.delivery-check.supersede",
        "action_kind": SUPERSESSION_ACTION_KIND,
        "principal": {"id": "fixture-operator", "kind": "test-fixture"},
        "canonical_targets": targets,
        "source_revision": targets["original_authority_receipt_digest"],
        "artifact_digest": plan["plan_digest"],
        "bounds": {"allowed_effect_count": 1},
        "issued_at": (now - timedelta(seconds=1)).isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
        "one_shot": True,
        "provider_idempotency_key": "fixture.notification.delivery-check.supersede",
        "preconditions": {
            "original_authority_receipt_digest": targets[
                "original_authority_receipt_digest"
            ],
            "provider_readback_digest": targets["provider_readback_digest"],
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


@pytest.mark.parametrize(
    ("provider_outcome", "resolved_terminal_outcome"),
    [("accepted", "succeeded"), ("absent", "failed_before_effect")],
)
def test_single_channel_unknown_can_be_superseded_without_transport(
    tmp_path: Path,
    provider_outcome: str,
    resolved_terminal_outcome: str,
) -> None:
    original_plan, original_receipt = _unknown_original(tmp_path)
    provider_readback = tmp_path / "provider-readback.json"
    fresh_claims = tmp_path / "supersession-claims"
    _write_provider_readback(provider_readback, outcome=provider_outcome)
    plan = build_delivery_check_receipt_supersession_plan(
        original_plan_path=original_plan,
        original_receipt_path=original_receipt,
        provider_readback_path=provider_readback,
        receipt_state_dir=fresh_claims,
    )
    envelope = tmp_path / "supersession-envelope.json"
    _write_supersession_envelope(envelope, plan)
    original_receipt_before = original_receipt.read_bytes()

    with (
        patch("notification_hub.delivery_check.send_slack_with_result") as slack,
        patch("notification_hub.delivery_check.send_push_with_result") as push,
    ):
        report = apply_delivery_check_receipt_supersession_plan(
            plan,
            envelope_path=envelope,
            claim_state_dir=fresh_claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    slack.assert_not_called()
    push.assert_not_called()
    assert report["status"] == "superseded"
    assert report["resolved_terminal_outcome"] == resolved_terminal_outcome
    assert original_receipt.read_bytes() == original_receipt_before
    receipt = json.loads(
        Path(str(report["authority_receipt_path"])).read_text(encoding="utf-8")
    )
    assert receipt["terminal_outcome"] == "succeeded"
    assert receipt["readback_result"]["provider_outcome"] == provider_outcome


def test_changed_provider_readback_cannot_consume_older_supersession_plan(
    tmp_path: Path,
) -> None:
    original_plan, original_receipt = _unknown_original(tmp_path)
    provider_readback = tmp_path / "provider-readback.json"
    fresh_claims = tmp_path / "supersession-claims"
    _write_provider_readback(provider_readback)
    plan = build_delivery_check_receipt_supersession_plan(
        original_plan_path=original_plan,
        original_receipt_path=original_receipt,
        provider_readback_path=provider_readback,
        receipt_state_dir=fresh_claims,
    )
    envelope = tmp_path / "supersession-envelope.json"
    _write_supersession_envelope(envelope, plan)
    _write_provider_readback(provider_readback, outcome="absent")

    with (
        pytest.raises(
            DeliveryCheckSupersessionAuthorizationError,
            match="evidence changed",
        ),
        patch("notification_hub.delivery_check.send_slack_with_result") as slack,
    ):
        apply_delivery_check_receipt_supersession_plan(
            plan,
            envelope_path=envelope,
            claim_state_dir=fresh_claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    slack.assert_not_called()
    assert not fresh_claims.exists()


def test_multi_channel_original_is_rejected_before_provider_readback(
    tmp_path: Path,
) -> None:
    original_plan = tmp_path / "multi.plan.json"
    original_receipt = tmp_path / "multi.receipt.json"
    provider_readback = tmp_path / "provider-readback.json"
    plan = build_delivery_check_plan(verify_slack=True, verify_push=True)
    original_plan.write_text(json.dumps(plan), encoding="utf-8")
    original_plan.chmod(0o600)
    original_receipt.write_text("{}", encoding="utf-8")
    original_receipt.chmod(0o600)
    _write_provider_readback(provider_readback)

    with pytest.raises(
        DeliveryCheckSupersessionAuthorizationError,
        match="single-channel",
    ):
        build_delivery_check_receipt_supersession_plan(
            original_plan_path=original_plan,
            original_receipt_path=original_receipt,
            provider_readback_path=provider_readback,
            receipt_state_dir=tmp_path / "fresh-claims",
        )


def test_supersession_claim_can_be_finalized_without_transport_replay(
    tmp_path: Path,
) -> None:
    original_plan, original_receipt = _unknown_original(tmp_path)
    provider_readback = tmp_path / "provider-readback.json"
    fresh_claims = tmp_path / "supersession-claims"
    _write_provider_readback(provider_readback)
    plan = build_delivery_check_receipt_supersession_plan(
        original_plan_path=original_plan,
        original_receipt_path=original_receipt,
        provider_readback_path=provider_readback,
        receipt_state_dir=fresh_claims,
    )
    envelope = tmp_path / "supersession-envelope.json"
    _write_supersession_envelope(envelope, plan)

    with (
        patch(
            "notification_hub.delivery_check_supersession._emit_supersession_receipt",
            side_effect=SystemExit("fixture after fresh claim"),
        ),
        pytest.raises(SystemExit, match="after fresh claim"),
    ):
        apply_delivery_check_receipt_supersession_plan(
            plan,
            envelope_path=envelope,
            claim_state_dir=fresh_claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    with (
        patch("notification_hub.delivery_check.send_slack_with_result") as slack,
        patch("notification_hub.delivery_check.send_push_with_result") as push,
    ):
        report = finalize_delivery_check_receipt_supersession_claim(
            plan,
            envelope_path=envelope,
            claim_state_dir=fresh_claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    slack.assert_not_called()
    push.assert_not_called()
    assert report["status"] == "finalized"
    receipt_path = Path(str(report["authority_receipt_path"]))
    receipt_before = receipt_path.read_bytes()
    replay = finalize_delivery_check_receipt_supersession_claim(
        plan,
        envelope_path=envelope,
        claim_state_dir=fresh_claims,
        envelope_module_path=SHARED_ENVELOPE_MODULE,
    )
    assert replay["status"] == "already_finalized"
    assert receipt_path.read_bytes() == receipt_before
