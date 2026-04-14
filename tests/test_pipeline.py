"""Tests for the event processing pipeline: classify → suppress → route → deliver."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from notification_hub.models import Event
from notification_hub.pipeline import (
    process_event,
    reset_suppression_engine,
    get_suppression_engine,
)
import notification_hub.channels as channels_mod


@pytest.fixture
def tmp_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log_dir = tmp_path / "notification-hub"
    log_file = log_dir / "events.jsonl"
    monkeypatch.setattr(channels_mod, "EVENTS_DIR", log_dir)
    monkeypatch.setattr(channels_mod, "EVENTS_LOG", log_file)
    return log_file


@pytest.fixture(autouse=True)
def fresh_suppression() -> None:
    """Reset suppression engine between tests."""
    reset_suppression_engine()


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


def _patch_channels():
    """Returns tuples of patches for all delivery channels."""
    return (
        patch("notification_hub.pipeline.send_push", return_value=True),
        patch("notification_hub.pipeline.send_slack", return_value=True),
        patch("notification_hub.pipeline.send_slack_digest", return_value=True),
    )


def _patch_daytime():
    """Patch suppression engine to report NOT quiet hours (daytime)."""
    return patch.object(get_suppression_engine(), "is_quiet_hours", return_value=False)


class TestClassificationRouting:
    def test_urgent_triggers_push_and_slack(self, tmp_log: Path) -> None:
        p1, p2, p3 = _patch_channels()
        with p1 as mock_push, p2 as mock_slack, p3, _patch_daytime():
            stored = process_event(_event(body="Verification failed on main"))
        assert stored.classified_level == "urgent"
        mock_push.assert_called_once_with(stored)
        mock_slack.assert_called_once_with(stored)

    def test_normal_triggers_slack_only(self, tmp_log: Path) -> None:
        p1, p2, p3 = _patch_channels()
        with p1 as mock_push, p2 as mock_slack, p3, _patch_daytime():
            stored = process_event(_event(body="Session complete for ink"))
        assert stored.classified_level == "normal"
        mock_push.assert_not_called()
        mock_slack.assert_called_once_with(stored)

    def test_info_no_push_no_slack(self, tmp_log: Path) -> None:
        p1, p2, p3 = _patch_channels()
        with p1 as mock_push, p2 as mock_slack, p3, _patch_daytime():
            stored = process_event(_event(body="Routine status update"))
        assert stored.classified_level == "info"
        mock_push.assert_not_called()
        mock_slack.assert_not_called()

    def test_keyword_overrides_source_level(self, tmp_log: Path) -> None:
        p1, p2, p3 = _patch_channels()
        with p1 as mock_push, p2 as mock_slack, p3, _patch_daytime():
            stored = process_event(_event(body="Security finding in auth.py", level="info"))
        assert stored.classified_level == "urgent"
        mock_push.assert_called_once()
        mock_slack.assert_called_once()


class TestLogging:
    def test_all_events_logged_to_jsonl(self, tmp_log: Path) -> None:
        p1, p2, p3 = _patch_channels()
        with p1, p2, p3, _patch_daytime():
            process_event(_event(body="Verification failed", level="info"))
            process_event(_event(body="Session complete", project="a"))
            process_event(_event(body="Just a status update", project="b"))

        lines = tmp_log.read_text().strip().split("\n")
        assert len(lines) == 3

    def test_classified_level_persisted_in_jsonl(self, tmp_log: Path) -> None:
        import json

        p1, p2, p3 = _patch_channels()
        with p1, p2, p3, _patch_daytime():
            process_event(_event(body="Test regression detected", level="info"))

        data = json.loads(tmp_log.read_text().strip())
        assert data["classified_level"] == "urgent"
        assert data["level"] == "info"


class TestDedup:
    def test_duplicate_event_suppresses_delivery(self, tmp_log: Path) -> None:
        p1, p2, p3 = _patch_channels()
        with p1 as mock_push, p2 as mock_slack, p3, _patch_daytime():
            process_event(_event(body="Verification failed", project="ink"))
            process_event(_event(body="Verification failed again", project="ink"))
        # First fires, second is deduped (same project + same classified level)
        assert mock_push.call_count == 1
        assert mock_slack.call_count == 1

    def test_different_projects_not_deduped(self, tmp_log: Path) -> None:
        p1, p2, p3 = _patch_channels()
        with p1 as mock_push, p2, p3, _patch_daytime():
            process_event(_event(body="Verification failed", project="ink"))
            process_event(_event(body="Verification failed", project="codec"))
        assert mock_push.call_count == 2

    def test_dedup_still_logs_to_jsonl(self, tmp_log: Path) -> None:
        p1, p2, p3 = _patch_channels()
        with p1, p2, p3, _patch_daytime():
            process_event(_event(body="Verification failed", project="ink"))
            process_event(_event(body="Verification failed again", project="ink"))
        lines = tmp_log.read_text().strip().split("\n")
        assert len(lines) == 2  # Both logged, even if second delivery suppressed


class TestQuietHours:
    def test_push_suppressed_during_quiet_hours(self, tmp_log: Path) -> None:
        p1, p2, p3 = _patch_channels()
        with p1 as mock_push, p2 as mock_slack, p3:
            with patch.object(get_suppression_engine(), "is_quiet_hours", return_value=True):
                stored = process_event(_event(body="Approval needed"))
        # Push queued, not sent
        mock_push.assert_not_called()
        # Slack still fires during quiet hours
        mock_slack.assert_called_once()

    def test_slack_not_affected_by_quiet_hours(self, tmp_log: Path) -> None:
        p1, p2, p3 = _patch_channels()
        with p1, p2 as mock_slack, p3:
            with patch.object(get_suppression_engine(), "is_quiet_hours", return_value=True):
                process_event(_event(body="Session complete for ink"))
        mock_slack.assert_called_once()


class TestRateLimiting:
    def test_push_overflow_sent_as_digest_for_urgent(self, tmp_log: Path) -> None:
        engine = get_suppression_engine()
        # Exhaust push rate
        for _ in range(5):
            engine.record_push()

        p1, p2, p3 = _patch_channels()
        with p1 as mock_push, p2 as mock_slack, p3 as mock_digest, _patch_daytime():
            process_event(_event(body="Approval needed", project="p1"))
        # Push skipped due to rate limit
        mock_push.assert_not_called()
        # Overflow flushed as digest when Slack delivery runs (urgent = push + slack)
        mock_digest.assert_called_once()
        # Individual Slack message still sent
        mock_slack.assert_called_once()

    def test_slack_overflow_goes_to_buffer(self, tmp_log: Path) -> None:
        engine = get_suppression_engine()
        # Exhaust slack rate
        for _ in range(20):
            engine.record_slack()

        p1, p2, p3 = _patch_channels()
        with p1, p2 as mock_slack, p3, _patch_daytime():
            process_event(_event(body="Session complete", project="p1"))
        mock_slack.assert_not_called()
        assert engine.has_overflow()

    def test_overflow_flushed_as_digest(self, tmp_log: Path) -> None:
        engine = get_suppression_engine()
        # Exhaust slack rate
        for _ in range(20):
            engine.record_slack()

        p1, p2, p3 = _patch_channels()
        with p1, p2, p3, _patch_daytime():
            # This event overflows
            process_event(_event(body="Session complete", project="p1"))

        assert engine.has_overflow()

        # Reset rate limit (simulate time passing)
        engine._slack_times.clear()

        p1, p2, p3 = _patch_channels()
        with p1, p2 as mock_slack, p3 as mock_digest, _patch_daytime():
            # Next event triggers overflow flush
            process_event(_event(body="Deployed to prod", project="p2"))
        # Digest sent for overflow + new event sent directly
        mock_digest.assert_called_once()
        mock_slack.assert_called_once()


class TestPushFailureResilience:
    def test_push_failure_doesnt_break_pipeline(self, tmp_log: Path) -> None:
        with (
            patch("notification_hub.pipeline.send_push", return_value=False),
            patch("notification_hub.pipeline.send_slack", return_value=True),
            patch("notification_hub.pipeline.send_slack_digest", return_value=True),
            _patch_daytime(),
        ):
            stored = process_event(_event(body="Approval needed for draft"))
        assert stored.classified_level == "urgent"
        assert tmp_log.exists()
