"""Envelope-gated delivery-check tests with fake transports only."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from multiprocessing import get_context
from multiprocessing.connection import Connection
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from notification_hub.channels import ChannelDeliveryResult
from notification_hub.delivery_check import (
    ACTION_KIND,
    REQUIRED_READBACK,
    SOURCE_REVISION,
    DeliveryCheckAuthorizationError,
    apply_delivery_check_plan,
    build_delivery_check_plan,
    finalize_delivery_check_claim,
)
from notification_hub.operations import run_delivery_check

from tests.envelope_support import (  # noqa: E402
    SHARED_ENVELOPE_MODULE,
    requires_shared_envelope_module,
)

pytestmark = requires_shared_envelope_module


def _write_envelope(path: Path, plan: dict[str, object]) -> None:
    now = datetime.now(UTC)
    targets = plan["canonical_targets"]
    assert isinstance(targets, dict)
    typed_targets = cast(dict[str, object], targets)
    channels_object = typed_targets.get("channels")
    assert isinstance(channels_object, list)
    channels = cast(list[object], channels_object)
    payload: dict[str, object] = {
        "schema": "IrreversibleActionEnvelopeV1",
        "action_id": "fixture.notification.delivery-check.0001",
        "action_kind": ACTION_KIND,
        "principal": {"id": "fixture-operator", "kind": "test-fixture"},
        "canonical_targets": targets,
        "source_revision": SOURCE_REVISION,
        "artifact_digest": plan["plan_digest"],
        "bounds": {"allowed_effect_count": len(channels)},
        "issued_at": (now - timedelta(seconds=1)).isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
        "one_shot": True,
        "provider_idempotency_key": "fixture.notification.delivery-check.0001",
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


def _run_delivery_check_paused_after_claim(
    plan: dict[str, object],
    envelope_path: Path,
    claim_state_dir: Path,
    ready: Connection,
) -> None:
    import notification_hub.delivery_check as delivery_check_module

    real_loader = delivery_check_module.load_envelope_functions

    def paused_loader(
        module_path: Path | None,
    ) -> tuple[object, object, object]:
        load_envelope, claim_envelope, emit_receipt = real_loader(module_path)

        def claim_then_pause(payload: dict[str, object], state_dir: Path) -> Path:
            claim_envelope(payload, state_dir)
            ready.send("claimed")
            while True:
                time.sleep(1)

        return load_envelope, claim_then_pause, emit_receipt

    with (
        patch.object(
            delivery_check_module,
            "load_envelope_functions",
            side_effect=paused_loader,
        ),
        patch.object(
            delivery_check_module,
            "live_smoke_authorized",
            return_value=True,
        ),
    ):
        delivery_check_module.apply_delivery_check_plan(
            plan,
            envelope_path=envelope_path,
            claim_state_dir=claim_state_dir,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )


def test_delivery_check_is_plan_only_by_default() -> None:
    with (
        patch("notification_hub.delivery_check.send_slack_with_result") as slack,
        patch("notification_hub.delivery_check.send_push_with_result") as push,
    ):
        report = run_delivery_check(verify_slack=True, verify_push=True)

    assert report["status"] == "planned"
    assert report["event_id"] is None
    assert report.get("plan") == build_delivery_check_plan(
        verify_slack=True,
        verify_push=True,
    )
    slack.assert_not_called()
    push.assert_not_called()


def test_delivery_check_requires_product_policy_before_claim(
    tmp_path: Path,
) -> None:
    claims = tmp_path / "claims"
    plan = build_delivery_check_plan(
        verify_slack=True,
        receipt_state_dir=claims,
    )
    envelope = tmp_path / "envelope.json"
    _write_envelope(envelope, plan)
    with (
        patch("notification_hub.delivery_check.live_smoke_authorized", return_value=False),
        patch("notification_hub.delivery_check.send_slack_with_result") as slack,
        pytest.raises(DeliveryCheckAuthorizationError, match="product live-smoke"),
    ):
        apply_delivery_check_plan(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )
    slack.assert_not_called()
    assert not claims.exists()


def test_exact_delivery_check_emits_stable_receipt_and_denies_replay(
    tmp_path: Path,
) -> None:
    claims = tmp_path / "claims"
    plan = build_delivery_check_plan(
        verify_slack=True,
        receipt_state_dir=claims,
    )
    envelope = tmp_path / "envelope.json"
    _write_envelope(envelope, plan)
    with (
        patch("notification_hub.delivery_check.live_smoke_authorized", return_value=True),
        patch(
            "notification_hub.delivery_check.send_slack_with_result",
            return_value=ChannelDeliveryResult(
                True,
                receipt="slack:webhook:http:2xx",
            ),
        ) as slack,
    ):
        report = apply_delivery_check_plan(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )
        with pytest.raises(DeliveryCheckAuthorizationError, match="already been claimed"):
            apply_delivery_check_plan(
                plan,
                envelope_path=envelope,
                claim_state_dir=claims,
                envelope_module_path=SHARED_ENVELOPE_MODULE,
            )

    assert slack.call_count == 1
    assert report["status"] == "ok"
    assert report["terminal_outcome"] == "succeeded"
    receipt_path = Path(str(report["authority_receipt_path"]))
    receipt_before = receipt_path.read_bytes()
    receipt = json.loads(receipt_before)
    assert receipt["action_id"] == "fixture.notification.delivery-check.0001"
    assert receipt["target"] == plan["canonical_targets"]
    assert receipt["artifact_digest"] == plan["plan_digest"]
    assert receipt["provider_reference"] == "slack=slack:webhook:http:2xx"
    assert receipt["terminal_outcome"] == "succeeded"
    assert receipt["effect_count"] == 1
    assert receipt_path.read_bytes() == receipt_before


def test_delivery_check_envelope_cannot_replay_in_a_second_claim_directory(
    tmp_path: Path,
) -> None:
    first_claims = tmp_path / "claims-a"
    second_claims = tmp_path / "claims-b"
    plan = build_delivery_check_plan(
        verify_slack=True,
        receipt_state_dir=first_claims,
    )
    envelope = tmp_path / "envelope.json"
    _write_envelope(envelope, plan)
    with (
        patch("notification_hub.delivery_check.live_smoke_authorized", return_value=True),
        patch(
            "notification_hub.delivery_check.send_slack_with_result",
            return_value=ChannelDeliveryResult(
                True,
                receipt="slack:webhook:http:2xx",
            ),
        ) as slack,
    ):
        apply_delivery_check_plan(
            plan,
            envelope_path=envelope,
            claim_state_dir=first_claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )
        with pytest.raises(
            DeliveryCheckAuthorizationError,
            match="claim state directory does not match",
        ):
            apply_delivery_check_plan(
                plan,
                envelope_path=envelope,
                claim_state_dir=second_claims,
                envelope_module_path=SHARED_ENVELOPE_MODULE,
            )

    assert slack.call_count == 1
    assert not second_claims.exists()


def test_changed_channel_plan_cannot_consume_older_authority(
    tmp_path: Path,
) -> None:
    claims = tmp_path / "claims"
    slack_plan = build_delivery_check_plan(
        verify_slack=True,
        receipt_state_dir=claims,
    )
    changed_plan = build_delivery_check_plan(
        verify_push=True,
        receipt_state_dir=claims,
    )
    envelope = tmp_path / "envelope.json"
    _write_envelope(envelope, slack_plan)
    with (
        patch("notification_hub.delivery_check.live_smoke_authorized", return_value=True),
        patch("notification_hub.delivery_check.send_push_with_result") as push,
        pytest.raises(DeliveryCheckAuthorizationError, match="canonical targets mismatch"),
    ):
        apply_delivery_check_plan(
            changed_plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )
    push.assert_not_called()
    assert not claims.exists()


def test_outcome_unknown_stops_remaining_channel_and_blocks_replay(
    tmp_path: Path,
) -> None:
    claims = tmp_path / "claims"
    plan = build_delivery_check_plan(
        verify_slack=True,
        verify_push=True,
        receipt_state_dir=claims,
    )
    envelope = tmp_path / "envelope.json"
    _write_envelope(envelope, plan)
    with (
        patch("notification_hub.delivery_check.live_smoke_authorized", return_value=True),
        patch(
            "notification_hub.delivery_check.send_push_with_result",
            return_value=ChannelDeliveryResult(
                False,
                receipt="push:transport:response_unknown",
                outcome_unknown=True,
            ),
        ) as push,
        patch("notification_hub.delivery_check.send_slack_with_result") as slack,
    ):
        report = apply_delivery_check_plan(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )
        with pytest.raises(DeliveryCheckAuthorizationError, match="already been claimed"):
            apply_delivery_check_plan(
                plan,
                envelope_path=envelope,
                claim_state_dir=claims,
                envelope_module_path=SHARED_ENVELOPE_MODULE,
            )

    push.assert_called_once()
    slack.assert_not_called()
    assert report["status"] == "degraded"
    assert report["terminal_outcome"] == "outcome_unknown"
    receipt = report["authority_receipt"]
    assert isinstance(receipt, dict)
    typed_receipt = cast(dict[str, object], receipt)
    assert typed_receipt["terminal_outcome"] == "outcome_unknown"
    assert typed_receipt["effect_count"] == 1
    readback = typed_receipt["readback_result"]
    assert isinstance(readback, dict)
    typed_readback = cast(dict[str, object], readback)
    channel_results = typed_readback["channel_results"]
    assert isinstance(channel_results, dict)
    typed_channel_results = cast(dict[str, object], channel_results)
    slack_result = typed_channel_results["slack"]
    assert isinstance(slack_result, dict)
    assert cast(dict[str, object], slack_result)["error_category"] == (
        "not_attempted_after_outcome_unknown"
    )


def test_finalize_after_post_claim_termination_never_replays_transport(
    tmp_path: Path,
) -> None:
    claims = tmp_path / "claims"
    plan = build_delivery_check_plan(
        verify_slack=True,
        receipt_state_dir=claims,
    )
    envelope = tmp_path / "envelope.json"
    _write_envelope(envelope, plan)

    with (
        patch(
            "notification_hub.delivery_check.send_slack_with_result",
            side_effect=SystemExit("fixture post-claim termination"),
        ),
        patch("notification_hub.delivery_check.live_smoke_authorized", return_value=True),
        pytest.raises(SystemExit, match="post-claim termination"),
    ):
        apply_delivery_check_plan(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    plan_path = claims / "fixture.notification.delivery-check.0001.plan.json"
    assert json.loads(plan_path.read_text(encoding="utf-8")) == plan
    assert (claims / "fixture.notification.delivery-check.0001.claim.json").exists()
    assert not (claims / "fixture.notification.delivery-check.0001.receipt.json").exists()

    with (
        patch("notification_hub.delivery_check.send_slack_with_result") as slack,
        patch("notification_hub.delivery_check.send_push_with_result") as push,
    ):
        report = finalize_delivery_check_claim(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    slack.assert_not_called()
    push.assert_not_called()
    assert report["status"] == "finalized"
    assert report["terminal_outcome"] == "outcome_unknown"
    receipt_path = Path(str(report["authority_receipt_path"]))
    receipt_before = receipt_path.read_bytes()
    receipt = json.loads(receipt_before)
    assert receipt["terminal_outcome"] == "outcome_unknown"
    assert receipt["effect_count"] == 1

    replay = finalize_delivery_check_claim(
        plan,
        envelope_path=envelope,
        claim_state_dir=claims,
        envelope_module_path=SHARED_ENVELOPE_MODULE,
    )
    assert replay["status"] == "already_finalized"
    assert receipt_path.read_bytes() == receipt_before


def test_killed_post_claim_executor_blocks_live_finalizer_then_recovers_no_send(
    tmp_path: Path,
) -> None:
    claims = tmp_path / "claims"
    plan = build_delivery_check_plan(
        verify_slack=True,
        receipt_state_dir=claims,
    )
    envelope = tmp_path / "envelope.json"
    _write_envelope(envelope, plan)
    parent, child = get_context("fork").Pipe(duplex=False)
    process = get_context("fork").Process(
        target=_run_delivery_check_paused_after_claim,
        args=(plan, envelope, claims, child),
    )
    process.start()
    child.close()
    try:
        assert parent.poll(5), "child did not publish the fixture claim"
        assert parent.recv() == "claimed"
        with pytest.raises(
            DeliveryCheckAuthorizationError,
            match="irreversible action is still executing",
        ):
            finalize_delivery_check_claim(
                plan,
                envelope_path=envelope,
                claim_state_dir=claims,
                envelope_module_path=SHARED_ENVELOPE_MODULE,
            )
        plan_path = claims / "fixture.notification.delivery-check.0001.plan.json"
        claim_path = claims / "fixture.notification.delivery-check.0001.claim.json"
        plan_before = plan_path.read_bytes()
        claim_before = claim_path.read_bytes()
    finally:
        process.terminate()
        process.join(timeout=5)
        parent.close()
    assert not process.is_alive()

    with (
        patch("notification_hub.delivery_check.send_slack_with_result") as slack,
        patch("notification_hub.delivery_check.send_push_with_result") as push,
    ):
        report = finalize_delivery_check_claim(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    slack.assert_not_called()
    push.assert_not_called()
    assert report["terminal_outcome"] == "outcome_unknown"
    assert plan_path.read_bytes() == plan_before
    assert claim_path.read_bytes() == claim_before


def test_finalizer_rejects_tampered_terminal_receipt_without_transport(
    tmp_path: Path,
) -> None:
    claims = tmp_path / "claims"
    plan = build_delivery_check_plan(
        verify_slack=True,
        receipt_state_dir=claims,
    )
    envelope = tmp_path / "envelope.json"
    _write_envelope(envelope, plan)
    with (
        patch("notification_hub.delivery_check.live_smoke_authorized", return_value=True),
        patch(
            "notification_hub.delivery_check.send_slack_with_result",
            return_value=ChannelDeliveryResult(
                True,
                receipt="slack:webhook:http:2xx",
            ),
        ),
    ):
        report = apply_delivery_check_plan(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )
    receipt_path = Path(str(report["authority_receipt_path"]))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["provider_reference"] = "slack=forged"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    receipt_path.chmod(0o600)

    with (
        patch("notification_hub.delivery_check.send_slack_with_result") as slack,
        patch("notification_hub.delivery_check.send_push_with_result") as push,
        pytest.raises(
            DeliveryCheckAuthorizationError,
            match="outcome is inconsistent",
        ),
    ):
        finalize_delivery_check_claim(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    slack.assert_not_called()
    push.assert_not_called()


def test_finalizer_rejects_path_shaped_action_id_before_state_access(
    tmp_path: Path,
) -> None:
    claims = tmp_path / "claims"
    plan = build_delivery_check_plan(
        verify_slack=True,
        receipt_state_dir=claims,
    )
    envelope = tmp_path / "envelope.json"
    claims.mkdir(mode=0o700)
    _write_envelope(envelope, plan)
    payload = json.loads(envelope.read_text(encoding="utf-8"))
    payload["action_id"] = "../escape"
    envelope.write_text(json.dumps(payload), encoding="utf-8")
    envelope.chmod(0o600)

    with (
        patch(
            "notification_hub.delivery_check.private_authority_directory"
        ) as state_access,
        pytest.raises(
            DeliveryCheckAuthorizationError,
            match="action_id is invalid",
        ),
    ):
        finalize_delivery_check_claim(
            plan,
            envelope_path=envelope,
            claim_state_dir=claims,
            envelope_module_path=SHARED_ENVELOPE_MODULE,
        )

    state_access.assert_not_called()
    assert not (tmp_path / "escape").exists()
