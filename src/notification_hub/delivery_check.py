"""Plan and execute one envelope-gated notification transport check."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from notification_hub.channels import (
    ChannelDeliveryResult,
    send_push_with_result,
    send_slack_with_result,
)
from notification_hub.config import live_smoke_authorized
from notification_hub.models import StoredEvent
from notification_hub.reconciliation import (
    ACTION_ID_RE,
    action_execution_lock,
    canonical_json,
    load_envelope_functions,
    load_readback_file,
    parse_claimed_at,
    persist_plan_artifact,
    private_authority_directory,
    sha256_json,
)

ACTION_KIND = "notification.delivery_check"
PLAN_SCHEMA = "NotificationDeliveryCheckPlanV2"
SOURCE_REVISION = "notification-hub.delivery-check.v2"
REQUIRED_READBACK = ["channel_results", "all_accepted"]


class DeliveryCheckAuthorizationError(ValueError):
    """Raised when a delivery check lacks exact one-shot authority."""


def build_delivery_check_plan(
    *,
    verify_slack: bool = False,
    verify_push: bool = False,
    receipt_state_dir: Path | None = None,
) -> dict[str, object]:
    channels = sorted(
        channel
        for channel, enabled in (
            ("slack", verify_slack),
            ("push", verify_push),
        )
        if enabled
    )
    if not channels:
        raise ValueError("delivery check requires at least one channel")
    targets: dict[str, object] = {
        "channels": channels,
        "transport_scope": "notification-hub:delivery-check",
    }
    if receipt_state_dir is not None:
        targets["receipt_state_dir"] = str(Path(os.path.abspath(receipt_state_dir)))
    body: dict[str, object] = {
        "schema": PLAN_SCHEMA,
        "action_kind": ACTION_KIND,
        "canonical_targets": targets,
        "source_revision": SOURCE_REVISION,
        "required_readback": REQUIRED_READBACK,
    }
    return {**body, "plan_digest": sha256_json(body)}


def _delivery_result(result: ChannelDeliveryResult) -> dict[str, object]:
    return {
        "accepted": result.accepted,
        "outcome_unknown": result.outcome_unknown,
        "provider_reference": result.receipt,
        "error_category": result.error_category,
    }


def _validate_plan(
    plan: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, object], list[str], str]:
    canonical_plan = dict(plan)
    targets_object = canonical_plan.get("canonical_targets")
    if not isinstance(targets_object, dict):
        raise DeliveryCheckAuthorizationError("delivery-check targets are invalid")
    raw_targets = cast(dict[object, object], targets_object)
    channels_object = raw_targets.get("channels")
    raw_channels = (
        cast(list[object], channels_object)
        if isinstance(channels_object, list)
        else []
    )
    if (
        not isinstance(channels_object, list)
        or any(not isinstance(channel, str) for channel in raw_channels)
    ):
        raise DeliveryCheckAuthorizationError("delivery-check channels are invalid")
    channels = [cast(str, channel) for channel in raw_channels]
    receipt_state_dir_object = raw_targets.get("receipt_state_dir")
    if receipt_state_dir_object is not None and not isinstance(
        receipt_state_dir_object, str
    ):
        raise DeliveryCheckAuthorizationError(
            "delivery-check receipt state directory is invalid"
        )
    expected = build_delivery_check_plan(
        verify_slack="slack" in channels,
        verify_push="push" in channels,
        receipt_state_dir=(
            Path(receipt_state_dir_object)
            if isinstance(receipt_state_dir_object, str)
            else None
        ),
    )
    if canonical_json(canonical_plan) != canonical_json(expected):
        raise DeliveryCheckAuthorizationError("delivery-check plan is invalid")
    targets = cast(dict[str, object], canonical_plan["canonical_targets"])
    plan_digest = cast(str, canonical_plan["plan_digest"])
    return canonical_plan, targets, channels, plan_digest


def _validate_exact_authority(
    envelope: Mapping[str, object],
    *,
    channels: list[str],
    plan_digest: str,
) -> str:
    bounds_object = envelope.get("bounds")
    bounds = (
        cast(dict[object, object], bounds_object)
        if isinstance(bounds_object, dict)
        else {}
    )
    if (
        envelope.get("one_shot") is not True
        or bounds.get("allowed_effect_count") != len(channels)
        or envelope.get("preconditions")
        != {
            "live_smoke_policy_required": True,
            "plan_digest": plan_digest,
        }
        or envelope.get("required_readback") != REQUIRED_READBACK
    ):
        raise DeliveryCheckAuthorizationError(
            "delivery-check authority is not exact and one-shot"
        )
    provider_idempotency_key = envelope.get("provider_idempotency_key")
    if not isinstance(provider_idempotency_key, str):
        raise DeliveryCheckAuthorizationError(
            "delivery-check provider idempotency key is invalid"
        )
    return provider_idempotency_key


def _unknown_readback(channels: list[str]) -> tuple[dict[str, object], str]:
    results = {
        channel: {
            "accepted": False,
            "outcome_unknown": True,
            "provider_reference": f"{channel}:delivery-check:post-claim-state-unknown",
            "error_category": "post_claim_state_unknown",
        }
        for channel in channels
    }
    provider_reference = ";".join(
        f"{channel}={channel}:delivery-check:post-claim-state-unknown"
        for channel in channels
    )
    return {"channel_results": results, "all_accepted": False}, provider_reference


def _validate_existing_receipt(
    receipt: Mapping[str, object],
    *,
    envelope: Mapping[str, object],
    targets: Mapping[str, object],
    channels: list[str],
) -> dict[str, object]:
    canonical_receipt = dict(receipt)
    readback = canonical_receipt.get("readback_result")
    effect_count = canonical_receipt.get("effect_count")
    terminal_outcome = canonical_receipt.get("terminal_outcome")
    if (
        canonical_receipt.get("schema") != "IrreversibleActionReceiptV1"
        or canonical_receipt.get("action_id") != envelope.get("action_id")
        or canonical_receipt.get("action_kind") != ACTION_KIND
        or canonical_json(canonical_receipt.get("target")) != canonical_json(targets)
        or canonical_receipt.get("artifact_digest")
        != envelope.get("artifact_digest")
        or canonical_receipt.get("provider_idempotency_key")
        != envelope.get("provider_idempotency_key")
        or not isinstance(canonical_receipt.get("provider_reference"), str)
        or terminal_outcome
        not in {"failed_before_effect", "succeeded", "outcome_unknown"}
        or not isinstance(effect_count, int)
        or isinstance(effect_count, bool)
        or effect_count < 0
        or effect_count > len(channels)
        or not isinstance(readback, dict)
    ):
        raise DeliveryCheckAuthorizationError(
            "existing delivery-check receipt does not match the consumed claim"
        )
    raw_readback = cast(dict[object, object], readback)
    channel_results = raw_readback.get("channel_results")
    typed_results = (
        cast(dict[object, object], channel_results)
        if isinstance(channel_results, dict)
        else {}
    )
    if (
        set(raw_readback) != set(REQUIRED_READBACK)
        or not isinstance(raw_readback.get("all_accepted"), bool)
        or not isinstance(channel_results, dict)
        or set(typed_results) != set(channels)
        or terminal_outcome == "succeeded"
        and (
            raw_readback.get("all_accepted") is not True
            or effect_count != len(channels)
        )
        or terminal_outcome == "failed_before_effect" and effect_count != 0
        or terminal_outcome == "outcome_unknown"
        and raw_readback.get("all_accepted") is not False
    ):
        raise DeliveryCheckAuthorizationError(
            "existing delivery-check receipt readback is inconsistent"
        )
    accepted_count = 0
    unknown_count = 0
    result_references: list[str] = []
    for channel in channels:
        result = typed_results.get(channel)
        if not isinstance(result, dict):
            raise DeliveryCheckAuthorizationError(
                "existing delivery-check receipt channel result is invalid"
            )
        typed_result = cast(dict[object, object], result)
        accepted = typed_result.get("accepted")
        outcome_unknown = typed_result.get("outcome_unknown")
        provider_reference = typed_result.get("provider_reference")
        error_category = typed_result.get("error_category")
        if (
            set(typed_result)
            != {
                "accepted",
                "outcome_unknown",
                "provider_reference",
                "error_category",
            }
            or not isinstance(accepted, bool)
            or not isinstance(outcome_unknown, bool)
            or accepted
            and outcome_unknown
            or provider_reference is not None
            and not isinstance(provider_reference, str)
            or error_category is not None
            and not isinstance(error_category, str)
        ):
            raise DeliveryCheckAuthorizationError(
                "existing delivery-check receipt channel result is invalid"
            )
        accepted_count += int(accepted)
        unknown_count += int(outcome_unknown)
        result_references.append(
            f"{channel}={provider_reference or error_category or 'none'}"
        )
    if (
        terminal_outcome == "succeeded"
        and accepted_count != len(channels)
        or terminal_outcome == "failed_before_effect"
        and (accepted_count != 0 or unknown_count != 0)
        or terminal_outcome == "outcome_unknown"
        and accepted_count + unknown_count == 0
        or effect_count != accepted_count + unknown_count
        or canonical_receipt.get("provider_reference")
        != ";".join(result_references)
    ):
        raise DeliveryCheckAuthorizationError(
            "existing delivery-check receipt outcome is inconsistent"
        )
    return canonical_receipt


def _execute_claimed_delivery_check(
    *,
    canonical_plan: dict[str, object],
    targets: dict[str, object],
    channels: list[str],
    envelope: dict[str, object],
    provider_idempotency_key: str,
    state_dir: Path,
    claim_envelope: Callable[[dict[str, object], Path], Path],
    emit_receipt: Callable[..., Path],
) -> dict[str, object]:
    action_id = cast(str, envelope["action_id"])
    try:
        plan_artifact_path = persist_plan_artifact(
            canonical_plan,
            action_id=action_id,
            state_dir=state_dir,
        )
        claim_envelope(envelope, state_dir)
    except (OSError, ValueError) as exc:
        raise DeliveryCheckAuthorizationError(str(exc)) from exc

    event_id = "delivery-check:" + hashlib.sha256(
        provider_idempotency_key.encode("utf-8")
    ).hexdigest()[:32]
    event = StoredEvent(
        event_id=event_id,
        source="codex",
        producer="notification-hub-delivery-check",
        level="normal",
        classified_level="normal",
        title="Notification Hub delivery check",
        body="Explicit operator-authorized delivery verification.",
        project="notification-hub",
        required_destinations=channels,
    )
    results: dict[str, object] = {}
    accepted_count = 0
    unknown_count = 0
    for channel in channels:
        try:
            result = (
                send_slack_with_result(event)
                if channel == "slack"
                else send_push_with_result(event)
            )
        except Exception as exc:  # noqa: BLE001 - consumed authority needs terminal evidence
            result = ChannelDeliveryResult(
                False,
                receipt=f"{channel}:delivery-check:exception_unknown",
                error_category=type(exc).__name__,
                outcome_unknown=True,
            )
        results[channel] = _delivery_result(result)
        if result.accepted:
            accepted_count += 1
        if result.outcome_unknown:
            unknown_count += 1
            break
    for channel in channels:
        results.setdefault(
            channel,
            {
                "accepted": False,
                "outcome_unknown": False,
                "provider_reference": None,
                "error_category": "not_attempted_after_outcome_unknown",
            },
        )
    all_accepted = accepted_count == len(channels)
    terminal_outcome = (
        "succeeded"
        if all_accepted
        else "failed_before_effect"
        if accepted_count == 0 and unknown_count == 0
        else "outcome_unknown"
    )
    readback = {
        "channel_results": results,
        "all_accepted": all_accepted,
    }
    provider_reference = ";".join(
        f"{channel}="
        f"{cast(dict[str, object], results[channel]).get('provider_reference') or cast(dict[str, object], results[channel]).get('error_category') or 'none'}"
        for channel in channels
    )
    try:
        receipt_path = emit_receipt(
            envelope,
            receipt_dir=state_dir,
            target=targets,
            provider_reference=provider_reference,
            readback_result=readback,
            terminal_outcome=terminal_outcome,
            effect_count=accepted_count + unknown_count,
        )
    except (OSError, ValueError) as exc:
        raise DeliveryCheckAuthorizationError(
            f"delivery-check receipt emission failed after claim: {exc}"
        ) from exc
    decoded_receipt = load_readback_file(receipt_path)
    receipt = _validate_existing_receipt(
        decoded_receipt,
        envelope=envelope,
        targets=targets,
        channels=channels,
    )
    return {
        "status": "ok" if all_accepted else "degraded",
        "plan": canonical_plan,
        "verify_slack": "slack" in channels,
        "verify_push": "push" in channels,
        "slack_ok": cast(dict[str, object], results.get("slack", {})).get(
            "accepted"
        )
        if "slack" in channels
        else None,
        "push_ok": cast(dict[str, object], results.get("push", {})).get(
            "accepted"
        )
        if "push" in channels
        else None,
        "event_id": event_id,
        "error": None if all_accepted else "delivery check did not fully succeed",
        "terminal_outcome": terminal_outcome,
        "authority_receipt": receipt,
        "authority_receipt_path": str(receipt_path),
        "plan_artifact_path": str(plan_artifact_path),
        "channel_results": results,
    }


def apply_delivery_check_plan(
    plan: Mapping[str, object],
    *,
    envelope_path: Path,
    claim_state_dir: Path,
    envelope_module_path: Path | None = None,
) -> dict[str, object]:
    """Consume exact authority, invoke bounded transports once, and emit a receipt."""
    canonical_plan, targets, channels, plan_digest = _validate_plan(plan)
    if not live_smoke_authorized():
        raise DeliveryCheckAuthorizationError(
            "live delivery check requires product live-smoke policy approval"
        )
    try:
        load_envelope, claim_envelope, emit_receipt = load_envelope_functions(
            envelope_module_path
        )
        envelope = load_envelope(
            envelope_path,
            expected_action_kind=ACTION_KIND,
            expected_targets=targets,
            expected_source_revision=SOURCE_REVISION,
            expected_artifact_digest=plan_digest,
        )
    except (OSError, ValueError) as exc:
        raise DeliveryCheckAuthorizationError(str(exc)) from exc
    provider_idempotency_key = _validate_exact_authority(
        envelope,
        channels=channels,
        plan_digest=plan_digest,
    )
    action_id = cast(str, envelope["action_id"])
    expected_state_dir = targets.get("receipt_state_dir")
    supplied_state_dir = str(Path(os.path.abspath(claim_state_dir)))
    if expected_state_dir != supplied_state_dir:
        raise DeliveryCheckAuthorizationError(
            "delivery-check claim state directory does not match the plan"
        )
    try:
        state_dir = private_authority_directory(claim_state_dir, create=True)
        if str(state_dir) != expected_state_dir:
            raise DeliveryCheckAuthorizationError(
                "delivery-check claim state directory does not match the plan"
            )
        with action_execution_lock(state_dir, action_id, exclusive=True):
            return _execute_claimed_delivery_check(
                canonical_plan=canonical_plan,
                targets=targets,
                channels=channels,
                envelope=envelope,
                provider_idempotency_key=provider_idempotency_key,
                state_dir=state_dir,
                claim_envelope=claim_envelope,
                emit_receipt=emit_receipt,
            )
    except (OSError, ValueError) as exc:
        raise DeliveryCheckAuthorizationError(str(exc)) from exc


def finalize_delivery_check_claim(
    plan: Mapping[str, object],
    *,
    envelope_path: Path,
    claim_state_dir: Path,
    envelope_module_path: Path | None = None,
) -> dict[str, object]:
    """Append outcome_unknown evidence for a consumed claim without transport replay."""
    canonical_plan, targets, channels, plan_digest = _validate_plan(plan)
    raw_envelope = load_readback_file(envelope_path)
    action_id = raw_envelope.get("action_id")
    if not isinstance(action_id, str) or not ACTION_ID_RE.fullmatch(action_id):
        raise DeliveryCheckAuthorizationError("envelope action_id is invalid")
    expected_state_dir = targets.get("receipt_state_dir")
    supplied_state_dir = str(Path(os.path.abspath(claim_state_dir)))
    if expected_state_dir != supplied_state_dir:
        raise DeliveryCheckAuthorizationError(
            "delivery-check claim state directory does not match the plan"
        )
    try:
        state_dir = private_authority_directory(claim_state_dir, create=False)
        if str(state_dir) != expected_state_dir:
            raise DeliveryCheckAuthorizationError(
                "delivery-check claim state directory does not match the plan"
            )
        with action_execution_lock(state_dir, action_id, exclusive=False):
            persisted_plan = load_readback_file(
                state_dir / f"{action_id}.plan.json"
            )
            if canonical_json(persisted_plan) != canonical_json(canonical_plan):
                raise DeliveryCheckAuthorizationError(
                    "persisted delivery-check plan does not match the requested plan"
                )
            claim = load_readback_file(state_dir / f"{action_id}.claim.json")
            if set(claim) != {
                "schema",
                "action_id",
                "action_kind",
                "envelope_digest",
                "claimed_at",
            } or (
                claim.get("schema") != "IrreversibleActionClaimV1"
                or claim.get("action_id") != action_id
                or claim.get("action_kind") != ACTION_KIND
                or claim.get("envelope_digest") != sha256_json(raw_envelope)
            ):
                raise DeliveryCheckAuthorizationError(
                    "claim does not bind the exact delivery-check envelope"
                )
            claimed_at = parse_claimed_at(claim.get("claimed_at"))
            load_envelope, _, emit_receipt = load_envelope_functions(
                envelope_module_path
            )
            envelope = load_envelope(
                envelope_path,
                expected_action_kind=ACTION_KIND,
                expected_targets=targets,
                expected_source_revision=SOURCE_REVISION,
                expected_artifact_digest=plan_digest,
                now=claimed_at,
            )
            _validate_exact_authority(
                envelope,
                channels=channels,
                plan_digest=plan_digest,
            )
            receipt_path = state_dir / f"{action_id}.receipt.json"
            if receipt_path.exists() or receipt_path.is_symlink():
                receipt = _validate_existing_receipt(
                    load_readback_file(receipt_path),
                    envelope=envelope,
                    targets=targets,
                    channels=channels,
                )
                return {
                    "status": "already_finalized",
                    "plan": canonical_plan,
                    "authority_receipt": receipt,
                    "authority_receipt_path": str(receipt_path),
                    "plan_artifact_path": str(
                        state_dir / f"{action_id}.plan.json"
                    ),
                    "terminal_outcome": receipt["terminal_outcome"],
                }
            readback, provider_reference = _unknown_readback(channels)
            receipt_path = emit_receipt(
                envelope,
                receipt_dir=state_dir,
                target=targets,
                provider_reference=provider_reference,
                readback_result=readback,
                terminal_outcome="outcome_unknown",
                effect_count=len(channels),
            )
            receipt = _validate_existing_receipt(
                load_readback_file(receipt_path),
                envelope=envelope,
                targets=targets,
                channels=channels,
            )
            return {
                "status": "finalized",
                "plan": canonical_plan,
                "authority_receipt": receipt,
                "authority_receipt_path": str(receipt_path),
                "plan_artifact_path": str(
                    state_dir / f"{action_id}.plan.json"
                ),
                "terminal_outcome": "outcome_unknown",
            }
    except (OSError, ValueError) as exc:
        if isinstance(exc, DeliveryCheckAuthorizationError):
            raise
        raise DeliveryCheckAuthorizationError(str(exc)) from exc
