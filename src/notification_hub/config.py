"""Configuration constants and paths."""

from __future__ import annotations

from pathlib import Path

HOST = "127.0.0.1"
PORT = 9199

EVENTS_DIR = Path.home() / ".local" / "share" / "notification-hub"
EVENTS_LOG = EVENTS_DIR / "events.jsonl"

BRIDGE_FILE = Path.home() / ".claude" / "projects" / "-Users-d" / "memory" / "claude_ai_context.md"

# Sections in the bridge file that trigger events when changed
WATCHED_SECTIONS = (
    "## Recent Claude Code Activity",
    "## Recent Codex Activity",
)
