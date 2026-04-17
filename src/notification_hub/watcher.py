"""Bridge file watcher — monitors Recent Activity sections for changes."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, cast

from watchdog.events import DirModifiedEvent, FileModifiedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from notification_hub.config import BRIDGE_FILE, WATCHED_SECTIONS
from notification_hub.models import Event, Level

logger = logging.getLogger(__name__)


class ObserverHandle(Protocol):
    """Minimal observer interface used by the server lifecycle."""

    def stop(self) -> None: ...

    def join(self, timeout: float | None = None) -> None: ...

# Pattern for activity log entries:
# - [YYYY-MM-DD] [optional-tag] project-name: summary (branch)
_ACTIVITY_LINE = re.compile(
    r"^- \[(\d{4}-\d{2}-\d{2})\]\s*(?:\[(\w+)\]\s*)?(.+?):\s*(.+?)(?:\s*\(([^)]+)\))?\s*$"
)


def extract_section_lines(content: str, section_heading: str) -> list[str]:
    """Extract non-comment, non-empty lines from a markdown section."""
    lines = content.split("\n")
    in_section = False
    result: list[str] = []
    for line in lines:
        if line.strip() == section_heading:
            in_section = True
            continue
        if in_section:
            if line.startswith("## ") and line.strip() != section_heading:
                break
            stripped = line.strip()
            if stripped and not stripped.startswith("<!--"):
                result.append(stripped)
    return result


def diff_sections(old_content: str, new_content: str) -> list[str]:
    """Find new activity lines across all watched sections."""
    new_lines: list[str] = []
    for section in WATCHED_SECTIONS:
        old_lines = set(extract_section_lines(old_content, section))
        cur_lines = extract_section_lines(new_content, section)
        for line in cur_lines:
            if line not in old_lines:
                new_lines.append(line)
    return new_lines


def parse_activity_line(line: str) -> Event | None:
    """Parse a bridge activity line into an Event."""
    match = _ACTIVITY_LINE.match(line)
    if not match:
        return None
    date_str, tag, project, summary, branch = match.groups()
    title = f"Bridge: {project.strip()}"
    body_parts = [summary.strip()]
    if branch:
        body_parts.append(f"({branch})")
    if tag:
        body_parts.insert(0, f"[{tag}]")
    level: Level = "info"
    if tag and tag.upper() == "SHIPPED":
        level = "normal"
    return Event(
        source="bridge_watcher",
        level=level,
        title=title,
        body=" ".join(body_parts),
        project=project.strip(),
        timestamp=datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc),
    )


class BridgeFileHandler(FileSystemEventHandler):
    """Watchdog handler that detects bridge file changes and emits events."""

    def __init__(self, on_event: Callable[[Event], None]) -> None:
        super().__init__()
        self._on_event = on_event
        self._last_content = self._read_file()

    def _read_file(self) -> str:
        try:
            return BRIDGE_FILE.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def on_modified(self, event: DirModifiedEvent | FileModifiedEvent) -> None:
        if not isinstance(event, FileModifiedEvent):
            return
        src = event.src_path.decode() if isinstance(event.src_path, bytes) else event.src_path
        if Path(src).resolve() != BRIDGE_FILE.resolve():
            return
        new_content = self._read_file()
        new_lines = diff_sections(self._last_content, new_content)
        self._last_content = new_content
        for line in new_lines:
            parsed = parse_activity_line(line)
            if parsed:
                logger.info("Bridge watcher detected: %s", parsed.title)
                self._on_event(parsed)


def start_watcher(on_event: Callable[[Event], None]) -> ObserverHandle:
    """Start watching the bridge file directory. Returns the observer (call .stop() to halt)."""
    handler = BridgeFileHandler(on_event)
    observer = Observer()
    watch_dir = str(BRIDGE_FILE.parent)
    observer.schedule(handler, watch_dir, recursive=False)
    observer.daemon = True
    observer.start()
    logger.info("Bridge file watcher started on %s", watch_dir)
    return cast(ObserverHandle, observer)
