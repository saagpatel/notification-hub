"""Dry-run planning and envelope-gated reconciliation for ambiguous delivery outcomes."""

from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import stat
import tempfile
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

from notification_hub.durable_inbox import (
    read_channel_reconciliation_state,
    read_unknown_delivery_context,
    record_channel_reconciliation,
)

ACTION_KIND = "notification.channel_reconcile"
PLAN_SCHEMA = "ChannelReconciliationPlanV2"
REQUIRED_READBACK = ["event_id", "channel", "provider_outcome", "provider_reference"]
SUPERSESSION_ACTION_KIND = "notification.reconciliation_receipt_supersede"
SUPERSESSION_PLAN_SCHEMA = "ReconciliationReceiptSupersessionPlanV1"
SUPERSESSION_REQUIRED_READBACK = [
    *REQUIRED_READBACK,
    "database_event_status",
    "original_authority_receipt_digest",
]
EXECUTION_LOCK_IDENTITY_SCHEMA = "ReconciliationExecutionLockIdentityV1"
ACTION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
MAX_CLAIM_FUTURE_SKEW = timedelta(seconds=60)
DEFAULT_ENVELOPE_MODULE = (
    Path.home() / ".codex" / "scripts" / "security" / "irreversible_action_envelope.py"
)


class ReconciliationAuthorizationError(ValueError):
    """Raised when a reconciliation plan lacks exact one-shot authority."""


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_json(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_mapping(value: Mapping[str, object], *, label: str) -> dict[str, object]:
    decoded = cast(object, json.loads(canonical_json(dict(value))))
    if not isinstance(decoded, dict):
        raise ValueError(f"{label} must be an object")
    raw = cast(dict[object, object], decoded)
    if not all(isinstance(key, str) for key in raw):
        raise ValueError(f"{label} contains a non-string key")
    return {cast(str, key): item for key, item in raw.items()}


def load_readback_file(path: Path) -> dict[str, object]:
    """Load owner-private readback evidence without following a symlink."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ValueError("readback evidence must be a regular non-symlink file") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("readback evidence must be a regular non-symlink file")
        if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
            raise ValueError("readback evidence must be owner-private")
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            descriptor = -1
            decoded = cast(object, json.load(handle))
    except json.JSONDecodeError as exc:
        raise ValueError("readback evidence is invalid JSON") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(decoded, dict):
        raise ValueError("readback evidence must be a JSON object")
    raw = cast(dict[object, object], decoded)
    if not all(isinstance(key, str) for key in raw):
        raise ValueError("readback evidence contains a non-string key")
    return {cast(str, key): item for key, item in raw.items()}


def build_reconciliation_plan(
    event_id: str,
    channel: str,
    *,
    terminal_outcome: str,
    provider_reference: str,
    readback_result: Mapping[str, object],
    database_path: Path,
    receipt_state_dir: Path | None = None,
) -> dict[str, object]:
    """Render a canonical reconciliation plan using a read-only SQLite connection."""
    if terminal_outcome not in {"reconciled_succeeded", "reconciled_absent"}:
        raise ValueError("unsupported reconciliation terminal outcome")
    if not event_id.strip() or event_id != event_id.strip():
        raise ValueError("event_id must be a non-empty exact value")
    if not channel.strip() or channel != channel.strip():
        raise ValueError("channel must be a non-empty exact value")
    if not provider_reference.strip() or provider_reference != provider_reference.strip():
        raise ValueError("provider_reference must be a non-empty exact value")

    context = read_unknown_delivery_context(event_id, channel, path=database_path)
    if context["event_status"] != "reconciliation_required":
        raise ValueError("event is not awaiting reconciliation")
    readback = _canonical_mapping(readback_result, label="readback_result")
    expected_provider_outcome = (
        "accepted" if terminal_outcome == "reconciled_succeeded" else "absent"
    )
    required_values = {
        "event_id": event_id,
        "channel": channel,
        "provider_outcome": expected_provider_outcome,
        "provider_reference": provider_reference,
    }
    for key, expected in required_values.items():
        if readback.get(key) != expected:
            raise ValueError(f"readback_result {key} does not match reconciliation target")

    canonical_database_path = Path(cast(str, context["database_path"]))
    bound_receipt_state_dir = Path(
        os.path.abspath(
            receipt_state_dir
            if receipt_state_dir is not None
            else canonical_database_path.parent / "claims"
        )
    )
    targets = {
        "database_path": context["database_path"],
        "event_id": event_id,
        "channel": channel,
        "unknown_evidence_digest": context["unknown_evidence_digest"],
        "original_provider_reference": context["original_provider_reference"],
        "terminal_outcome": terminal_outcome,
        "provider_reference": provider_reference,
        "receipt_state_dir": str(bound_receipt_state_dir),
    }
    body: dict[str, object] = {
        "schema": PLAN_SCHEMA,
        "action_kind": ACTION_KIND,
        "canonical_targets": targets,
        "source_revision": context["event_payload_digest"],
        "readback_result": readback,
        "readback_digest": sha256_json(readback),
        "required_readback": REQUIRED_READBACK,
        "effect_count": 1,
    }
    return {**body, "plan_digest": sha256_json(body)}


def _load_envelope_functions(
    module_path: Path | None,
) -> tuple[
    Callable[..., dict[str, object]],
    Callable[[dict[str, object], Path], Path],
    Callable[..., Path],
]:
    path = (module_path or DEFAULT_ENVELOPE_MODULE).expanduser().resolve(strict=True)
    spec = importlib.util.spec_from_file_location(
        "notification_hub_irreversible_action_envelope",
        path,
    )
    if spec is None or spec.loader is None:
        raise ReconciliationAuthorizationError("shared envelope adapter is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    load_envelope = cast(
        Callable[..., dict[str, object]],
        getattr(module, "load_envelope", None),
    )
    claim_envelope = cast(
        Callable[[dict[str, object], Path], Path],
        getattr(module, "claim_envelope", None),
    )
    emit_receipt = cast(
        Callable[..., Path],
        getattr(module, "emit_receipt", None),
    )
    if (
        not callable(load_envelope)
        or not callable(claim_envelope)
        or not callable(emit_receipt)
    ):
        raise ReconciliationAuthorizationError("shared envelope adapter is incomplete")
    return load_envelope, claim_envelope, emit_receipt


def load_envelope_functions(
    module_path: Path | None,
) -> tuple[
    Callable[..., dict[str, object]],
    Callable[[dict[str, object], Path], Path],
    Callable[..., Path],
]:
    """Load the shared irreversible-action adapter for product-local sinks."""
    return _load_envelope_functions(module_path)


def _classify_post_claim_state(
    *,
    target_strings: Mapping[str, str],
    canonical_readback: Mapping[str, object],
    action_id: str,
    plan_digest: str,
    provider_idempotency_key: str,
) -> tuple[
    Literal[
        "failed_before_effect",
        "outcome_unknown",
        "reconciled_succeeded",
        "reconciled_absent",
    ],
    int,
    dict[str, object],
    dict[str, object] | None,
]:
    readback = dict(canonical_readback)
    try:
        state = read_channel_reconciliation_state(
            target_strings["event_id"],
            target_strings["channel"],
            path=Path(target_strings["database_path"]),
        )
        event_status = state.get("event_status")
        stored_receipt = state.get("receipt")
        readback["database_event_status"] = event_status
        if isinstance(stored_receipt, dict):
            canonical_receipt = _canonical_mapping(
                cast(dict[str, object], stored_receipt),
                label="stored reconciliation receipt",
            )
            readback["database_receipt_digest"] = sha256_json(canonical_receipt)
            exact_receipt = (
                canonical_receipt.get("action_id") == action_id
                and canonical_receipt.get("target")
                == {
                    "event_id": target_strings["event_id"],
                    "channel": target_strings["channel"],
                }
                and canonical_receipt.get("original_unknown_evidence_digest")
                == target_strings["unknown_evidence_digest"]
                and canonical_receipt.get("original_provider_reference")
                == target_strings["original_provider_reference"]
                and canonical_receipt.get("terminal_outcome")
                == target_strings["terminal_outcome"]
                and canonical_receipt.get("provider_reference")
                == target_strings["provider_reference"]
                and canonical_receipt.get("readback_result") == canonical_readback
                and canonical_receipt.get("artifact_digest") == plan_digest
                and canonical_receipt.get("provider_idempotency_key")
                == provider_idempotency_key
            )
            if exact_receipt and event_status == target_strings["terminal_outcome"]:
                return (
                    cast(
                        Literal["reconciled_succeeded", "reconciled_absent"],
                        event_status,
                    ),
                    1,
                    readback,
                    canonical_receipt,
                )
        elif event_status == "reconciliation_required":
            context = read_unknown_delivery_context(
                target_strings["event_id"],
                target_strings["channel"],
                path=Path(target_strings["database_path"]),
            )
            if (
                context.get("unknown_evidence_digest")
                == target_strings["unknown_evidence_digest"]
                and context.get("original_provider_reference")
                == target_strings["original_provider_reference"]
            ):
                return "failed_before_effect", 0, readback, None
    except (OSError, sqlite3.Error, ValueError, KeyError) as exc:
        readback["database_readback_error"] = f"{exc.__class__.__name__}: {exc}"
    return "outcome_unknown", 1, readback, None


def _private_directory(path: Path, *, create: bool) -> Path:
    absolute = Path(os.path.abspath(path))
    if create:
        absolute.mkdir(parents=True, mode=0o700, exist_ok=True)
    metadata = absolute.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ReconciliationAuthorizationError(
            "claim state path must be a non-symlink directory"
        )
    if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
        raise ReconciliationAuthorizationError("claim state path must be owner-private")
    return absolute.resolve(strict=True)


def _bind_execution_lock_identity(
    state_dir: Path,
    action_id: str,
    metadata: os.stat_result,
) -> Path:
    """Pin one action ID to the first validated execution-lock inode."""
    identity_path = state_dir / f"{action_id}.execution.lock.identity.json"
    expected = {
        "schema": EXECUTION_LOCK_IDENTITY_SCHEMA,
        "action_id": action_id,
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
    }
    if identity_path.exists() or identity_path.is_symlink():
        try:
            existing = load_readback_file(identity_path)
        except ValueError as exc:
            raise ReconciliationAuthorizationError(
                "execution lock identity must be owner-private immutable evidence"
            ) from exc
        if canonical_json(existing) != canonical_json(expected):
            raise ReconciliationAuthorizationError(
                "execution lock identity does not match the pinned inode"
            )
        return identity_path
    encoded = canonical_json(expected) + "\n"
    descriptor, temporary = tempfile.mkstemp(
        prefix=".execution-lock-identity-",
        dir=state_dir,
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, identity_path)
        except FileExistsError:
            pass
        else:
            directory_fd = os.open(state_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    try:
        existing = load_readback_file(identity_path)
    except ValueError as exc:
        raise ReconciliationAuthorizationError(
            "execution lock identity must be owner-private immutable evidence"
        ) from exc
    if canonical_json(existing) != canonical_json(expected):
        raise ReconciliationAuthorizationError(
            "execution lock identity does not match the pinned inode"
        )
    return identity_path


@contextmanager
def _action_execution_lock(
    state_dir: Path,
    action_id: str,
    *,
    exclusive: bool,
) -> Generator[Path]:
    """Hold an owner-private process lock for one action without waiting."""
    lock_path = state_dir / f"{action_id}.execution.lock"
    identity_path = state_dir / f"{action_id}.execution.lock.identity.json"
    durable_paths = (
        state_dir / f"{action_id}.plan.json",
        state_dir / f"{action_id}.claim.json",
        state_dir / f"{action_id}.receipt.json",
    )
    if (
        not identity_path.exists()
        and not identity_path.is_symlink()
        and any(path.exists() or path.is_symlink() for path in durable_paths)
    ):
        raise ReconciliationAuthorizationError(
            "execution lock identity is missing for durable action state"
        )
    try:
        descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
    except OSError as exc:
        raise ReconciliationAuthorizationError(
            "execution lock must be a regular non-symlink file"
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o077
        ):
            raise ReconciliationAuthorizationError(
                "execution lock must be owner-private"
            )
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        try:
            fcntl.flock(descriptor, operation | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ReconciliationAuthorizationError(
                "irreversible action is still executing"
            ) from exc
        _bind_execution_lock_identity(state_dir, action_id, metadata)
        try:
            yield lock_path
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _persist_plan_artifact(
    plan: Mapping[str, object],
    *,
    action_id: str,
    state_dir: Path,
) -> Path:
    plan_path = state_dir / f"{action_id}.plan.json"
    encoded = canonical_json(plan) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=".plan-", dir=state_dir)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, plan_path)
        except FileExistsError:
            existing = load_readback_file(plan_path)
            if canonical_json(existing) != canonical_json(plan):
                raise ReconciliationAuthorizationError(
                    "claim state already contains a different plan artifact"
                )
            return plan_path
        directory_fd = os.open(state_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return plan_path


def _parse_claimed_at(value: object) -> datetime:
    if not isinstance(value, str):
        raise ReconciliationAuthorizationError("claim claimed_at must be an ISO-8601 string")
    try:
        claimed_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReconciliationAuthorizationError("claim claimed_at is invalid") from exc
    if claimed_at.tzinfo is None:
        raise ReconciliationAuthorizationError("claim claimed_at must include a timezone")
    claimed_at = claimed_at.astimezone(UTC)
    if claimed_at > datetime.now(UTC) + MAX_CLAIM_FUTURE_SKEW:
        raise ReconciliationAuthorizationError("claim claimed_at is too far in the future")
    return claimed_at


def private_authority_directory(path: Path, *, create: bool) -> Path:
    """Validate one owner-private authority directory without widening access."""
    return _private_directory(path, create=create)


@contextmanager
def action_execution_lock(
    state_dir: Path,
    action_id: str,
    *,
    exclusive: bool,
) -> Generator[Path]:
    """Hold the shared per-action process lock used by irreversible sinks."""
    with _action_execution_lock(state_dir, action_id, exclusive=exclusive) as lock_path:
        yield lock_path


def persist_plan_artifact(
    plan: Mapping[str, object],
    *,
    action_id: str,
    state_dir: Path,
) -> Path:
    """Atomically persist an exact plan before one-shot claim consumption."""
    return _persist_plan_artifact(plan, action_id=action_id, state_dir=state_dir)


def parse_claimed_at(value: object) -> datetime:
    """Validate a claim timestamp for claim-time envelope readback."""
    return _parse_claimed_at(value)


def _validate_existing_authority_receipt(
    receipt: Mapping[str, object],
    *,
    envelope: Mapping[str, object],
    canonical_targets: Mapping[str, object],
    canonical_readback: Mapping[str, object],
) -> dict[str, object]:
    canonical_receipt = _canonical_mapping(receipt, label="authority receipt")
    if (
        canonical_receipt.get("schema") != "IrreversibleActionReceiptV1"
        or canonical_receipt.get("action_id") != envelope.get("action_id")
        or canonical_receipt.get("action_kind") != ACTION_KIND
        or canonical_receipt.get("target") != canonical_targets
        or canonical_receipt.get("artifact_digest") != envelope.get("artifact_digest")
        or canonical_receipt.get("provider_idempotency_key")
        != envelope.get("provider_idempotency_key")
        or canonical_receipt.get("provider_reference")
        != canonical_targets.get("provider_reference")
        or canonical_receipt.get("terminal_outcome")
        not in {
            "failed_before_effect",
            "outcome_unknown",
            "reconciled_succeeded",
            "reconciled_absent",
        }
    ):
        raise ReconciliationAuthorizationError(
            "existing authority receipt does not match the consumed claim"
        )
    effect_count = canonical_receipt.get("effect_count")
    if (
        not isinstance(effect_count, int)
        or isinstance(effect_count, bool)
        or effect_count < 0
        or effect_count > 1
    ):
        raise ReconciliationAuthorizationError(
            "existing authority receipt effect count is invalid"
        )
    receipt_readback = canonical_receipt.get("readback_result")
    if not isinstance(receipt_readback, dict):
        raise ReconciliationAuthorizationError(
            "existing authority receipt readback does not match the plan"
        )
    canonical_receipt_readback = _canonical_mapping(
        cast(dict[str, object], receipt_readback),
        label="authority receipt readback",
    )
    if any(
        canonical_receipt_readback.get(field) != canonical_readback.get(field)
        for field in REQUIRED_READBACK
    ):
        raise ReconciliationAuthorizationError(
            "existing authority receipt readback does not match the plan"
        )
    terminal_outcome = canonical_receipt["terminal_outcome"]
    expected_effect_count = 0 if terminal_outcome == "failed_before_effect" else 1
    if effect_count != expected_effect_count or (
        terminal_outcome in {"reconciled_succeeded", "reconciled_absent"}
        and terminal_outcome != canonical_targets.get("terminal_outcome")
    ):
        raise ReconciliationAuthorizationError(
            "existing authority receipt outcome is inconsistent"
        )
    return canonical_receipt


def _existing_reconciliation_finalization(
    receipt_path: Path,
    *,
    envelope: Mapping[str, object],
    canonical_targets: Mapping[str, object],
    canonical_readback: Mapping[str, object],
    canonical_plan: Mapping[str, object],
) -> dict[str, object] | None:
    if not receipt_path.exists() and not receipt_path.is_symlink():
        return None
    existing_receipt = _validate_existing_authority_receipt(
        load_readback_file(receipt_path),
        envelope=envelope,
        canonical_targets=canonical_targets,
        canonical_readback=canonical_readback,
    )
    return {
        "status": "already_finalized",
        "plan": dict(canonical_plan),
        "authority_receipt": existing_receipt,
        "authority_receipt_path": str(receipt_path),
        "terminal_outcome": existing_receipt["terminal_outcome"],
    }


def finalize_reconciliation_claim(
    plan: Mapping[str, object],
    *,
    envelope_path: Path,
    claim_state_dir: Path,
    envelope_module_path: Path | None = None,
) -> dict[str, object]:
    """Finalize one consumed claim only when no apply process is active."""
    raw_envelope = load_readback_file(envelope_path)
    action_id = raw_envelope.get("action_id")
    if not isinstance(action_id, str) or not ACTION_ID_RE.fullmatch(action_id):
        raise ReconciliationAuthorizationError("envelope action_id is invalid")
    state_dir = _private_directory(claim_state_dir, create=False)
    with _action_execution_lock(
        state_dir,
        action_id,
        exclusive=False,
    ):
        return _finalize_reconciliation_claim_locked(
            plan,
            envelope_path=envelope_path,
            claim_state_dir=state_dir,
            envelope_module_path=envelope_module_path,
        )


def _finalize_reconciliation_claim_locked(
    plan: Mapping[str, object],
    *,
    envelope_path: Path,
    claim_state_dir: Path,
    envelope_module_path: Path | None = None,
) -> dict[str, object]:
    """Finalize one consumed claim from read-only state without replaying its mutation."""
    if plan.get("action_kind") == SUPERSESSION_ACTION_KIND:
        return _finalize_reconciliation_receipt_supersession_claim_locked(
            plan,
            envelope_path=envelope_path,
            claim_state_dir=claim_state_dir,
            envelope_module_path=envelope_module_path,
        )
    canonical_plan = _canonical_mapping(plan, label="plan")
    plan_digest = canonical_plan.get("plan_digest")
    body = {key: value for key, value in canonical_plan.items() if key != "plan_digest"}
    if canonical_plan.get("schema") != PLAN_SCHEMA or plan_digest != sha256_json(body):
        raise ReconciliationAuthorizationError("reconciliation plan digest is invalid")
    if canonical_plan.get("action_kind") != ACTION_KIND:
        raise ReconciliationAuthorizationError("reconciliation action kind is invalid")
    targets = canonical_plan.get("canonical_targets")
    readback = canonical_plan.get("readback_result")
    source_revision = canonical_plan.get("source_revision")
    if (
        not isinstance(targets, dict)
        or not isinstance(readback, dict)
        or not isinstance(source_revision, str)
        or not isinstance(plan_digest, str)
    ):
        raise ReconciliationAuthorizationError("reconciliation plan is incomplete")
    canonical_targets = _canonical_mapping(
        cast(dict[str, object], targets),
        label="canonical_targets",
    )
    canonical_readback = _canonical_mapping(
        cast(dict[str, object], readback),
        label="readback_result",
    )
    target_strings: dict[str, str] = {}
    for field in (
        "database_path",
        "event_id",
        "channel",
        "unknown_evidence_digest",
        "original_provider_reference",
        "terminal_outcome",
        "provider_reference",
        "receipt_state_dir",
    ):
        value = canonical_targets.get(field)
        if not isinstance(value, str) or not value:
            raise ReconciliationAuthorizationError(f"reconciliation target {field} is invalid")
        target_strings[field] = value

    raw_envelope = load_readback_file(envelope_path)
    action_id = raw_envelope.get("action_id")
    if not isinstance(action_id, str) or not ACTION_ID_RE.fullmatch(action_id):
        raise ReconciliationAuthorizationError("envelope action_id is invalid")
    state_dir = _private_directory(claim_state_dir, create=False)
    if str(state_dir) != target_strings["receipt_state_dir"]:
        raise ReconciliationAuthorizationError(
            "reconciliation claim state directory does not match the plan"
        )
    persisted_plan = load_readback_file(state_dir / f"{action_id}.plan.json")
    if canonical_json(persisted_plan) != canonical_json(canonical_plan):
        raise ReconciliationAuthorizationError(
            "persisted reconciliation plan does not match the requested plan"
        )
    claim_path = state_dir / f"{action_id}.claim.json"
    claim = load_readback_file(claim_path)
    if set(claim) != {
        "schema",
        "action_id",
        "action_kind",
        "envelope_digest",
        "claimed_at",
    }:
        raise ReconciliationAuthorizationError("claim fields mismatch")
    if (
        claim.get("schema") != "IrreversibleActionClaimV1"
        or claim.get("action_id") != action_id
        or claim.get("action_kind") != ACTION_KIND
        or claim.get("envelope_digest") != sha256_json(raw_envelope)
    ):
        raise ReconciliationAuthorizationError("claim does not bind the exact envelope")
    claimed_at = _parse_claimed_at(claim.get("claimed_at"))

    try:
        load_envelope, _, emit_receipt = _load_envelope_functions(envelope_module_path)
        envelope = load_envelope(
            envelope_path,
            expected_action_kind=ACTION_KIND,
            expected_targets=canonical_targets,
            expected_source_revision=source_revision,
            expected_artifact_digest=plan_digest,
            now=claimed_at,
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise ReconciliationAuthorizationError(str(exc)) from exc

    provider_idempotency_key = envelope.get("provider_idempotency_key")
    if not isinstance(provider_idempotency_key, str):
        raise ReconciliationAuthorizationError("envelope idempotency key is invalid")
    bounds = envelope.get("bounds")
    if not isinstance(bounds, dict):
        raise ReconciliationAuthorizationError(
            "consumed claim does not carry exact reconciliation authority"
        )
    canonical_bounds = _canonical_mapping(
        cast(dict[str, object], bounds),
        label="authority bounds",
    )
    expected_preconditions = {
        "unknown_evidence_digest": canonical_targets["unknown_evidence_digest"],
        "original_provider_reference": canonical_targets[
            "original_provider_reference"
        ],
    }
    if (
        envelope.get("one_shot") is not True
        or canonical_bounds.get("allowed_effect_count") != 1
        or canonical_json(envelope.get("preconditions"))
        != canonical_json(expected_preconditions)
        or envelope.get("required_readback") != REQUIRED_READBACK
    ):
        raise ReconciliationAuthorizationError(
            "consumed claim does not carry exact reconciliation authority"
        )
    if target_strings["terminal_outcome"] not in {
        "reconciled_succeeded",
        "reconciled_absent",
    }:
        raise ReconciliationAuthorizationError(
            "reconciliation terminal outcome is invalid"
        )
    existing_receipt_path = state_dir / f"{action_id}.receipt.json"
    existing_finalization = _existing_reconciliation_finalization(
        existing_receipt_path,
        envelope=envelope,
        canonical_targets=canonical_targets,
        canonical_readback=canonical_readback,
        canonical_plan=canonical_plan,
    )
    if existing_finalization is not None:
        return existing_finalization

    observed_outcome, effect_count, outcome_readback, recovered_receipt = (
        _classify_post_claim_state(
            target_strings=target_strings,
            canonical_readback=canonical_readback,
            action_id=action_id,
            plan_digest=plan_digest,
            provider_idempotency_key=provider_idempotency_key,
        )
    )
    try:
        authority_receipt_path = emit_receipt(
            envelope,
            receipt_dir=state_dir,
            target=canonical_targets,
            provider_reference=target_strings["provider_reference"],
            readback_result=outcome_readback,
            terminal_outcome=observed_outcome,
            effect_count=effect_count,
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        concurrent_finalization = _existing_reconciliation_finalization(
            existing_receipt_path,
            envelope=envelope,
            canonical_targets=canonical_targets,
            canonical_readback=canonical_readback,
            canonical_plan=canonical_plan,
        )
        if concurrent_finalization is not None:
            return concurrent_finalization
        raise ReconciliationAuthorizationError(
            f"consumed claim classification={observed_outcome}; receipt emission failed: {exc}"
        ) from exc
    authority_receipt = _validate_existing_authority_receipt(
        load_readback_file(authority_receipt_path),
        envelope=envelope,
        canonical_targets=canonical_targets,
        canonical_readback=canonical_readback,
    )
    return {
        "status": "finalized",
        "plan": canonical_plan,
        "receipt": recovered_receipt,
        "authority_receipt": authority_receipt,
        "authority_receipt_path": str(authority_receipt_path),
        "terminal_outcome": authority_receipt["terminal_outcome"],
    }


def _validate_unknown_authority_receipt(
    original_plan: Mapping[str, object],
    original_receipt: Mapping[str, object],
) -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, str],
]:
    canonical_plan = _canonical_mapping(original_plan, label="original plan")
    plan_digest = canonical_plan.get("plan_digest")
    body = {key: value for key, value in canonical_plan.items() if key != "plan_digest"}
    targets = canonical_plan.get("canonical_targets")
    readback = canonical_plan.get("readback_result")
    if (
        canonical_plan.get("schema") != PLAN_SCHEMA
        or canonical_plan.get("action_kind") != ACTION_KIND
        or plan_digest != sha256_json(body)
        or not isinstance(targets, dict)
        or not isinstance(readback, dict)
    ):
        raise ReconciliationAuthorizationError("original reconciliation plan is invalid")
    canonical_targets = _canonical_mapping(
        cast(dict[str, object], targets),
        label="original canonical targets",
    )
    canonical_readback = _canonical_mapping(
        cast(dict[str, object], readback),
        label="original readback",
    )
    target_strings: dict[str, str] = {}
    for field in (
        "database_path",
        "event_id",
        "channel",
        "unknown_evidence_digest",
        "original_provider_reference",
        "terminal_outcome",
        "provider_reference",
        "receipt_state_dir",
    ):
        value = canonical_targets.get(field)
        if not isinstance(value, str) or not value:
            raise ReconciliationAuthorizationError(
                f"original reconciliation target {field} is invalid"
            )
        target_strings[field] = value
    canonical_receipt = _canonical_mapping(
        original_receipt,
        label="original authority receipt",
    )
    receipt_readback = canonical_receipt.get("readback_result")
    if (
        canonical_receipt.get("schema") != "IrreversibleActionReceiptV1"
        or canonical_receipt.get("action_kind") != ACTION_KIND
        or canonical_receipt.get("terminal_outcome") != "outcome_unknown"
        or canonical_receipt.get("target") != canonical_targets
        or canonical_receipt.get("artifact_digest") != plan_digest
        or canonical_receipt.get("provider_reference")
        != target_strings["provider_reference"]
        or not isinstance(canonical_receipt.get("action_id"), str)
        or not isinstance(canonical_receipt.get("provider_idempotency_key"), str)
        or not isinstance(receipt_readback, dict)
    ):
        raise ReconciliationAuthorizationError(
            "original authority receipt is not an exact outcome_unknown receipt"
        )
    canonical_receipt_readback = _canonical_mapping(
        cast(dict[str, object], receipt_readback),
        label="original authority receipt readback",
    )
    if any(
        canonical_receipt_readback.get(field) != canonical_readback.get(field)
        for field in REQUIRED_READBACK
    ):
        raise ReconciliationAuthorizationError(
            "original authority receipt readback does not match its plan"
        )
    return canonical_plan, canonical_targets, canonical_receipt, target_strings


def build_reconciliation_receipt_supersession_plan(
    original_plan: Mapping[str, object],
    *,
    original_receipt_path: Path,
) -> dict[str, object]:
    """Render a receipt-only supersession plan from current read-only database state."""
    absolute_receipt_path = Path(
        os.path.abspath(original_receipt_path.expanduser())
    )
    original_receipt = load_readback_file(absolute_receipt_path)
    canonical_receipt_path = absolute_receipt_path.resolve(strict=True)
    if canonical_receipt_path != absolute_receipt_path:
        raise ReconciliationAuthorizationError(
            "original authority receipt path must not traverse symlinks"
        )
    (
        canonical_plan,
        _,
        canonical_receipt,
        target_strings,
    ) = _validate_unknown_authority_receipt(original_plan, original_receipt)
    if canonical_receipt_path.parent != Path(
        target_strings["receipt_state_dir"]
    ):
        raise ReconciliationAuthorizationError(
            "original authority receipt directory does not match its plan"
        )
    original_action_id = cast(str, canonical_receipt["action_id"])
    provider_idempotency_key = cast(
        str,
        canonical_receipt["provider_idempotency_key"],
    )
    plan_digest = cast(str, canonical_plan["plan_digest"])
    resolved_outcome, _, resolution_readback, _ = _classify_post_claim_state(
        target_strings=target_strings,
        canonical_readback=cast(
            dict[str, object],
            canonical_plan["readback_result"],
        ),
        action_id=original_action_id,
        plan_digest=plan_digest,
        provider_idempotency_key=provider_idempotency_key,
    )
    if resolved_outcome == "outcome_unknown":
        raise ReconciliationAuthorizationError(
            "database state remains outcome_unknown; supersession is not allowed"
        )
    original_receipt_digest = sha256_json(canonical_receipt)
    resolution_readback["original_authority_receipt_digest"] = original_receipt_digest
    targets = {
        "original_action_id": original_action_id,
        "original_authority_receipt_path": str(canonical_receipt_path),
        "original_authority_receipt_digest": original_receipt_digest,
        "receipt_state_dir": str(canonical_receipt_path.parent),
        "database_path": target_strings["database_path"],
        "event_id": target_strings["event_id"],
        "channel": target_strings["channel"],
        "resolved_terminal_outcome": resolved_outcome,
        "provider_reference": target_strings["provider_reference"],
    }
    body: dict[str, object] = {
        "schema": SUPERSESSION_PLAN_SCHEMA,
        "action_kind": SUPERSESSION_ACTION_KIND,
        "canonical_targets": targets,
        "source_revision": original_receipt_digest,
        "readback_result": resolution_readback,
        "readback_digest": sha256_json(resolution_readback),
        "required_readback": SUPERSESSION_REQUIRED_READBACK,
        "effect_count": 1,
    }
    return {**body, "plan_digest": sha256_json(body)}


def _validate_supersession_plan(
    plan: Mapping[str, object],
) -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, str],
    str,
]:
    canonical_plan = _canonical_mapping(plan, label="supersession plan")
    plan_digest = canonical_plan.get("plan_digest")
    body = {key: value for key, value in canonical_plan.items() if key != "plan_digest"}
    targets = canonical_plan.get("canonical_targets")
    readback = canonical_plan.get("readback_result")
    if (
        canonical_plan.get("schema") != SUPERSESSION_PLAN_SCHEMA
        or canonical_plan.get("action_kind") != SUPERSESSION_ACTION_KIND
        or plan_digest != sha256_json(body)
        or not isinstance(plan_digest, str)
        or not isinstance(targets, dict)
        or not isinstance(readback, dict)
    ):
        raise ReconciliationAuthorizationError("supersession plan is invalid")
    canonical_targets = _canonical_mapping(
        cast(dict[str, object], targets),
        label="supersession targets",
    )
    canonical_readback = _canonical_mapping(
        cast(dict[str, object], readback),
        label="supersession readback",
    )
    target_strings: dict[str, str] = {}
    for field in (
        "original_action_id",
        "original_authority_receipt_path",
        "original_authority_receipt_digest",
        "receipt_state_dir",
        "database_path",
        "event_id",
        "channel",
        "resolved_terminal_outcome",
        "provider_reference",
    ):
        value = canonical_targets.get(field)
        if not isinstance(value, str) or not value:
            raise ReconciliationAuthorizationError(f"supersession target {field} is invalid")
        target_strings[field] = value
    if target_strings["resolved_terminal_outcome"] not in {
        "failed_before_effect",
        "reconciled_succeeded",
        "reconciled_absent",
    }:
        raise ReconciliationAuthorizationError("supersession outcome is invalid")
    if not ACTION_ID_RE.fullmatch(target_strings["original_action_id"]):
        raise ReconciliationAuthorizationError(
            "supersession original action ID is invalid"
        )
    if (
        canonical_plan.get("source_revision")
        != target_strings["original_authority_receipt_digest"]
        or canonical_plan.get("readback_digest") != sha256_json(canonical_readback)
        or canonical_plan.get("required_readback") != SUPERSESSION_REQUIRED_READBACK
        or canonical_plan.get("effect_count") != 1
        or canonical_readback.get("original_authority_receipt_digest")
        != target_strings["original_authority_receipt_digest"]
        or any(field not in canonical_readback for field in SUPERSESSION_REQUIRED_READBACK)
    ):
        raise ReconciliationAuthorizationError("supersession plan contract is invalid")
    return (
        canonical_plan,
        canonical_targets,
        canonical_readback,
        target_strings,
        plan_digest,
    )


def _validate_supersession_envelope(
    envelope: Mapping[str, object],
    *,
    canonical_targets: Mapping[str, object],
    canonical_readback: Mapping[str, object],
    target_strings: Mapping[str, str],
) -> str:
    bounds = envelope.get("bounds")
    if not isinstance(bounds, dict):
        raise ReconciliationAuthorizationError(
            "supersession authority is not exact and one-shot"
        )
    canonical_bounds = _canonical_mapping(
        cast(dict[str, object], bounds),
        label="supersession bounds",
    )
    expected_preconditions = {
        "original_authority_receipt_digest": target_strings[
            "original_authority_receipt_digest"
        ],
        "resolved_readback_digest": sha256_json(canonical_readback),
    }
    if (
        envelope.get("one_shot") is not True
        or canonical_bounds.get("allowed_effect_count") != 1
        or canonical_json(envelope.get("preconditions"))
        != canonical_json(expected_preconditions)
        or envelope.get("required_readback") != SUPERSESSION_REQUIRED_READBACK
    ):
        raise ReconciliationAuthorizationError(
            "supersession authority is not exact and one-shot"
        )
    action_id = envelope.get("action_id")
    if not isinstance(action_id, str) or not ACTION_ID_RE.fullmatch(action_id):
        raise ReconciliationAuthorizationError("supersession action ID is invalid")
    if action_id == target_strings["original_action_id"]:
        raise ReconciliationAuthorizationError(
            "supersession action ID must differ from the original action"
        )
    if envelope.get("canonical_targets") != canonical_targets:
        raise ReconciliationAuthorizationError("supersession authority target mismatch")
    return action_id


def _validate_existing_supersession_receipt(
    receipt: Mapping[str, object],
    *,
    envelope: Mapping[str, object],
    canonical_targets: Mapping[str, object],
    canonical_readback: Mapping[str, object],
    provider_reference: str,
) -> dict[str, object]:
    canonical_receipt = _canonical_mapping(
        receipt,
        label="supersession authority receipt",
    )
    if (
        canonical_receipt.get("schema") != "IrreversibleActionReceiptV1"
        or canonical_receipt.get("action_id") != envelope.get("action_id")
        or canonical_receipt.get("action_kind") != SUPERSESSION_ACTION_KIND
        or canonical_receipt.get("target") != canonical_targets
        or canonical_receipt.get("artifact_digest") != envelope.get("artifact_digest")
        or canonical_receipt.get("provider_idempotency_key")
        != envelope.get("provider_idempotency_key")
        or canonical_receipt.get("provider_reference") != provider_reference
        or canonical_receipt.get("readback_result") != canonical_readback
        or canonical_receipt.get("terminal_outcome") != "succeeded"
        or canonical_receipt.get("effect_count") != 1
    ):
        raise ReconciliationAuthorizationError(
            "existing supersession receipt does not match the consumed claim"
        )
    return canonical_receipt


def _finalize_reconciliation_receipt_supersession_claim_locked(
    plan: Mapping[str, object],
    *,
    envelope_path: Path,
    claim_state_dir: Path,
    envelope_module_path: Path | None = None,
) -> dict[str, object]:
    """Finalize a consumed receipt-only supersession claim without replay."""
    (
        canonical_plan,
        canonical_targets,
        canonical_readback,
        target_strings,
        plan_digest,
    ) = _validate_supersession_plan(plan)
    raw_envelope = load_readback_file(envelope_path)
    action_id = raw_envelope.get("action_id")
    if not isinstance(action_id, str) or not ACTION_ID_RE.fullmatch(action_id):
        raise ReconciliationAuthorizationError("envelope action_id is invalid")
    state_dir = _private_directory(claim_state_dir, create=False)
    if str(state_dir) != target_strings["receipt_state_dir"]:
        raise ReconciliationAuthorizationError(
            "supersession receipt directory does not match the plan"
        )
    if (
        Path(target_strings["original_authority_receipt_path"]).parent
        != state_dir
    ):
        raise ReconciliationAuthorizationError(
            "original authority receipt directory does not match the plan"
        )
    persisted_plan = load_readback_file(state_dir / f"{action_id}.plan.json")
    if canonical_json(persisted_plan) != canonical_json(canonical_plan):
        raise ReconciliationAuthorizationError(
            "persisted supersession plan does not match the requested plan"
        )
    claim = load_readback_file(state_dir / f"{action_id}.claim.json")
    if set(claim) != {
        "schema",
        "action_id",
        "action_kind",
        "envelope_digest",
        "claimed_at",
    }:
        raise ReconciliationAuthorizationError("claim fields mismatch")
    if (
        claim.get("schema") != "IrreversibleActionClaimV1"
        or claim.get("action_id") != action_id
        or claim.get("action_kind") != SUPERSESSION_ACTION_KIND
        or claim.get("envelope_digest") != sha256_json(raw_envelope)
    ):
        raise ReconciliationAuthorizationError("claim does not bind the exact envelope")
    claimed_at = _parse_claimed_at(claim.get("claimed_at"))
    try:
        load_envelope, _, emit_receipt = _load_envelope_functions(
            envelope_module_path
        )
        envelope = load_envelope(
            envelope_path,
            expected_action_kind=SUPERSESSION_ACTION_KIND,
            expected_targets=canonical_targets,
            expected_source_revision=target_strings[
                "original_authority_receipt_digest"
            ],
            expected_artifact_digest=plan_digest,
            now=claimed_at,
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise ReconciliationAuthorizationError(str(exc)) from exc
    _validate_supersession_envelope(
        envelope,
        canonical_targets=canonical_targets,
        canonical_readback=canonical_readback,
        target_strings=target_strings,
    )

    original_receipt_path = Path(
        target_strings["original_authority_receipt_path"]
    )
    current_original_receipt = load_readback_file(original_receipt_path)
    if sha256_json(current_original_receipt) != target_strings[
        "original_authority_receipt_digest"
    ]:
        raise ReconciliationAuthorizationError("original authority receipt changed")
    current_original_plan = load_readback_file(
        original_receipt_path.with_name(
            f"{target_strings['original_action_id']}.plan.json"
        )
    )
    _validate_unknown_authority_receipt(
        current_original_plan,
        current_original_receipt,
    )

    existing_receipt_path = state_dir / f"{action_id}.receipt.json"
    if existing_receipt_path.exists() or existing_receipt_path.is_symlink():
        existing_receipt = _validate_existing_supersession_receipt(
            load_readback_file(existing_receipt_path),
            envelope=envelope,
            canonical_targets=canonical_targets,
            canonical_readback=canonical_readback,
            provider_reference=target_strings["provider_reference"],
        )
        return {
            "status": "already_finalized",
            "plan": canonical_plan,
            "authority_receipt": existing_receipt,
            "authority_receipt_path": str(existing_receipt_path),
            "terminal_outcome": "succeeded",
            "resolved_terminal_outcome": target_strings[
                "resolved_terminal_outcome"
            ],
            "supersedes_action_id": target_strings["original_action_id"],
        }
    try:
        authority_receipt_path = emit_receipt(
            envelope,
            receipt_dir=state_dir,
            target=canonical_targets,
            provider_reference=target_strings["provider_reference"],
            readback_result=canonical_readback,
            terminal_outcome="succeeded",
            effect_count=1,
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise ReconciliationAuthorizationError(
            f"consumed supersession claim receipt emission failed: {exc}"
        ) from exc
    authority_receipt = _validate_existing_supersession_receipt(
        load_readback_file(authority_receipt_path),
        envelope=envelope,
        canonical_targets=canonical_targets,
        canonical_readback=canonical_readback,
        provider_reference=target_strings["provider_reference"],
    )
    return {
        "status": "finalized",
        "plan": canonical_plan,
        "authority_receipt": authority_receipt,
        "authority_receipt_path": str(authority_receipt_path),
        "terminal_outcome": "succeeded",
        "resolved_terminal_outcome": target_strings["resolved_terminal_outcome"],
        "supersedes_action_id": target_strings["original_action_id"],
    }


def apply_reconciliation_receipt_supersession_plan(
    plan: Mapping[str, object],
    *,
    envelope_path: Path,
    claim_state_dir: Path,
    envelope_module_path: Path | None = None,
) -> dict[str, object]:
    """Consume fresh authority and append one receipt that supersedes outcome_unknown."""
    (
        canonical_plan,
        canonical_targets,
        canonical_readback,
        target_strings,
        plan_digest,
    ) = _validate_supersession_plan(plan)

    current_original_receipt = load_readback_file(
        Path(target_strings["original_authority_receipt_path"])
    )
    if sha256_json(current_original_receipt) != target_strings[
        "original_authority_receipt_digest"
    ]:
        raise ReconciliationAuthorizationError("original authority receipt changed")
    original_plan_path = (
        Path(target_strings["original_authority_receipt_path"]).with_name(
            f"{target_strings['original_action_id']}.plan.json"
        )
    )
    current_original_plan = load_readback_file(original_plan_path)
    _validate_unknown_authority_receipt(
        current_original_plan,
        current_original_receipt,
    )
    current_plan = build_reconciliation_receipt_supersession_plan(
        current_original_plan,
        original_receipt_path=Path(
            target_strings["original_authority_receipt_path"]
        ),
    )
    if current_plan != canonical_plan:
        raise ReconciliationAuthorizationError(
            "supersession evidence changed after plan rendering"
        )
    try:
        load_envelope, claim_envelope, emit_receipt = _load_envelope_functions(
            envelope_module_path
        )
        envelope = load_envelope(
            envelope_path,
            expected_action_kind=SUPERSESSION_ACTION_KIND,
            expected_targets=canonical_targets,
            expected_source_revision=target_strings[
                "original_authority_receipt_digest"
            ],
            expected_artifact_digest=plan_digest,
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise ReconciliationAuthorizationError(str(exc)) from exc
    action_id = _validate_supersession_envelope(
        envelope,
        canonical_targets=canonical_targets,
        canonical_readback=canonical_readback,
        target_strings=target_strings,
    )
    state_dir = _private_directory(claim_state_dir, create=False)
    if str(state_dir) != target_strings["receipt_state_dir"]:
        raise ReconciliationAuthorizationError(
            "supersession receipt directory does not match the plan"
        )
    with _action_execution_lock(
        state_dir,
        action_id,
        exclusive=True,
    ):
        plan_artifact_path = _persist_plan_artifact(
            canonical_plan,
            action_id=action_id,
            state_dir=state_dir,
        )
        try:
            claim_envelope(envelope, state_dir)
            authority_receipt_path = emit_receipt(
                envelope,
                receipt_dir=state_dir,
                target=canonical_targets,
                provider_reference=target_strings["provider_reference"],
                readback_result=canonical_readback,
                terminal_outcome="succeeded",
                effect_count=1,
            )
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise ReconciliationAuthorizationError(str(exc)) from exc
    return {
        "status": "superseded",
        "plan": canonical_plan,
        "authority_receipt_path": str(authority_receipt_path),
        "plan_artifact_path": str(plan_artifact_path),
        "terminal_outcome": "succeeded",
        "resolved_terminal_outcome": target_strings["resolved_terminal_outcome"],
        "supersedes_action_id": target_strings["original_action_id"],
    }


def _execute_reconciliation_apply_locked(
    *,
    canonical_plan: dict[str, object],
    plan_digest: str,
    canonical_targets: dict[str, object],
    canonical_readback: dict[str, object],
    target_strings: Mapping[str, str],
    terminal_outcome: Literal["reconciled_succeeded", "reconciled_absent"],
    envelope: dict[str, object],
    claim_envelope: Callable[[dict[str, object], Path], Path],
    emit_receipt: Callable[..., Path],
    provider_idempotency_key: str,
    action_id: str,
    state_dir: Path,
) -> dict[str, object]:
    try:
        plan_artifact_path = _persist_plan_artifact(
            canonical_plan,
            action_id=action_id,
            state_dir=state_dir,
        )
        claim_envelope(envelope, state_dir)
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise ReconciliationAuthorizationError(str(exc)) from exc

    try:
        receipt = record_channel_reconciliation(
            target_strings["event_id"],
            target_strings["channel"],
            reconciliation_id=action_id,
            expected_unknown_evidence_digest=target_strings[
                "unknown_evidence_digest"
            ],
            expected_original_provider_reference=target_strings[
                "original_provider_reference"
            ],
            terminal_outcome=terminal_outcome,
            provider_reference=target_strings["provider_reference"],
            readback_result=canonical_readback,
            artifact_digest=plan_digest,
            provider_idempotency_key=provider_idempotency_key,
            path=Path(target_strings["database_path"]),
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        (
            observed_outcome,
            effect_count,
            outcome_readback,
            recovered_receipt,
        ) = _classify_post_claim_state(
            target_strings=target_strings,
            canonical_readback=canonical_readback,
            action_id=action_id,
            plan_digest=plan_digest,
            provider_idempotency_key=provider_idempotency_key,
        )
        try:
            authority_receipt_path = emit_receipt(
                envelope,
                receipt_dir=state_dir,
                target=canonical_targets,
                provider_reference=target_strings["provider_reference"],
                readback_result=outcome_readback,
                terminal_outcome=observed_outcome,
                effect_count=effect_count,
            )
        except (OSError, sqlite3.Error, ValueError) as receipt_exc:
            raise ReconciliationAuthorizationError(
                "reconciliation failed after authority claim; "
                f"post-claim outcome={observed_outcome}; "
                f"receipt emission failed: {receipt_exc}"
            ) from exc
        if recovered_receipt is not None:
            return {
                "status": "applied",
                "plan": canonical_plan,
                "receipt": recovered_receipt,
                "authority_receipt_path": str(authority_receipt_path),
                "plan_artifact_path": str(plan_artifact_path),
                "recovered_after_error": True,
            }
        raise ReconciliationAuthorizationError(
            "reconciliation failed after authority claim; "
            f"terminal outcome={observed_outcome}; "
            f"authority receipt={authority_receipt_path}"
        ) from exc

    try:
        authority_receipt_path = emit_receipt(
            envelope,
            receipt_dir=state_dir,
            target=canonical_targets,
            provider_reference=target_strings["provider_reference"],
            readback_result={
                **canonical_readback,
                "database_event_status": terminal_outcome,
                "database_receipt_digest": sha256_json(receipt),
            },
            terminal_outcome=terminal_outcome,
            effect_count=1,
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise ReconciliationAuthorizationError(
            "reconciliation committed but authority receipt emission failed; "
            "the append-only database receipt is authoritative"
        ) from exc

    return {
        "status": "applied",
        "plan": canonical_plan,
        "receipt": receipt,
        "authority_receipt_path": str(authority_receipt_path),
        "plan_artifact_path": str(plan_artifact_path),
        "recovered_after_error": False,
    }


def apply_reconciliation_plan(
    plan: Mapping[str, object],
    *,
    envelope_path: Path,
    claim_state_dir: Path,
    envelope_module_path: Path | None = None,
) -> dict[str, object]:
    """Consume exact one-shot authority and atomically apply one reconciliation plan."""
    canonical_plan = _canonical_mapping(plan, label="plan")
    plan_digest = canonical_plan.get("plan_digest")
    body = {key: value for key, value in canonical_plan.items() if key != "plan_digest"}
    if canonical_plan.get("schema") != PLAN_SCHEMA or plan_digest != sha256_json(body):
        raise ReconciliationAuthorizationError("reconciliation plan digest is invalid")
    if canonical_plan.get("action_kind") != ACTION_KIND:
        raise ReconciliationAuthorizationError("reconciliation action kind is invalid")
    targets = canonical_plan.get("canonical_targets")
    readback = canonical_plan.get("readback_result")
    if not isinstance(targets, dict) or not isinstance(readback, dict):
        raise ReconciliationAuthorizationError("reconciliation plan is incomplete")
    canonical_targets = _canonical_mapping(
        cast(dict[str, object], targets),
        label="canonical_targets",
    )
    canonical_readback = _canonical_mapping(
        cast(dict[str, object], readback),
        label="readback_result",
    )
    source_revision = canonical_plan.get("source_revision")
    if not isinstance(source_revision, str):
        raise ReconciliationAuthorizationError("reconciliation source revision is invalid")

    try:
        load_envelope, claim_envelope, emit_receipt = _load_envelope_functions(
            envelope_module_path
        )
        envelope = load_envelope(
            envelope_path,
            expected_action_kind=ACTION_KIND,
            expected_targets=canonical_targets,
            expected_source_revision=source_revision,
            expected_artifact_digest=cast(str, plan_digest),
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise ReconciliationAuthorizationError(str(exc)) from exc

    if envelope.get("one_shot") is not True:
        raise ReconciliationAuthorizationError("reconciliation authority must be one-shot")
    bounds = envelope.get("bounds")
    if not isinstance(bounds, dict):
        raise ReconciliationAuthorizationError("reconciliation authority bounds are invalid")
    canonical_bounds = _canonical_mapping(
        cast(dict[str, object], bounds),
        label="bounds",
    )
    if canonical_bounds.get("allowed_effect_count") != 1:
        raise ReconciliationAuthorizationError("reconciliation authority must allow one effect")
    expected_preconditions = {
        "unknown_evidence_digest": canonical_targets["unknown_evidence_digest"],
        "original_provider_reference": canonical_targets["original_provider_reference"],
    }
    if canonical_json(envelope.get("preconditions")) != canonical_json(expected_preconditions):
        raise ReconciliationAuthorizationError("reconciliation preconditions mismatch")
    if envelope.get("required_readback") != REQUIRED_READBACK:
        raise ReconciliationAuthorizationError("reconciliation readback contract mismatch")
    provider_idempotency_key = envelope.get("provider_idempotency_key")
    action_id = envelope.get("action_id")
    if not isinstance(action_id, str) or not isinstance(provider_idempotency_key, str):
        raise ReconciliationAuthorizationError("reconciliation envelope identity is invalid")
    target_strings: dict[str, str] = {}
    for field in (
        "database_path",
        "event_id",
        "channel",
        "unknown_evidence_digest",
        "original_provider_reference",
        "terminal_outcome",
        "provider_reference",
        "receipt_state_dir",
    ):
        value = canonical_targets.get(field)
        if not isinstance(value, str) or not value:
            raise ReconciliationAuthorizationError(f"reconciliation target {field} is invalid")
        target_strings[field] = value
    terminal_outcome = target_strings["terminal_outcome"]
    if terminal_outcome not in {"reconciled_succeeded", "reconciled_absent"}:
        raise ReconciliationAuthorizationError("reconciliation terminal outcome is invalid")

    supplied_state_dir = str(Path(os.path.abspath(claim_state_dir)))
    if supplied_state_dir != target_strings["receipt_state_dir"]:
        raise ReconciliationAuthorizationError(
            "reconciliation claim state directory does not match the plan"
        )
    state_dir = _private_directory(claim_state_dir, create=True)
    if str(state_dir) != target_strings["receipt_state_dir"]:
        raise ReconciliationAuthorizationError(
            "reconciliation claim state directory does not match the plan"
        )
    with _action_execution_lock(
        state_dir,
        action_id,
        exclusive=True,
    ):
        return _execute_reconciliation_apply_locked(
            canonical_plan=canonical_plan,
            plan_digest=cast(str, plan_digest),
            canonical_targets=canonical_targets,
            canonical_readback=canonical_readback,
            target_strings=target_strings,
            terminal_outcome=cast(
                Literal["reconciled_succeeded", "reconciled_absent"],
                terminal_outcome,
            ),
            envelope=envelope,
            claim_envelope=claim_envelope,
            emit_receipt=emit_receipt,
            provider_idempotency_key=provider_idempotency_key,
            action_id=action_id,
            state_dir=state_dir,
        )
