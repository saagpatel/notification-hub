"""Tests for the event processing pipeline: classify → route → deliver."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from notification_hub.models import Event
from notification_hub.pipeline import process_event
import notification_hub.channels as channels_mod


@pytest.fixture
def tmp_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log_dir = tmp_path / "notification-hub"
    log_file = log_dir / "events.jsonl"
    monkeypatch.setattr(channels_mod, "EVENTS_DIR", log_dir)
    monkeypatch.setattr(channels_mod, "EVENTS_LOG", log_file)
    return log_file


def _event(
    title: str = "Test",
    body: str = "Test body",
    level: str = "info",
    source: str = "cc",
    project: str | None = None,
) -> Event:
    return Event(
        source=source,  # type: ignore[arg-type]
        level=level,  # type: ignore[arg-type]
        title=title,
        body=body,
        project=project,
    )


class TestPipelineClassification:
    def test_urgent_event_gets_classified(self, tmp_log: Path) -> None:
        with patch("notification_hub.pipeline.send_push", return_value=True) as mock_push:
            stored = process_event(_event(body="Verification failed on main"))
        assert stored.classified_level == "urgent"
        mock_push.assert_called_once_with(stored)

    def test_normal_event_no_push(self, tmp_log: Path) -> None:
        with patch("notification_hub.pipeline.send_push") as mock_push:
            stored = process_event(_event(body="Session complete for ink"))
        assert stored.classified_level == "normal"
        mock_push.assert_not_called()

    def test_info_event_no_push(self, tmp_log: Path) -> None:
        with patch("notification_hub.pipeline.send_push") as mock_push:
            stored = process_event(_event(body="Routine status update"))
        assert stored.classified_level == "info"
        mock_push.assert_not_called()

    def test_fallback_uses_source_level(self, tmp_log: Path) -> None:
        with patch("notification_hub.pipeline.send_push") as mock_push:
            stored = process_event(_event(body="Something generic happened", level="normal"))
        assert stored.classified_level == "normal"
        mock_push.assert_not_called()

    def test_keyword_overrides_source_level(self, tmp_log: Path) -> None:
        with patch("notification_hub.pipeline.send_push", return_value=True) as mock_push:
            stored = process_event(_event(body="Security finding in auth.py", level="info"))
        assert stored.classified_level == "urgent"
        mock_push.assert_called_once()


class TestPipelineLogging:
    def test_all_events_logged_to_jsonl(self, tmp_log: Path) -> None:
        with patch("notification_hub.pipeline.send_push", return_value=True):
            process_event(_event(body="Verification failed", level="info"))
            process_event(_event(body="Session complete"))
            process_event(_event(body="Just a status update"))

        lines = tmp_log.read_text().strip().split("\n")
        assert len(lines) == 3

    def test_stored_event_has_classified_level_in_jsonl(self, tmp_log: Path) -> None:
        import json

        with patch("notification_hub.pipeline.send_push", return_value=True):
            process_event(_event(body="Test regression detected", level="info"))

        line = tmp_log.read_text().strip()
        data = json.loads(line)
        assert data["classified_level"] == "urgent"
        assert data["level"] == "info"  # Original source level preserved


class TestPipelineRouting:
    def test_urgent_triggers_push(self, tmp_log: Path) -> None:
        with patch("notification_hub.pipeline.send_push", return_value=True) as mock_push:
            process_event(_event(body="Eval degradation: pass rate dropped"))
        assert mock_push.call_count == 1

    def test_multiple_urgent_events_each_get_push(self, tmp_log: Path) -> None:
        with patch("notification_hub.pipeline.send_push", return_value=True) as mock_push:
            process_event(_event(body="Verification failed"))
            process_event(_event(body="Security finding in routes"))
        assert mock_push.call_count == 2

    def test_push_failure_doesnt_break_pipeline(self, tmp_log: Path) -> None:
        with patch("notification_hub.pipeline.send_push", return_value=False):
            stored = process_event(_event(body="Approval needed for draft"))
        assert stored.classified_level == "urgent"
        assert tmp_log.exists()
