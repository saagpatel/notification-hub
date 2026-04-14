"""Noise suppression: dedup, quiet hours, rate limiting."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from notification_hub.models import Level, StoredEvent

logger = logging.getLogger(__name__)

PACIFIC = ZoneInfo("America/Los_Angeles")
QUIET_START_HOUR = 23  # 11 PM
QUIET_END_HOUR = 7  # 7 AM
DEDUP_WINDOW = timedelta(minutes=30)
MAX_PUSH_PER_HOUR = 5
MAX_SLACK_PER_HOUR = 20


class SuppressionEngine:
    """Manages dedup, quiet hours, and rate limiting for notification delivery."""

    def __init__(self) -> None:
        # Dedup: (project, level) -> last event timestamp
        self._dedup_log: dict[tuple[str | None, Level], datetime] = {}
        # Rate counters: channel -> list of timestamps
        self._push_times: list[datetime] = []
        self._slack_times: list[datetime] = []
        # Quiet hours queue
        self._quiet_queue: list[StoredEvent] = []

    def is_duplicate(self, event: StoredEvent) -> bool:
        """Check if this (project, level) combo was seen within the dedup window."""
        key = (event.project, event.level)
        now = datetime.now(timezone.utc)
        last_seen = self._dedup_log.get(key)
        if last_seen and (now - last_seen) < DEDUP_WINDOW:
            logger.debug("Dedup suppressed: %s/%s", event.project, event.level)
            return True
        self._dedup_log[key] = now
        return False

    def is_quiet_hours(self, at: datetime | None = None) -> bool:
        """Check if current time is in quiet hours (11 PM - 7 AM Pacific)."""
        now_pacific = (at or datetime.now(timezone.utc)).astimezone(PACIFIC)
        hour = now_pacific.hour
        return hour >= QUIET_START_HOUR or hour < QUIET_END_HOUR

    def queue_for_morning(self, event: StoredEvent) -> None:
        """Queue an event for delivery when quiet hours end."""
        self._quiet_queue.append(event)
        logger.info("Queued event %s for morning delivery", event.event_id)

    def drain_quiet_queue(self) -> list[StoredEvent]:
        """Return and clear all queued events. Called when quiet hours end."""
        events = list(self._quiet_queue)
        self._quiet_queue.clear()
        return events

    def _prune_old(self, timestamps: list[datetime], window: timedelta) -> list[datetime]:
        """Remove timestamps older than window."""
        cutoff = datetime.now(timezone.utc) - window
        return [t for t in timestamps if t > cutoff]

    def check_push_rate(self) -> bool:
        """Return True if a push notification is allowed under rate limit."""
        self._push_times = self._prune_old(self._push_times, timedelta(hours=1))
        if len(self._push_times) >= MAX_PUSH_PER_HOUR:
            logger.debug("Push rate limit reached (%d/hr)", MAX_PUSH_PER_HOUR)
            return False
        return True

    def record_push(self) -> None:
        """Record a push notification send."""
        self._push_times.append(datetime.now(timezone.utc))

    def check_slack_rate(self) -> bool:
        """Return True if a Slack message is allowed under rate limit."""
        self._slack_times = self._prune_old(self._slack_times, timedelta(hours=1))
        if len(self._slack_times) >= MAX_SLACK_PER_HOUR:
            logger.debug("Slack rate limit reached (%d/hr)", MAX_SLACK_PER_HOUR)
            return False
        return True

    def record_slack(self) -> None:
        """Record a Slack message send."""
        self._slack_times.append(datetime.now(timezone.utc))
