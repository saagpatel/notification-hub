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
MAX_OVERFLOW_BUFFER = 500
MAX_QUIET_QUEUE = 200


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
        # Rate limit overflow buffer
        self._overflow_buffer: list[StoredEvent] = []

    def is_duplicate(self, event: StoredEvent) -> bool:
        """Check if this (project, classified_level) combo was seen within the dedup window."""
        effective_level = event.classified_level or event.level
        key = (event.project, effective_level)
        now = datetime.now(timezone.utc)
        last_seen = self._dedup_log.get(key)
        if last_seen and (now - last_seen) < DEDUP_WINDOW:
            logger.debug("Dedup suppressed: %s/%s", event.project, effective_level)
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
        if len(self._quiet_queue) >= MAX_QUIET_QUEUE:
            logger.warning(
                "Quiet queue full (%d), dropping event %s", MAX_QUIET_QUEUE, event.event_id
            )
            return
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

    def add_to_overflow(self, event: StoredEvent) -> None:
        """Add an event to the overflow buffer for later digest delivery."""
        if len(self._overflow_buffer) >= MAX_OVERFLOW_BUFFER:
            logger.warning(
                "Overflow buffer full (%d), dropping event %s", MAX_OVERFLOW_BUFFER, event.event_id
            )
            return
        self._overflow_buffer.append(event)
        logger.debug(
            "Event %s added to overflow buffer (%d total)",
            event.event_id,
            len(self._overflow_buffer),
        )

    def drain_overflow(self) -> list[StoredEvent]:
        """Return and clear the overflow buffer."""
        events = list(self._overflow_buffer)
        self._overflow_buffer.clear()
        return events

    def has_overflow(self) -> bool:
        """Check if there are events waiting in the overflow buffer."""
        return len(self._overflow_buffer) > 0
