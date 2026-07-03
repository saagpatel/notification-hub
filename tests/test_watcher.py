"""Tests for bridge file watcher diff logic and event parsing."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from pathlib import Path

import pytest
from watchdog.events import FileModifiedEvent

import notification_hub.watcher as watcher_mod
from notification_hub.models import Event
from notification_hub.watcher import (
    BridgeFileHandler,
    diff_sections,
    extract_section_lines,
    parse_activity_line,
)

SAMPLE_BRIDGE = """\
# Bridge File

## Claude.ai Capabilities Summary

17 custom skills.

## Recent Claude Code Activity
<!-- /end skill appends here automatically -->
- [2026-04-13] AssistSupport: Added batch endpoint (feat/batch-classify)

## Codex State Snapshot

Some codex state.

## Recent Codex Activity
<!-- Codex bridge-sync automation appends here -->
- [2026-04-14] bridge-sync: First manual export
"""


class TestExtractSectionLines:
    def test_extracts_cc_activity(self) -> None:
        lines = extract_section_lines(SAMPLE_BRIDGE, "## Recent Claude Code Activity")
        assert lines == ["- [2026-04-13] AssistSupport: Added batch endpoint (feat/batch-classify)"]

    def test_extracts_codex_activity(self) -> None:
        lines = extract_section_lines(SAMPLE_BRIDGE, "## Recent Codex Activity")
        assert lines == ["- [2026-04-14] bridge-sync: First manual export"]

    def test_skips_comments(self) -> None:
        lines = extract_section_lines(SAMPLE_BRIDGE, "## Recent Claude Code Activity")
        assert not any(line.startswith("<!--") for line in lines)

    def test_missing_section_returns_empty(self) -> None:
        lines = extract_section_lines(SAMPLE_BRIDGE, "## Nonexistent Section")
        assert lines == []


class TestDiffSections:
    def test_detects_new_cc_activity(self) -> None:
        new_bridge = SAMPLE_BRIDGE.replace(
            "- [2026-04-13] AssistSupport: Added batch endpoint (feat/batch-classify)",
            "- [2026-04-13] AssistSupport: Added batch endpoint (feat/batch-classify)\n"
            "- [2026-04-14] ink: Phase 0 scaffold complete (feat/phase-0)",
        )
        new_lines = diff_sections(SAMPLE_BRIDGE, new_bridge)
        assert len(new_lines) == 1
        assert "ink" in new_lines[0]

    def test_detects_new_codex_activity(self) -> None:
        new_bridge = SAMPLE_BRIDGE.replace(
            "- [2026-04-14] bridge-sync: First manual export",
            "- [2026-04-14] bridge-sync: First manual export\n"
            "- [2026-04-14] portfolio-review: 3 repos flagged",
        )
        new_lines = diff_sections(SAMPLE_BRIDGE, new_bridge)
        assert len(new_lines) == 1
        assert "portfolio-review" in new_lines[0]

    def test_no_diff_when_unchanged(self) -> None:
        assert diff_sections(SAMPLE_BRIDGE, SAMPLE_BRIDGE) == []

    def test_detects_repeated_identical_appended_activity(self) -> None:
        repeated = "- [2026-04-14] bridge-sync: First manual export"
        new_bridge = SAMPLE_BRIDGE.replace(repeated, f"{repeated}\n{repeated}")
        assert diff_sections(SAMPLE_BRIDGE, new_bridge) == [repeated]


class TestParseActivityLine:
    def test_standard_line(self) -> None:
        event = parse_activity_line(
            "- [2026-04-13] AssistSupport: Added batch endpoint (feat/batch-classify)"
        )
        assert event is not None
        assert event.source == "bridge_watcher"
        assert event.project == "AssistSupport"
        assert event.level == "info"
        assert "batch endpoint" in event.body

    def test_shipped_tag(self) -> None:
        event = parse_activity_line(
            "- [2026-04-15] [SHIPPED] Chromafield: v1.0 released to App Store (main)"
        )
        assert event is not None
        assert event.level == "normal"
        assert event.project == "Chromafield"
        assert "[SHIPPED]" in event.body

    def test_no_branch(self) -> None:
        event = parse_activity_line("- [2026-04-14] bridge-sync: First manual export")
        assert event is not None
        assert event.project == "bridge-sync"

    def test_invalid_line_returns_none(self) -> None:
        assert parse_activity_line("not a valid line") is None
        assert parse_activity_line("") is None
        assert parse_activity_line("<!-- comment -->") is None

    def test_regex_valid_but_invalid_date_returns_none(self) -> None:
        # Matches \d{4}-\d{2}-\d{2} but is not a real calendar date; must not raise.
        assert parse_activity_line("- [2026-13-45] proj: impossible date") is None


# A watched section that starts empty (comment only) so an appended line is "new".
_WATCHED_BASE = "## Recent Codex Activity\n<!-- codex appends here -->\n"


def _modified_event(path: Path) -> FileModifiedEvent:
    return FileModifiedEvent(src_path=str(path))


class TestBridgeFileHandlerResilience:
    """The watcher thread must survive transient I/O, bad data, and callback errors."""

    def test_emits_event_on_new_activity(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        bridge = tmp_path / "bridge.md"
        bridge.write_text(_WATCHED_BASE, encoding="utf-8")
        monkeypatch.setattr(watcher_mod, "BRIDGE_FILE", bridge)
        events: list[Event] = []
        handler = BridgeFileHandler(events.append)
        bridge.write_text(_WATCHED_BASE + "- [2026-04-14] proj: did a thing\n", encoding="utf-8")
        handler.on_modified(_modified_event(bridge))
        assert len(events) == 1
        assert events[0].project == "proj"

    def test_read_oserror_returns_none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        bridge = tmp_path / "bridge.md"
        bridge.write_text(_WATCHED_BASE, encoding="utf-8")
        monkeypatch.setattr(watcher_mod, "BRIDGE_FILE", bridge)
        handler = BridgeFileHandler(lambda e: None)

        class FailingPath:
            def read_text(self, *args: object, **kwargs: object) -> str:
                raise PermissionError("temporarily locked")

        monkeypatch.setattr(watcher_mod, "BRIDGE_FILE", FailingPath())
        assert handler._read_file() is None  # must not raise

    def test_read_unicode_error_returns_none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        bridge = tmp_path / "bridge.md"
        bridge.write_text(_WATCHED_BASE, encoding="utf-8")
        monkeypatch.setattr(watcher_mod, "BRIDGE_FILE", bridge)
        handler = BridgeFileHandler(lambda e: None)

        class GarbledPath:
            def read_text(self, *args: object, **kwargs: object) -> str:
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

        monkeypatch.setattr(watcher_mod, "BRIDGE_FILE", GarbledPath())
        assert handler._read_file() is None

    def test_on_modified_survives_read_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        bridge = tmp_path / "bridge.md"
        bridge.write_text(_WATCHED_BASE, encoding="utf-8")
        monkeypatch.setattr(watcher_mod, "BRIDGE_FILE", bridge)
        events: list[Event] = []
        handler = BridgeFileHandler(events.append)
        prior = handler._last_content

        class FailingPath:
            def read_text(self, *args: object, **kwargs: object) -> str:
                raise OSError("transient I/O")

            def resolve(self) -> Path:
                return bridge.resolve()

        monkeypatch.setattr(watcher_mod, "BRIDGE_FILE", FailingPath())
        handler.on_modified(_modified_event(bridge))  # must not raise
        assert events == []
        assert handler._last_content == prior  # state preserved for the next cycle

    def test_on_modified_survives_callback_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        bridge = tmp_path / "bridge.md"
        bridge.write_text(_WATCHED_BASE, encoding="utf-8")
        monkeypatch.setattr(watcher_mod, "BRIDGE_FILE", bridge)
        calls: list[Event] = []

        def boom(event: Event) -> None:
            calls.append(event)
            raise RuntimeError("pipeline down")

        handler = BridgeFileHandler(boom)
        bridge.write_text(_WATCHED_BASE + "- [2026-04-14] proj: did a thing\n", encoding="utf-8")
        handler.on_modified(_modified_event(bridge))  # must not raise
        assert len(calls) == 1  # callback attempted; its error was swallowed
