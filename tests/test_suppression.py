"""Tests for noise suppression: dedup, quiet hours, rate limiting."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from notification_hub.config import PolicyConfig, SuppressionPolicy
from notification_hub.models import Level, StoredEvent
from notification_hub.suppression import SuppressionEngine

PACIFIC = ZoneInfo("America/Los_Angeles")


def _stored(
    project: str | None = "test-proj",
    level: Level = "info",
    title: str = "Test",
    body: str = "Test body",
    classified_level: Level | None = None,
) -> StoredEvent:
    return StoredEvent(
        source="cc",
        level=level,
        title=title,
        body=body,
        project=project,
        classified_level=classified_level,
    )


class TestDedup:
    def test_first_event_not_duplicate(self) -> None:
        engine = SuppressionEngine()
        assert engine.is_duplicate(_stored()) is False

    def test_same_project_level_within_window_is_duplicate(self) -> None:
        engine = SuppressionEngine()
        event = _stored()
        engine.is_duplicate(event)
        assert engine.is_duplicate(_stored()) is True

    def test_different_project_not_duplicate(self) -> None:
        engine = SuppressionEngine()
        engine.is_duplicate(_stored(project="proj-a"))
        assert engine.is_duplicate(_stored(project="proj-b")) is False

    def test_different_level_not_duplicate(self) -> None:
        engine = SuppressionEngine()
        engine.is_duplicate(_stored(level="info"))
        assert engine.is_duplicate(_stored(level="urgent")) is False

    def test_none_project_dedupes_separately(self) -> None:
        engine = SuppressionEngine()
        engine.is_duplicate(_stored(project=None))
        assert engine.is_duplicate(_stored(project=None)) is True
        assert engine.is_duplicate(_stored(project="some-proj")) is False

    def test_dedup_uses_classified_level_not_source_level(self) -> None:
        """Events with same source level but different classified levels should NOT dedup."""
        engine = SuppressionEngine()
        e1 = _stored(project="ink", level="info", classified_level="urgent")
        e2 = _stored(project="ink", level="info", classified_level="normal")
        engine.is_duplicate(e1)
        assert engine.is_duplicate(e2) is False

    def test_dedup_matches_on_classified_level(self) -> None:
        """Events with same classified level should dedup even if source levels differ."""
        engine = SuppressionEngine()
        e1 = _stored(project="ink", level="info", classified_level="urgent")
        e2 = _stored(project="ink", level="normal", classified_level="urgent")
        engine.is_duplicate(e1)
        assert engine.is_duplicate(e2) is True

    def test_dedup_falls_back_to_source_level_when_no_classified(self) -> None:
        engine = SuppressionEngine()
        e1 = _stored(project="ink", level="info", classified_level=None)
        engine.is_duplicate(e1)
        e2 = _stored(project="ink", level="info", classified_level=None)
        assert engine.is_duplicate(e2) is True


class TestQuietHours:
    def test_midnight_is_quiet(self) -> None:
        engine = SuppressionEngine()
        midnight_pacific = datetime(2026, 4, 15, 7, 0, tzinfo=timezone.utc)  # midnight PT = 7 UTC
        assert engine.is_quiet_hours(midnight_pacific) is True

    def test_3am_is_quiet(self) -> None:
        engine = SuppressionEngine()
        three_am_pacific = datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc)  # 3 AM PT = 10 UTC
        assert engine.is_quiet_hours(three_am_pacific) is True

    def test_noon_is_not_quiet(self) -> None:
        engine = SuppressionEngine()
        noon_pacific = datetime(2026, 4, 15, 19, 0, tzinfo=timezone.utc)  # noon PT = 19 UTC
        assert engine.is_quiet_hours(noon_pacific) is False

    def test_10pm_is_not_quiet(self) -> None:
        engine = SuppressionEngine()
        ten_pm_pacific = datetime(
            2026, 4, 16, 5, 0, tzinfo=timezone.utc
        )  # 10 PM PT = 5 UTC next day
        assert engine.is_quiet_hours(ten_pm_pacific) is False

    def test_11pm_is_quiet(self) -> None:
        engine = SuppressionEngine()
        eleven_pm_pacific = datetime(
            2026, 4, 16, 6, 0, tzinfo=timezone.utc
        )  # 11 PM PT = 6 UTC next day
        assert engine.is_quiet_hours(eleven_pm_pacific) is True

    def test_boundary_7am_is_not_quiet(self) -> None:
        engine = SuppressionEngine()
        seven_am_pacific = datetime(2026, 4, 15, 14, 0, tzinfo=timezone.utc)  # 7 AM PT = 14 UTC
        assert engine.is_quiet_hours(seven_am_pacific) is False

    def test_boundary_659am_is_quiet(self) -> None:
        engine = SuppressionEngine()
        six_59_am = datetime(2026, 4, 15, 13, 59, tzinfo=timezone.utc)  # 6:59 AM PT = 13:59 UTC
        assert engine.is_quiet_hours(six_59_am) is True

    def test_boundary_1059pm_is_not_quiet(self) -> None:
        engine = SuppressionEngine()
        ten_59_pm = datetime(2026, 4, 16, 5, 59, tzinfo=timezone.utc)  # 10:59 PM PT = 5:59 UTC
        assert engine.is_quiet_hours(ten_59_pm) is False

    def test_queue_and_drain(self) -> None:
        engine = SuppressionEngine()
        event = _stored()
        engine.queue_for_morning(event)
        drained = engine.drain_quiet_queue()
        assert len(drained) == 1
        assert drained[0].event_id == event.event_id
        assert engine.drain_quiet_queue() == []


class TestRateLimiting:
    def test_push_under_limit(self) -> None:
        engine = SuppressionEngine()
        for _ in range(5):
            assert engine.check_push_rate() is True
            engine.record_push()

    def test_push_over_limit(self) -> None:
        engine = SuppressionEngine()
        for _ in range(5):
            engine.record_push()
        assert engine.check_push_rate() is False

    def test_slack_under_limit(self) -> None:
        engine = SuppressionEngine()
        for _ in range(20):
            assert engine.check_slack_rate() is True
            engine.record_slack()

    def test_slack_over_limit(self) -> None:
        engine = SuppressionEngine()
        for _ in range(20):
            engine.record_slack()
        assert engine.check_slack_rate() is False

    def test_push_rate_resets_after_window(self) -> None:
        engine = SuppressionEngine()
        old = datetime.now(timezone.utc) - timedelta(hours=1, minutes=1)
        for _ in range(5):
            engine.record_push_at(old)
        assert engine.check_push_rate() is True

    def test_slack_rate_resets_after_window(self) -> None:
        engine = SuppressionEngine()
        old = datetime.now(timezone.utc) - timedelta(hours=1, minutes=1)
        for _ in range(20):
            engine.record_slack_at(old)
        assert engine.check_slack_rate() is True


class TestOverflowBuffer:
    def test_empty_by_default(self) -> None:
        engine = SuppressionEngine()
        assert engine.has_overflow() is False
        assert engine.drain_overflow() == []

    def test_add_and_drain(self) -> None:
        engine = SuppressionEngine()
        event = _stored(title="Overflow 1")
        engine.add_to_overflow(event)
        assert engine.has_overflow() is True
        drained = engine.drain_overflow()
        assert len(drained) == 1
        assert drained[0].event_id == event.event_id
        assert engine.has_overflow() is False

    def test_drain_clears_buffer(self) -> None:
        engine = SuppressionEngine()
        engine.add_to_overflow(_stored(title="A"))
        engine.add_to_overflow(_stored(title="B"))
        drained = engine.drain_overflow()
        assert len(drained) == 2
        assert engine.drain_overflow() == []

    def test_multiple_overflows_accumulate(self) -> None:
        engine = SuppressionEngine()
        for i in range(5):
            engine.add_to_overflow(_stored(title=f"Event {i}"))
        assert len(engine.drain_overflow()) == 5


def test_uses_configured_quiet_hours_and_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = PolicyConfig(
        suppression=SuppressionPolicy(
            quiet_start_hour=21,
            quiet_end_hour=6,
            dedup_window_minutes=10,
            max_push_per_hour=1,
            max_slack_per_hour=2,
            max_overflow_buffer=3,
            max_quiet_queue=2,
        )
    )
    monkeypatch.setattr("notification_hub.suppression.get_policy_config", lambda: policy)
    engine = SuppressionEngine()

    nine_pm_pacific = datetime(2026, 4, 16, 4, 0, tzinfo=timezone.utc)
    assert engine.is_quiet_hours(nine_pm_pacific) is True

    assert engine.check_push_rate() is True
    engine.record_push()
    assert engine.check_push_rate() is False

    engine.queue_for_morning(_stored(title="first"))
    engine.queue_for_morning(_stored(title="second"))
    engine.queue_for_morning(_stored(title="dropped"))
    assert len(engine.drain_quiet_queue()) == 2
