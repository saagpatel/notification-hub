"""Tests for bridge file watcher diff logic and event parsing."""

from __future__ import annotations

from notification_hub.watcher import diff_sections, extract_section_lines, parse_activity_line


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
