"""Fresh-authority, no-send supersession for delivery-check outcome_unknown."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from notification_hub.delivery_check import (
    ACTION_KIND,
    PLAN_SCHEMA,
    build_delivery_check_plan,
)
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

SUPERSESSION_ACTION_KIND = "notification.delivery_check_receipt_supersede"
SUPERSESSION_PLAN_SCHEMA = "NotificationDeliveryCheckReceiptSupersessionPlanV1"
PROVIDER_READBACK_SCHEMA = "NotificationDeliveryCheckProviderReadbackV1"
SUPERSESSION_REQUIRED_READBACK = [
    "original_action_id",
    "channel",
    "provider_outcome",
    "provider_reference",
    "provider_observed_at",
    "provider_evidence",
    "original_authority_receipt_digest",
    "provider_readback_digest",
]
MAX_READBACK_FUTURE_SKEW = timedelta(seconds=60)


class DeliveryCheckSupersessionAuthorizationError(ValueError):
    """Raised when supersession evidence or authority is not exact."""


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _validate_original_plan(plan: Mapping[str, object]) -> tuple[dict[str, object], str]:
    canonical_plan = dict(plan)
    targets = canonical_plan.get("canonical_targets")
    if (
        canonical_plan.get("schema") != PLAN_SCHEMA
        or canonical_plan.get("action_kind") != ACTION_KIND
        or not isinstance(targets, dict)
    ):
        raise DeliveryCheckSupersessionAuthorizationError(
            "original delivery-check plan is invalid"
        )
    typed_targets = cast(dict[str, object], targets)
    channels = typed_targets.get("channels")
    if not isinstance(channels, list):
        raise DeliveryCheckSupersessionAuthorizationError(
            "supersession requires an exact single-channel original plan"
        )
    typed_channels = cast(list[object], channels)
    if len(typed_channels) != 1:
        raise DeliveryCheckSupersessionAuthorizationError(
            "supersession requires an exact single-channel original plan"
        )
    channel = typed_channels[0]
    if channel not in {"slack", "push"}:
        raise DeliveryCheckSupersessionAuthorizationError(
            "original delivery-check channel is invalid"
        )
    receipt_state_dir = typed_targets.get("receipt_state_dir")
    if not isinstance(receipt_state_dir, str):
        raise DeliveryCheckSupersessionAuthorizationError(
            "original delivery-check plan has no bound receipt state directory"
        )
    expected = build_delivery_check_plan(
        verify_slack=channel == "slack",
        verify_push=channel == "push",
        receipt_state_dir=Path(receipt_state_dir),
    )
    if canonical_json(canonical_plan) != canonical_json(expected):
        raise DeliveryCheckSupersessionAuthorizationError(
            "original delivery-check plan digest is invalid"
        )
    return canonical_plan, cast(str, channel)


def _load_original_unknown(
    *,
    original_plan_path: Path,
    original_receipt_path: Path,
) -> tuple[dict[str, object], dict[str, object], str, str]:
    canonical_plan, channel = _validate_original_plan(
        load_readback_file(original_plan_path)
    )
    receipt = load_readback_file(original_receipt_path)
    action_id = receipt.get("action_id")
    readback = receipt.get("readback_result")
    targets = cast(dict[str, object], canonical_plan["canonical_targets"])
    if (
        not isinstance(action_id, str)
        or not ACTION_ID_RE.fullmatch(action_id)
        or receipt.get("schema") != "IrreversibleActionReceiptV1"
        or receipt.get("action_kind") != ACTION_KIND
        or canonical_json(receipt.get("target")) != canonical_json(targets)
        or receipt.get("artifact_digest") != canonical_plan.get("plan_digest")
        or not isinstance(receipt.get("provider_idempotency_key"), str)
        or not isinstance(receipt.get("provider_reference"), str)
        or receipt.get("terminal_outcome") != "outcome_unknown"
        or receipt.get("effect_count") != 1
        or not isinstance(readback, dict)
        or original_plan_path.parent.resolve(strict=True)
        != original_receipt_path.parent.resolve(strict=True)
        or original_plan_path.name != f"{action_id}.plan.json"
        or original_receipt_path.name != f"{action_id}.receipt.json"
        or targets.get("receipt_state_dir")
        != str(original_receipt_path.parent.resolve(strict=True))
    ):
        raise DeliveryCheckSupersessionAuthorizationError(
            "original authority receipt is not an exact single-channel outcome_unknown"
        )
    typed_readback = cast(dict[str, object], readback)
    if typed_readback.get("all_accepted") is not False:
        raise DeliveryCheckSupersessionAuthorizationError(
            "original authority receipt is not an exact single-channel outcome_unknown"
        )
    channel_results = typed_readback.get("channel_results")
    if not isinstance(channel_results, dict):
        raise DeliveryCheckSupersessionAuthorizationError(
            "original authority receipt channel readback is invalid"
        )
    typed_channel_results = cast(dict[str, object], channel_results)
    if set(typed_channel_results) != {channel}:
        raise DeliveryCheckSupersessionAuthorizationError(
            "original authority receipt channel readback is invalid"
        )
    channel_result = typed_channel_results.get(channel)
    if (
        not isinstance(channel_result, dict)
    ):
        raise DeliveryCheckSupersessionAuthorizationError(
            "original authority receipt is not unresolved"
        )
    typed_channel_result = cast(dict[str, object], channel_result)
    if (
        typed_channel_result.get("accepted") is not False
        or typed_channel_result.get("outcome_unknown") is not True
        or not isinstance(typed_channel_result.get("provider_reference"), str)
    ):
        raise DeliveryCheckSupersessionAuthorizationError(
            "original authority receipt is not unresolved"
        )
    return canonical_plan, receipt, action_id, channel


def _load_provider_readback(
    path: Path,
    *,
    original_action_id: str,
    channel: str,
) -> dict[str, object]:
    readback = load_readback_file(path)
    if set(readback) != {
        "schema",
        "original_action_id",
        "channel",
        "provider_outcome",
        "provider_reference",
        "observed_at",
        "evidence",
    }:
        raise DeliveryCheckSupersessionAuthorizationError(
            "provider readback fields mismatch"
        )
    provider_outcome = readback.get("provider_outcome")
    provider_reference = readback.get("provider_reference")
    evidence = readback.get("evidence")
    observed_at_value = readback.get("observed_at")
    if (
        readback.get("schema") != PROVIDER_READBACK_SCHEMA
        or readback.get("original_action_id") != original_action_id
        or readback.get("channel") != channel
        or provider_outcome not in {"accepted", "absent"}
        or not isinstance(provider_reference, str)
        or not provider_reference.strip()
        or not isinstance(evidence, dict)
        or not evidence
        or not isinstance(observed_at_value, str)
    ):
        raise DeliveryCheckSupersessionAuthorizationError(
            "provider readback is invalid"
        )
    try:
        observed_at = datetime.fromisoformat(
            observed_at_value.replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise DeliveryCheckSupersessionAuthorizationError(
            "provider readback observed_at is invalid"
        ) from exc
    if (
        observed_at.tzinfo is None
        or observed_at.astimezone(UTC)
        > datetime.now(UTC) + MAX_READBACK_FUTURE_SKEW
    ):
        raise DeliveryCheckSupersessionAuthorizationError(
            "provider readback observed_at is invalid"
        )
    return readback


def build_delivery_check_receipt_supersession_plan(
    *,
    original_plan_path: Path,
    original_receipt_path: Path,
    provider_readback_path: Path,
    receipt_state_dir: Path,
) -> dict[str, object]:
    """Build a deterministic no-send supersession plan from immutable evidence."""
    canonical_plan, original_receipt, action_id, channel = _load_original_unknown(
        original_plan_path=original_plan_path,
        original_receipt_path=original_receipt_path,
    )
    provider_readback = _load_provider_readback(
        provider_readback_path,
        original_action_id=action_id,
        channel=channel,
    )
    original_receipt_path = original_receipt_path.resolve(strict=True)
    original_plan_path = original_plan_path.resolve(strict=True)
    provider_readback_path = provider_readback_path.resolve(strict=True)
    original_receipt_digest = sha256_json(original_receipt)
    provider_readback_digest = sha256_json(provider_readback)
    targets: dict[str, object] = {
        "original_action_id": action_id,
        "original_plan_path": str(original_plan_path),
        "original_plan_digest": cast(str, canonical_plan["plan_digest"]),
        "original_authority_receipt_path": str(original_receipt_path),
        "original_authority_receipt_digest": original_receipt_digest,
        "provider_readback_path": str(provider_readback_path),
        "provider_readback_digest": provider_readback_digest,
        "channel": channel,
        "provider_outcome": provider_readback["provider_outcome"],
        "provider_reference": provider_readback["provider_reference"],
        "receipt_state_dir": str(_absolute_lexical(receipt_state_dir)),
    }
    readback_result: dict[str, object] = {
        "original_action_id": action_id,
        "channel": channel,
        "provider_outcome": provider_readback["provider_outcome"],
        "provider_reference": provider_readback["provider_reference"],
        "provider_observed_at": provider_readback["observed_at"],
        "provider_evidence": provider_readback["evidence"],
        "original_authority_receipt_digest": original_receipt_digest,
        "provider_readback_digest": provider_readback_digest,
    }
    body: dict[str, object] = {
        "schema": SUPERSESSION_PLAN_SCHEMA,
        "action_kind": SUPERSESSION_ACTION_KIND,
        "canonical_targets": targets,
        "source_revision": original_receipt_digest,
        "readback_result": readback_result,
        "required_readback": SUPERSESSION_REQUIRED_READBACK,
    }
    return {**body, "plan_digest": sha256_json(body)}


def _validate_plan(
    plan: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, object], dict[str, object], str]:
    canonical_plan = dict(plan)
    plan_digest = canonical_plan.get("plan_digest")
    body = {key: value for key, value in canonical_plan.items() if key != "plan_digest"}
    targets = canonical_plan.get("canonical_targets")
    readback = canonical_plan.get("readback_result")
    if (
        canonical_plan.get("schema") != SUPERSESSION_PLAN_SCHEMA
        or canonical_plan.get("action_kind") != SUPERSESSION_ACTION_KIND
        or plan_digest != sha256_json(body)
        or not isinstance(targets, dict)
        or not isinstance(readback, dict)
        or canonical_plan.get("required_readback") != SUPERSESSION_REQUIRED_READBACK
    ):
        raise DeliveryCheckSupersessionAuthorizationError(
            "delivery-check supersession plan is invalid"
        )
    return (
        canonical_plan,
        cast(dict[str, object], targets),
        cast(dict[str, object], readback),
        cast(str, plan_digest),
    )


def _rebuild_current_plan(targets: Mapping[str, object]) -> dict[str, object]:
    path_fields = (
        "original_plan_path",
        "original_authority_receipt_path",
        "provider_readback_path",
        "receipt_state_dir",
    )
    if any(not isinstance(targets.get(field), str) for field in path_fields):
        raise DeliveryCheckSupersessionAuthorizationError(
            "delivery-check supersession target paths are invalid"
        )
    return build_delivery_check_receipt_supersession_plan(
        original_plan_path=Path(cast(str, targets["original_plan_path"])),
        original_receipt_path=Path(
            cast(str, targets["original_authority_receipt_path"])
        ),
        provider_readback_path=Path(cast(str, targets["provider_readback_path"])),
        receipt_state_dir=Path(cast(str, targets["receipt_state_dir"])),
    )


def _validate_exact_authority(
    envelope: Mapping[str, object],
    *,
    targets: Mapping[str, object],
    plan_digest: str,
) -> None:
    bounds = envelope.get("bounds")
    if (
        envelope.get("one_shot") is not True
        or not isinstance(bounds, dict)
        or envelope.get("preconditions")
        != {
            "original_authority_receipt_digest": targets[
                "original_authority_receipt_digest"
            ],
            "provider_readback_digest": targets["provider_readback_digest"],
        }
        or envelope.get("required_readback") != SUPERSESSION_REQUIRED_READBACK
        or not isinstance(envelope.get("provider_idempotency_key"), str)
        or envelope.get("artifact_digest") != plan_digest
    ):
        raise DeliveryCheckSupersessionAuthorizationError(
            "delivery-check supersession authority is not exact and one-shot"
        )
    if cast(dict[str, object], bounds).get("allowed_effect_count") != 1:
        raise DeliveryCheckSupersessionAuthorizationError(
            "delivery-check supersession authority is not exact and one-shot"
        )


def _emit_supersession_receipt(
    *,
    envelope: dict[str, object],
    emit_receipt: Callable[..., Path],
    state_dir: Path,
    targets: dict[str, object],
    readback: dict[str, object],
) -> Path:
    return emit_receipt(
        envelope,
        receipt_dir=state_dir,
        target=targets,
        provider_reference=cast(str, readback["provider_reference"]),
        readback_result=readback,
        terminal_outcome="succeeded",
        effect_count=1,
    )


def _validate_existing_receipt(
    receipt: Mapping[str, object],
    *,
    envelope: Mapping[str, object],
    targets: Mapping[str, object],
    readback: Mapping[str, object],
) -> dict[str, object]:
    canonical_receipt = dict(receipt)
    if (
        canonical_receipt.get("schema") != "IrreversibleActionReceiptV1"
        or canonical_receipt.get("action_id") != envelope.get("action_id")
        or canonical_receipt.get("action_kind") != SUPERSESSION_ACTION_KIND
        or canonical_json(canonical_receipt.get("target")) != canonical_json(targets)
        or canonical_receipt.get("artifact_digest")
        != envelope.get("artifact_digest")
        or canonical_receipt.get("provider_idempotency_key")
        != envelope.get("provider_idempotency_key")
        or canonical_receipt.get("provider_reference")
        != readback.get("provider_reference")
        or canonical_json(canonical_receipt.get("readback_result"))
        != canonical_json(readback)
        or canonical_receipt.get("terminal_outcome") != "succeeded"
        or canonical_receipt.get("effect_count") != 1
    ):
        raise DeliveryCheckSupersessionAuthorizationError(
            "existing supersession receipt does not match the consumed claim"
        )
    return canonical_receipt


def _resolved_terminal_outcome(readback: Mapping[str, object]) -> str:
    return (
        "succeeded"
        if readback.get("provider_outcome") == "accepted"
        else "failed_before_effect"
    )


def apply_delivery_check_receipt_supersession_plan(
    plan: Mapping[str, object],
    *,
    envelope_path: Path,
    claim_state_dir: Path,
    envelope_module_path: Path | None = None,
) -> dict[str, object]:
    """Consume fresh authority and append one no-send supersession receipt."""
    canonical_plan, targets, readback, plan_digest = _validate_plan(plan)
    if canonical_json(_rebuild_current_plan(targets)) != canonical_json(canonical_plan):
        raise DeliveryCheckSupersessionAuthorizationError(
            "delivery-check supersession evidence changed after plan rendering"
        )
    try:
        load_envelope, claim_envelope, emit_receipt = load_envelope_functions(
            envelope_module_path
        )
        envelope = load_envelope(
            envelope_path,
            expected_action_kind=SUPERSESSION_ACTION_KIND,
            expected_targets=targets,
            expected_source_revision=cast(
                str, targets["original_authority_receipt_digest"]
            ),
            expected_artifact_digest=plan_digest,
        )
    except (OSError, ValueError) as exc:
        raise DeliveryCheckSupersessionAuthorizationError(str(exc)) from exc
    action_id = envelope.get("action_id")
    if not isinstance(action_id, str) or not ACTION_ID_RE.fullmatch(action_id):
        raise DeliveryCheckSupersessionAuthorizationError(
            "supersession action_id is invalid"
        )
    _validate_exact_authority(envelope, targets=targets, plan_digest=plan_digest)
    try:
        state_dir = private_authority_directory(claim_state_dir, create=True)
        if str(state_dir) != targets["receipt_state_dir"]:
            raise DeliveryCheckSupersessionAuthorizationError(
                "supersession claim directory does not match the plan"
            )
        with action_execution_lock(state_dir, action_id, exclusive=True):
            plan_artifact_path = persist_plan_artifact(
                canonical_plan,
                action_id=action_id,
                state_dir=state_dir,
            )
            claim_envelope(envelope, state_dir)
            receipt_path = _emit_supersession_receipt(
                envelope=envelope,
                emit_receipt=emit_receipt,
                state_dir=state_dir,
                targets=targets,
                readback=readback,
            )
            receipt = _validate_existing_receipt(
                load_readback_file(receipt_path),
                envelope=envelope,
                targets=targets,
                readback=readback,
            )
    except (OSError, ValueError) as exc:
        if isinstance(exc, DeliveryCheckSupersessionAuthorizationError):
            raise
        raise DeliveryCheckSupersessionAuthorizationError(str(exc)) from exc
    return {
        "status": "superseded",
        "plan": canonical_plan,
        "authority_receipt": receipt,
        "authority_receipt_path": str(receipt_path),
        "plan_artifact_path": str(plan_artifact_path),
        "terminal_outcome": "succeeded",
        "resolved_terminal_outcome": _resolved_terminal_outcome(readback),
        "supersedes_action_id": targets["original_action_id"],
    }


def finalize_delivery_check_receipt_supersession_claim(
    plan: Mapping[str, object],
    *,
    envelope_path: Path,
    claim_state_dir: Path,
    envelope_module_path: Path | None = None,
) -> dict[str, object]:
    """Finalize only the missing supersession receipt; never replay provider work."""
    canonical_plan, targets, readback, plan_digest = _validate_plan(plan)
    if canonical_json(_rebuild_current_plan(targets)) != canonical_json(canonical_plan):
        raise DeliveryCheckSupersessionAuthorizationError(
            "delivery-check supersession evidence changed after plan rendering"
        )
    raw_envelope = load_readback_file(envelope_path)
    action_id = raw_envelope.get("action_id")
    if not isinstance(action_id, str) or not ACTION_ID_RE.fullmatch(action_id):
        raise DeliveryCheckSupersessionAuthorizationError(
            "supersession action_id is invalid"
        )
    try:
        state_dir = private_authority_directory(claim_state_dir, create=False)
        if str(state_dir) != targets["receipt_state_dir"]:
            raise DeliveryCheckSupersessionAuthorizationError(
                "supersession claim directory does not match the plan"
            )
        with action_execution_lock(state_dir, action_id, exclusive=False):
            persisted_plan = load_readback_file(
                state_dir / f"{action_id}.plan.json"
            )
            if canonical_json(persisted_plan) != canonical_json(canonical_plan):
                raise DeliveryCheckSupersessionAuthorizationError(
                    "persisted supersession plan does not match the requested plan"
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
                or claim.get("action_kind") != SUPERSESSION_ACTION_KIND
                or claim.get("envelope_digest") != sha256_json(raw_envelope)
            ):
                raise DeliveryCheckSupersessionAuthorizationError(
                    "claim does not bind the exact supersession envelope"
                )
            claimed_at = parse_claimed_at(claim.get("claimed_at"))
            load_envelope, _, emit_receipt = load_envelope_functions(
                envelope_module_path
            )
            envelope = load_envelope(
                envelope_path,
                expected_action_kind=SUPERSESSION_ACTION_KIND,
                expected_targets=targets,
                expected_source_revision=cast(
                    str, targets["original_authority_receipt_digest"]
                ),
                expected_artifact_digest=plan_digest,
                now=claimed_at,
            )
            _validate_exact_authority(
                envelope,
                targets=targets,
                plan_digest=plan_digest,
            )
            receipt_path = state_dir / f"{action_id}.receipt.json"
            if receipt_path.exists() or receipt_path.is_symlink():
                receipt = _validate_existing_receipt(
                    load_readback_file(receipt_path),
                    envelope=envelope,
                    targets=targets,
                    readback=readback,
                )
                status = "already_finalized"
            else:
                receipt_path = _emit_supersession_receipt(
                    envelope=envelope,
                    emit_receipt=emit_receipt,
                    state_dir=state_dir,
                    targets=targets,
                    readback=readback,
                )
                receipt = _validate_existing_receipt(
                    load_readback_file(receipt_path),
                    envelope=envelope,
                    targets=targets,
                    readback=readback,
                )
                status = "finalized"
    except (OSError, ValueError) as exc:
        if isinstance(exc, DeliveryCheckSupersessionAuthorizationError):
            raise
        raise DeliveryCheckSupersessionAuthorizationError(str(exc)) from exc
    return {
        "status": status,
        "plan": canonical_plan,
        "authority_receipt": receipt,
        "authority_receipt_path": str(receipt_path),
        "plan_artifact_path": str(state_dir / f"{action_id}.plan.json"),
        "terminal_outcome": "succeeded",
        "resolved_terminal_outcome": _resolved_terminal_outcome(readback),
        "supersedes_action_id": targets["original_action_id"],
    }
