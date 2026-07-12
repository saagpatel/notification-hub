"""Isolated end-to-end readback and observation receipt tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from notification_hub.delivery_readback import (
    confirm_delivery_with_readback,
    record_operator_observation,
)
from notification_hub.durable_inbox import (
    accepted_channels,
    channel_state_counts,
    enqueue_event,
    record_channel_state,
)
from notification_hub.models import StoredEvent


class FixtureDestination:
    def __init__(self, ref: str | None) -> None:
        self.ref = ref

    def readback(self, event_id: str, channel: str) -> str | None:
        del event_id, channel
        return self.ref


def _event() -> StoredEvent:
    return StoredEvent(
        event_id="fixture:e2e:1",
        source="codex",
        level="normal",
        title="Fixture delivery",
        body="Isolated destination only.",
    )


def test_delivery_requires_acceptance_and_destination_readback(tmp_path: Path) -> None:
    db = tmp_path / "inbox.db"
    enqueue_event(_event(), path=db)

    with pytest.raises(ValueError, match="no accepted transport"):
        confirm_delivery_with_readback(
            "fixture:e2e:1", "slack", FixtureDestination("fixture://message/1"), path=db
        )

    record_channel_state("fixture:e2e:1", "slack", "attempted", path=db)
    record_channel_state("fixture:e2e:1", "slack", "accepted", path=db)
    with pytest.raises(LookupError, match="did not find"):
        confirm_delivery_with_readback("fixture:e2e:1", "slack", FixtureDestination(None), path=db)

    ref = confirm_delivery_with_readback(
        "fixture:e2e:1", "slack", FixtureDestination("fixture://message/1"), path=db
    )
    assert ref == "fixture://message/1"
    assert accepted_channels("fixture:e2e:1", path=db) == frozenset({"slack"})
    assert channel_state_counts(path=db) == {"delivered": 1}


def test_observation_is_explicit_and_cannot_be_downgraded(tmp_path: Path) -> None:
    db = tmp_path / "inbox.db"
    enqueue_event(_event(), path=db)
    record_channel_state("fixture:e2e:1", "push", "accepted", path=db)
    confirm_delivery_with_readback(
        "fixture:e2e:1", "push", FixtureDestination("fixture://push/1"), path=db
    )
    record_operator_observation("fixture:e2e:1", "push", "operator:test:ack", path=db)
    record_channel_state("fixture:e2e:1", "push", "delivered", path=db)

    assert channel_state_counts(path=db) == {"observed": 1}
