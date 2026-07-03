"""Pydantic models for notification events."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Source = Literal["codex", "cc", "claude_ai", "bridge_watcher", "personal-ops", "notion-os"]
Level = Literal["urgent", "normal", "info"]
Intent = Literal[
    "needs_attention",
    "blocked",
    "waiting_on_user",
    "ready_to_review",
    "ready_to_merge",
    "handoff_created",
    "automation_failed",
    "completed",
    "informational",
]
type EventContextValue = str | int | float | bool | None

BRIDGE_SYSTEM_IDS: tuple[str, ...] = (
    "cc",
    "codex",
    "claude_ai",
    "notion_os",
    "personal_ops",
)

BRIDGE_SOURCE_ALIASES: dict[str, Source] = {
    "cc": "cc",
    "codex": "codex",
    "claude_ai": "claude_ai",
    "notion_os": "notion-os",
    "personal_ops": "personal-ops",
}

INTERNAL_SOURCE_IDS: tuple[Source, ...] = ("bridge_watcher",)
SOURCE_IDS: tuple[Source, ...] = (
    *BRIDGE_SOURCE_ALIASES.values(),
    *INTERNAL_SOURCE_IDS,
)

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\r\n]")


class Event(BaseModel):
    """Incoming notification event from any AI system."""

    source: Source
    level: Level
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=2000)
    project: str | None = Field(default=None, max_length=100)
    session_label: str | None = Field(default=None, max_length=200)
    intent: Intent | None = None
    context: dict[str, EventContextValue] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("source", mode="before")
    @classmethod
    def normalize_source_aliases(cls, v: object) -> object:
        """Accept bridge-db's underscore caller ids by mapping them to the hub's
        canonical (hyphenated) source names.

        A producer that reuses its bridge-db ``caller`` string (e.g. ``personal_ops``,
        ``notion_os``) when POSTing to the hub would otherwise hard-fail with a 422.
        We translate only via the explicit ``BRIDGE_SOURCE_ALIASES`` map — never a
        blanket ``_``→``-`` replace — because ``claude_ai`` and the internal
        ``bridge_watcher`` source are valid *with* underscores and must pass through
        unchanged. Unknown values fall through to the ``Source`` Literal check (still
        a 422), so this only widens the accepted set by the two known aliases.
        """
        if isinstance(v, str):
            return BRIDGE_SOURCE_ALIASES.get(v, v)
        return v

    @field_validator("level", mode="before")
    @classmethod
    def normalize_level_aliases(cls, v: object) -> object:
        """Normalize common producer aliases into the hub's routing levels."""
        if isinstance(v, str) and v.lower() in {"warn", "warning"}:
            return "normal"
        return v

    @field_validator("title", "body")
    @classmethod
    def strip_control_chars(cls, v: str) -> str:
        """Remove control characters to prevent log injection."""
        return _CONTROL_CHARS.sub("", v)

    @field_validator("project", "session_label")
    @classmethod
    def validate_optional_label(cls, v: str | None) -> str | None:
        if v is not None:
            v = _CONTROL_CHARS.sub("", v)
        return v

    @field_validator("context")
    @classmethod
    def sanitize_context(cls, v: dict[str, EventContextValue]) -> dict[str, EventContextValue]:
        clean: dict[str, EventContextValue] = {}
        for raw_key, raw_value in v.items():
            key = _CONTROL_CHARS.sub("", str(raw_key)).strip()
            if not key:
                continue
            value = raw_value
            if isinstance(value, str):
                value = _CONTROL_CHARS.sub("", value)
            clean[key[:100]] = value
        return clean


class StoredEvent(Event):
    """Event with server-assigned metadata, written to JSONL."""

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    classified_level: Level | None = None


class EventResponse(BaseModel):
    """Response returned after accepting an event."""

    event_id: str
    level: Level
    accepted: bool = True
