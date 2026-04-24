"""Pydantic models for notification events."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator


Source = Literal["codex", "cc", "claude_ai", "bridge_watcher", "personal-ops"]
Level = Literal["urgent", "normal", "info"]

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\r\n]")


class Event(BaseModel):
    """Incoming notification event from any AI system."""

    source: Source
    level: Level
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=2000)
    project: str | None = Field(default=None, max_length=100)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("title", "body")
    @classmethod
    def strip_control_chars(cls, v: str) -> str:
        """Remove control characters to prevent log injection."""
        return _CONTROL_CHARS.sub("", v)

    @field_validator("project")
    @classmethod
    def validate_project(cls, v: str | None) -> str | None:
        if v is not None:
            v = _CONTROL_CHARS.sub("", v)
        return v


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
