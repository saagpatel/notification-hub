"""Pydantic models for notification events."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


Source = Literal["codex", "cc", "claude_ai", "bridge_watcher"]
Level = Literal["urgent", "normal", "info"]


class Event(BaseModel):
    """Incoming notification event from any AI system."""

    source: Source
    level: Level
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=2000)
    project: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StoredEvent(Event):
    """Event with server-assigned metadata, written to JSONL."""

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    classified_level: Level | None = None


class EventResponse(BaseModel):
    """Response returned after accepting an event."""

    event_id: str
    level: Level
    accepted: bool = True
