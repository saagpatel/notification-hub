"""Readback-gated delivery and explicit operator observation receipts."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from notification_hub.durable_inbox import (
    accepted_channels,
    get_channel_state,
    record_channel_state,
)


class DestinationReadback(Protocol):
    def readback(self, event_id: str, channel: str) -> str | None:
        """Return a durable destination reference, or None when not observed there."""


def confirm_delivery_with_readback(
    event_id: str,
    channel: str,
    adapter: DestinationReadback,
    *,
    path: Path | None = None,
) -> str:
    """Promote accepted to delivered only after destination readback."""
    if channel not in accepted_channels(event_id, path=path):
        raise ValueError("channel has no accepted transport receipt")
    destination_ref = adapter.readback(event_id, channel)
    if not destination_ref:
        raise LookupError("destination readback did not find the event")
    record_channel_state(
        event_id,
        channel,
        "delivered",
        path=path,
        destination_ref=destination_ref,
    )
    return destination_ref


def record_operator_observation(
    event_id: str,
    channel: str,
    observation_ref: str,
    *,
    path: Path | None = None,
) -> None:
    """Record an explicit operator acknowledgement after proven delivery."""
    if not observation_ref.strip():
        raise ValueError("observation_ref is required")
    if get_channel_state(event_id, channel, path=path) not in {"delivered", "observed"}:
        raise ValueError("operator observation requires proven delivery readback")
    record_channel_state(
        event_id,
        channel,
        "observed",
        path=path,
        destination_ref=observation_ref.strip(),
    )
