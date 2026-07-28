"""Local producer authentication policy for notification intake."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import notification_hub.config as config

POLICY_SCHEMA = "NotificationProducerPolicyV1"
PRODUCER_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")
TOKEN_DIGEST_RE = re.compile(r"^[a-f0-9]{64}$")
ALLOWED_DESTINATIONS = frozenset({"log", "push", "slack"})


class ProducerPolicyError(ValueError):
    """Raised when the local producer policy is unavailable or invalid."""


class ProducerAuthenticationError(ValueError):
    """Raised when request credentials do not bind an exact producer."""


@dataclass(frozen=True)
class ProducerGrant:
    producer_id: str
    token_sha256: str
    allowed_destinations: frozenset[str]


def _owner_private_regular_file(path: Path, *, label: str) -> Path:
    absolute = path.expanduser().absolute()
    try:
        metadata = absolute.lstat()
    except FileNotFoundError as exc:
        raise ProducerPolicyError(f"{label} is missing") from exc
    if not stat.S_ISREG(metadata.st_mode) or absolute.is_symlink():
        raise ProducerPolicyError(f"{label} must be a regular non-symlink file")
    if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
        raise ProducerPolicyError(f"{label} must be owner-private")
    return absolute.resolve(strict=True)


def load_producer_policy(path: Path | None = None) -> dict[str, ProducerGrant]:
    """Load exact producer grants from an owner-private local policy file."""
    policy_path = _owner_private_regular_file(
        path or config.PRODUCER_AUTH_POLICY,
        label="producer authentication policy",
    )
    try:
        raw_object: object = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProducerPolicyError("producer authentication policy is unreadable") from exc
    if not isinstance(raw_object, dict):
        raise ProducerPolicyError("producer authentication policy fields mismatch")
    raw = cast(dict[object, object], raw_object)
    if set(raw) != {"schema", "producers"}:
        raise ProducerPolicyError("producer authentication policy fields mismatch")
    if raw.get("schema") != POLICY_SCHEMA:
        raise ProducerPolicyError("producer authentication policy schema mismatch")
    raw_producers_object = raw.get("producers")
    if not isinstance(raw_producers_object, dict) or not raw_producers_object:
        raise ProducerPolicyError("producer authentication policy has no producers")
    raw_producers = cast(dict[object, object], raw_producers_object)

    grants: dict[str, ProducerGrant] = {}
    seen_digests: set[str] = set()
    for raw_id, raw_grant_object in raw_producers.items():
        if not isinstance(raw_id, str) or not PRODUCER_ID_RE.fullmatch(raw_id):
            raise ProducerPolicyError("producer authentication policy has an invalid producer ID")
        if not isinstance(raw_grant_object, dict):
            raise ProducerPolicyError(f"producer grant fields mismatch: {raw_id}")
        raw_grant = cast(dict[object, object], raw_grant_object)
        if set(raw_grant) != {
            "token_sha256",
            "allowed_destinations",
        }:
            raise ProducerPolicyError(f"producer grant fields mismatch: {raw_id}")
        token_digest = raw_grant.get("token_sha256")
        raw_destinations_object = raw_grant.get("allowed_destinations")
        if not isinstance(token_digest, str) or not TOKEN_DIGEST_RE.fullmatch(
            token_digest
        ):
            raise ProducerPolicyError(f"producer token digest is invalid: {raw_id}")
        if token_digest in seen_digests:
            raise ProducerPolicyError("producer token digests must be unique")
        if not isinstance(raw_destinations_object, list):
            raise ProducerPolicyError(f"producer destination allowlist is invalid: {raw_id}")
        raw_destination_values = cast(list[object], raw_destinations_object)
        if not raw_destination_values or any(
            not isinstance(value, str) for value in raw_destination_values
        ):
            raise ProducerPolicyError(f"producer destination allowlist is invalid: {raw_id}")
        raw_destinations = cast(list[str], raw_destination_values)
        destinations = frozenset(raw_destinations)
        if (
            "log" not in destinations
            or not destinations <= ALLOWED_DESTINATIONS
            or len(destinations) != len(raw_destinations)
        ):
            raise ProducerPolicyError(f"producer destination allowlist is invalid: {raw_id}")
        grants[raw_id] = ProducerGrant(
            producer_id=raw_id,
            token_sha256=token_digest,
            allowed_destinations=destinations,
        )
        seen_digests.add(token_digest)
    return grants


def authenticate_producer(
    producer_id: str | None,
    authorization: str | None,
    *,
    policy_path: Path | None = None,
) -> ProducerGrant:
    """Authenticate one producer with an exact bearer token and local grant."""
    if producer_id is None or not PRODUCER_ID_RE.fullmatch(producer_id):
        raise ProducerAuthenticationError("producer identity is missing or invalid")
    if authorization is None:
        raise ProducerAuthenticationError("producer authorization is missing")
    scheme, separator, token = authorization.partition(" ")
    if (
        not separator
        or scheme.lower() != "bearer"
        or not token
        or len(token) > 512
        or any(character.isspace() for character in token)
    ):
        raise ProducerAuthenticationError("producer authorization is invalid")
    grant = load_producer_policy(policy_path).get(producer_id)
    if grant is None:
        raise ProducerAuthenticationError("producer authorization is invalid")
    supplied_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(supplied_digest, grant.token_sha256):
        raise ProducerAuthenticationError("producer authorization is invalid")
    return grant


def load_producer_token(
    producer_id: str,
    *,
    token_file: Path | None = None,
) -> str:
    """Load one producer's raw token from its owner-private client file."""
    if not PRODUCER_ID_RE.fullmatch(producer_id):
        raise ProducerPolicyError("producer identity is invalid")
    path = token_file or (config.PRODUCER_TOKEN_DIR / f"{producer_id}.token")
    private_path = _owner_private_regular_file(path, label="producer token file")
    try:
        token = private_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ProducerPolicyError("producer token file is unreadable") from exc
    if (
        not token
        or len(token) > 512
        or any(character.isspace() for character in token)
    ):
        raise ProducerPolicyError("producer token is invalid")
    return token


def producer_request_headers(
    producer_id: str,
    *,
    token_file: Path | None = None,
) -> dict[str, str]:
    """Build secret-bearing request headers without placing tokens in payloads."""
    token = load_producer_token(producer_id, token_file=token_file)
    return {
        "Authorization": f"Bearer {token}",
        "X-Notification-Hub-Producer": producer_id,
    }


def collect_producer_auth_health() -> dict[str, object]:
    """Report policy readiness without exposing producer IDs or token digests."""
    try:
        grants = load_producer_policy()
    except ProducerPolicyError:
        return {
            "status": "degraded",
            "configured": False,
            "producer_count": 0,
        }
    return {
        "status": "ok",
        "configured": True,
        "producer_count": len(grants),
    }
