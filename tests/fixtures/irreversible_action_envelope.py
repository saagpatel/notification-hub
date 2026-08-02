#!/usr/bin/env python3
"""Validate, claim, and receipt federated irreversible-action authority."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA = "IrreversibleActionEnvelopeV1"
RECEIPT_SCHEMA = "IrreversibleActionReceiptV1"
MAX_ONE_SHOT_VALIDITY = timedelta(minutes=15)
MAX_RECURRING_VALIDITY = timedelta(days=31)
MAX_FUTURE_SKEW = timedelta(seconds=60)
ACTION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
TOP_LEVEL_FIELDS = {
    "schema",
    "action_id",
    "action_kind",
    "principal",
    "canonical_targets",
    "source_revision",
    "artifact_digest",
    "bounds",
    "issued_at",
    "expires_at",
    "one_shot",
    "provider_idempotency_key",
    "preconditions",
    "required_readback",
    "receipt_requirements",
}


class EnvelopeError(ValueError):
    """Raised when authority is missing, stale, mismatched, or already consumed."""


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_json(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _parse_time(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise EnvelopeError(f"{field} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EnvelopeError(f"{field} is invalid: {exc}") from exc
    if parsed.tzinfo is None:
        raise EnvelopeError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)


def _required_mapping(payload: dict[str, Any], field: str) -> dict[str, Any]:
    value = payload.get(field)
    if not isinstance(value, dict) or not value:
        raise EnvelopeError(f"{field} must be a non-empty object")
    return value


def _required_string(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise EnvelopeError(f"{field} must be a non-empty string")
    return value


def validate_envelope(
    payload: dict[str, Any],
    *,
    expected_action_kind: str | None = None,
    expected_targets: dict[str, object] | None = None,
    expected_source_revision: str | None = None,
    expected_artifact_digest: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    unknown_fields = set(payload) - TOP_LEVEL_FIELDS
    missing_fields = TOP_LEVEL_FIELDS - set(payload)
    if unknown_fields or missing_fields:
        raise EnvelopeError(
            f"envelope fields mismatch; missing={sorted(missing_fields)} "
            f"unknown={sorted(unknown_fields)}"
        )
    if payload.get("schema") != SCHEMA:
        raise EnvelopeError(f"schema must be {SCHEMA}")
    action_id = _required_string(payload, "action_id")
    if not ACTION_ID_RE.fullmatch(action_id):
        raise EnvelopeError("action_id has an invalid format")
    action_kind = _required_string(payload, "action_kind")
    if expected_action_kind is not None and action_kind != expected_action_kind:
        raise EnvelopeError("action_kind mismatch")

    principal = _required_mapping(payload, "principal")
    if principal.get("kind") not in {"operator", "automation", "service", "test-fixture"}:
        raise EnvelopeError("principal.kind is invalid")
    if not isinstance(principal.get("id"), str) or not principal["id"].strip():
        raise EnvelopeError("principal.id is required")

    targets = _required_mapping(payload, "canonical_targets")
    if expected_targets is not None and canonical_json(targets) != canonical_json(expected_targets):
        raise EnvelopeError("canonical targets mismatch")

    source_revision = _required_string(payload, "source_revision")
    if expected_source_revision is not None and source_revision != expected_source_revision:
        raise EnvelopeError("source_revision mismatch")
    artifact_digest = _required_string(payload, "artifact_digest")
    if not DIGEST_RE.fullmatch(artifact_digest):
        raise EnvelopeError("artifact_digest must be sha256:<64 lowercase hex>")
    if expected_artifact_digest is not None and artifact_digest != expected_artifact_digest:
        raise EnvelopeError("artifact_digest mismatch")

    bounds = _required_mapping(payload, "bounds")
    allowed_effect_count = bounds.get("allowed_effect_count")
    if not isinstance(allowed_effect_count, int) or isinstance(allowed_effect_count, bool):
        raise EnvelopeError("bounds.allowed_effect_count must be an integer")
    if allowed_effect_count < 1:
        raise EnvelopeError("bounds.allowed_effect_count must be positive")
    for field in ("max_deletions", "minimum_survivors", "max_cost_minor_units"):
        value = bounds.get(field)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            raise EnvelopeError(f"bounds.{field} must be a non-negative integer")

    issued_at = _parse_time(payload.get("issued_at"), "issued_at")
    expires_at = _parse_time(payload.get("expires_at"), "expires_at")
    if expires_at <= issued_at:
        raise EnvelopeError("expires_at must be after issued_at")
    one_shot = payload.get("one_shot")
    if not isinstance(one_shot, bool):
        raise EnvelopeError("one_shot must be boolean")
    max_validity = MAX_ONE_SHOT_VALIDITY if one_shot else MAX_RECURRING_VALIDITY
    if expires_at - issued_at > max_validity:
        raise EnvelopeError("authority validity window is too long")
    current = (now or datetime.now(tz=UTC)).astimezone(UTC)
    if issued_at > current + MAX_FUTURE_SKEW:
        raise EnvelopeError("authority issuance is too far in the future")
    if current < issued_at or current >= expires_at:
        raise EnvelopeError("authority is not currently valid")

    _required_string(payload, "provider_idempotency_key")
    _required_mapping(payload, "preconditions")
    readback = payload.get("required_readback")
    if (
        not isinstance(readback, list)
        or not readback
        or not all(isinstance(item, str) and item.strip() for item in readback)
    ):
        raise EnvelopeError("required_readback must contain non-empty strings")
    receipt = _required_mapping(payload, "receipt_requirements")
    if receipt.get("schema") != RECEIPT_SCHEMA:
        raise EnvelopeError(f"receipt_requirements.schema must be {RECEIPT_SCHEMA}")
    for field in ("provider_reference", "readback_result", "terminal_outcome"):
        if receipt.get(field) is not True:
            raise EnvelopeError(f"receipt_requirements.{field} must be true")
    return payload


def load_envelope(path: Path, **kwargs: object) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise EnvelopeError("envelope must be a regular non-symlink file")
    metadata = path.stat()
    if metadata.st_uid != os.geteuid():
        raise EnvelopeError("envelope must be owned by the effective user")
    mode = metadata.st_mode & 0o777
    if mode & 0o077:
        raise EnvelopeError(f"envelope permissions must not grant group/other access: {mode:04o}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EnvelopeError(f"envelope is invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise EnvelopeError("envelope must be a JSON object")
    return validate_envelope(payload, **kwargs)


def require_private_authority_directory(directory: Path) -> Path:
    """Return one canonical owner-private directory or fail without repairing it."""
    absolute = Path(os.path.abspath(directory))
    if absolute.exists() or absolute.is_symlink():
        metadata = absolute.lstat()
        if absolute.is_symlink() or not absolute.is_dir():
            raise EnvelopeError("authority path must be a non-symlink directory")
    else:
        # Another legitimate worker may create the shared private directory
        # after the existence check. Re-read and validate the winning object.
        absolute.mkdir(parents=True, mode=0o700, exist_ok=True)
        metadata = absolute.lstat()
        if absolute.is_symlink() or not absolute.is_dir():
            raise EnvelopeError("authority path must be a non-symlink directory")
    if metadata.st_uid != os.geteuid():
        raise EnvelopeError("authority directory must be owned by the effective user")
    if metadata.st_mode & 0o077:
        raise EnvelopeError("authority directory must not grant group or other access")
    canonical = absolute.resolve(strict=True)
    return canonical


def claim_envelope(payload: dict[str, Any], state_dir: Path) -> Path:
    """Atomically claim one-shot authority; a repeated claim always fails."""
    state_dir = require_private_authority_directory(state_dir)
    claim_path = state_dir / f"{payload['action_id']}.claim.json"
    claim = {
        "schema": "IrreversibleActionClaimV1",
        "action_id": payload["action_id"],
        "action_kind": payload["action_kind"],
        "envelope_digest": sha256_json(payload),
        "claimed_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
    }
    try:
        fd = os.open(claim_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise EnvelopeError("one-shot authority has already been claimed") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(canonical_json(claim) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    directory_fd = os.open(state_dir, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return claim_path


def emit_receipt(
    payload: dict[str, Any],
    *,
    receipt_dir: Path,
    target: object,
    provider_reference: str,
    readback_result: object,
    terminal_outcome: str,
    effect_count: int,
) -> Path:
    if terminal_outcome not in {
        "failed_before_effect",
        "succeeded",
        "outcome_unknown",
        "reconciled_succeeded",
        "reconciled_absent",
    }:
        raise EnvelopeError("invalid terminal outcome")
    canonical_targets = _required_mapping(payload, "canonical_targets")
    if not isinstance(target, dict) or not target:
        raise EnvelopeError("receipt target must be a non-empty object")
    for field, expected in canonical_targets.items():
        if field not in target or canonical_json(target[field]) != canonical_json(expected):
            raise EnvelopeError(f"receipt target does not bind canonical target: {field}")
    if not isinstance(provider_reference, str) or not provider_reference.strip():
        raise EnvelopeError("receipt provider_reference must be a non-empty string")
    if not isinstance(readback_result, dict) or not readback_result:
        raise EnvelopeError("receipt readback_result must be a non-empty object")
    required_readback = payload.get("required_readback")
    if not isinstance(required_readback, list) or not required_readback:
        raise EnvelopeError("envelope required_readback must be a non-empty list")
    missing_readback = [
        field
        for field in required_readback
        if not isinstance(field, str) or not field.strip() or field not in readback_result
    ]
    if missing_readback:
        raise EnvelopeError(f"receipt is missing required readback fields: {missing_readback}")
    bounds = _required_mapping(payload, "bounds")
    allowed_effect_count = bounds.get("allowed_effect_count")
    if not isinstance(effect_count, int) or isinstance(effect_count, bool) or effect_count < 0:
        raise EnvelopeError("receipt effect_count must be a non-negative integer")
    if (
        not isinstance(allowed_effect_count, int)
        or isinstance(allowed_effect_count, bool)
        or effect_count > allowed_effect_count
    ):
        raise EnvelopeError("receipt effect_count exceeds envelope authority")
    receipt_dir = require_private_authority_directory(receipt_dir)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "action_id": payload["action_id"],
        "action_kind": payload["action_kind"],
        "target": target,
        "artifact_digest": payload["artifact_digest"],
        "provider_idempotency_key": payload["provider_idempotency_key"],
        "provider_reference": provider_reference,
        "readback_result": readback_result,
        "effect_count": effect_count,
        "terminal_outcome": terminal_outcome,
        "recorded_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
    }
    target_path = receipt_dir / f"{payload['action_id']}.receipt.json"
    fd, temporary = tempfile.mkstemp(prefix=".receipt-", dir=receipt_dir)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(canonical_json(receipt) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # Publish without replacement so concurrent terminal writers cannot
            # erase the first durable outcome. Hard-link creation is atomic and
            # fails with EEXIST when another writer won the race.
            os.link(temporary, target_path)
        except FileExistsError:
            metadata = target_path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise EnvelopeError("existing receipt must be a regular non-symlink file")
            if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
                raise EnvelopeError(
                    "existing receipt must be owned by the effective user "
                    "without group or other access"
                )
            try:
                existing = json.loads(target_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise EnvelopeError(
                    "existing receipt is unreadable; refusing to replace terminal evidence"
                ) from exc
            comparable = {k: v for k, v in receipt.items() if k != "recorded_at"}
            existing_comparable = {k: v for k, v in existing.items() if k != "recorded_at"}
            if comparable != existing_comparable:
                raise EnvelopeError("receipt already exists with different terminal evidence")
            return target_path
        directory_fd = os.open(receipt_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--envelope", type=Path, required=True)
    parser.add_argument("--action-kind")
    parser.add_argument("--targets-json")
    parser.add_argument("--source-revision")
    parser.add_argument("--artifact-digest")
    parser.add_argument("--claim-state-dir", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        targets = json.loads(args.targets_json) if args.targets_json else None
        if targets is not None and not isinstance(targets, dict):
            raise EnvelopeError("--targets-json must decode to an object")
        payload = load_envelope(
            args.envelope,
            expected_action_kind=args.action_kind,
            expected_targets=targets,
            expected_source_revision=args.source_revision,
            expected_artifact_digest=args.artifact_digest,
        )
        if args.claim_state_dir is not None and payload["one_shot"]:
            claim_envelope(payload, args.claim_state_dir)
    except (EnvelopeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(canonical_json({"status": "valid", "action_id": payload["action_id"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
