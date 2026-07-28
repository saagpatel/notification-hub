"""Producer authentication policy tests with fixture-only credentials."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from notification_hub.producer_auth import (
    ProducerAuthenticationError,
    ProducerPolicyError,
    authenticate_producer,
    load_producer_policy,
    load_producer_token,
)


def _write_policy(
    path: Path,
    *,
    token: str = "fixture-token",
    destinations: list[str] | None = None,
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "NotificationProducerPolicyV1",
                "producers": {
                    "fixture": {
                        "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                        "allowed_destinations": destinations
                        or ["log", "push", "slack"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


def test_authentication_binds_exact_producer_and_token(tmp_path: Path) -> None:
    policy = tmp_path / "producer-auth.json"
    _write_policy(policy)

    grant = authenticate_producer(
        "fixture",
        "Bearer fixture-token",
        policy_path=policy,
    )

    assert grant.producer_id == "fixture"
    assert grant.allowed_destinations == frozenset({"log", "push", "slack"})
    with pytest.raises(ProducerAuthenticationError):
        authenticate_producer("fixture", "Bearer wrong-token", policy_path=policy)
    with pytest.raises(ProducerAuthenticationError):
        authenticate_producer("other", "Bearer fixture-token", policy_path=policy)


def test_policy_must_be_owner_private_and_destinations_exact(tmp_path: Path) -> None:
    policy = tmp_path / "producer-auth.json"
    _write_policy(policy)
    policy.chmod(0o644)
    with pytest.raises(ProducerPolicyError, match="owner-private"):
        load_producer_policy(policy)

    _write_policy(policy, destinations=["log", "email"])
    with pytest.raises(ProducerPolicyError, match="destination allowlist"):
        load_producer_policy(policy)


def test_token_file_must_be_owner_private_and_non_symlinked(tmp_path: Path) -> None:
    token_file = tmp_path / "fixture.token"
    token_file.write_text("fixture-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    assert load_producer_token("fixture", token_file=token_file) == "fixture-token"

    token_file.chmod(0o640)
    with pytest.raises(ProducerPolicyError, match="owner-private"):
        load_producer_token("fixture", token_file=token_file)

    token_file.chmod(0o600)
    link = tmp_path / "fixture-link.token"
    link.symlink_to(token_file)
    with pytest.raises(ProducerPolicyError, match="non-symlink"):
        load_producer_token("fixture", token_file=link)
