"""Noise suppression: dedup, quiet hours, rate limiting."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from notification_hub.config import NoiseRule, get_policy_config
from notification_hub.models import Level, StoredEvent

logger = logging.getLogger(__name__)

PACIFIC = ZoneInfo("America/Los_Angeles")
DEFAULT_NOISE_RULES = (
    NoiseRule(source="personal-ops", window_minutes=5),
)


def _noise_rule_matches(rule: NoiseRule, event: StoredEvent, effective_level: Level) -> bool:
    title_text = event.title.lower()
    body_text = event.body.lower()
    combined_text = f"{title_text} {body_text}"
    if rule.source is not None and rule.source != event.source:
        return False
    if rule.project is not None and rule.project != event.project:
        return False
    if rule.project_prefix is not None:
        if event.project is None or not event.project.startswith(rule.project_prefix):
            return False
    if rule.level is not None and rule.level != effective_level:
        return False
    if rule.title_contains is not None and rule.title_contains not in title_text:
        return False
    if rule.body_contains is not None and rule.body_contains not in body_text:
        return False
    if rule.text_contains is not None and rule.text_contains not in combined_text:
        return False
    return True


class SuppressionEngine:
    """Manages dedup, quiet hours, and rate limiting for notification delivery."""

    def __init__(self) -> None:
        # Dedup: (project, level) -> last event timestamp
        self._dedup_log: dict[tuple[str | None, Level], datetime] = {}
        # Burst dedup: exact producer signature -> last event timestamp
        self._burst_dedup_log: dict[tuple[int, str, str | None, str, str, Level], datetime] = {}
        self._burst_duplicates = 0
        # Rate counters: channel -> list of timestamps
        self._push_times: list[datetime] = []
        self._slack_times: list[datetime] = []
        # Quiet hours queue
        self._quiet_queue: list[StoredEvent] = []
        # Rate limit overflow buffer
        self._overflow_buffer: list[StoredEvent] = []

    def is_burst_duplicate(self, event: StoredEvent) -> bool:
        """Suppress exact duplicate producer bursts before storage and delivery."""
        effective_level = event.classified_level or event.level
        rules = get_policy_config().noise.rules or DEFAULT_NOISE_RULES
        matching_rules = [
            (index, rule)
            for index, rule in enumerate(rules, start=1)
            if _noise_rule_matches(rule, event, effective_level)
        ]
        if not matching_rules:
            return False

        now = datetime.now(timezone.utc)
        max_window = max(rule.window_minutes for _index, rule in matching_rules)
        cutoff = now - timedelta(minutes=max_window)
        self._burst_dedup_log = {
            signature: seen_at
            for signature, seen_at in self._burst_dedup_log.items()
            if seen_at > cutoff
        }
        for index, rule in matching_rules:
            key = (index, event.source, event.project, event.title, event.body, effective_level)
            last_seen = self._burst_dedup_log.get(key)
            if last_seen and (now - last_seen) < timedelta(minutes=rule.window_minutes):
                self._burst_duplicates += 1
                logger.debug("Burst suppressed: %s/%s/%s", event.source, event.project, event.title)
                return True
        for index, _rule in matching_rules:
            key = (index, event.source, event.project, event.title, event.body, effective_level)
            self._burst_dedup_log[key] = now
        return False

    def is_duplicate(self, event: StoredEvent) -> bool:
        """Check if this (project, classified_level) combo was seen within the dedup window."""
        policy = get_policy_config().suppression
        effective_level = event.classified_level or event.level
        key = (event.project, effective_level)
        now = datetime.now(timezone.utc)
        last_seen = self._dedup_log.get(key)
        if last_seen and (now - last_seen) < timedelta(minutes=policy.dedup_window_minutes):
            logger.debug("Dedup suppressed: %s/%s", event.project, effective_level)
            return True
        self._dedup_log[key] = now
        return False

    def is_quiet_hours(self, at: datetime | None = None) -> bool:
        """Check if current time is in configured quiet hours."""
        policy = get_policy_config().suppression
        now_pacific = (at or datetime.now(timezone.utc)).astimezone(PACIFIC)
        hour = now_pacific.hour
        if policy.quiet_start_hour == policy.quiet_end_hour:
            return False
        if policy.quiet_start_hour < policy.quiet_end_hour:
            return policy.quiet_start_hour <= hour < policy.quiet_end_hour
        return hour >= policy.quiet_start_hour or hour < policy.quiet_end_hour

    def queue_for_morning(self, event: StoredEvent) -> None:
        """Queue an event for delivery when quiet hours end."""
        policy = get_policy_config().suppression
        if len(self._quiet_queue) >= policy.max_quiet_queue:
            logger.warning(
                "Quiet queue full (%d), dropping event %s",
                policy.max_quiet_queue,
                event.event_id,
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
        policy = get_policy_config().suppression
        self._push_times = self._prune_old(self._push_times, timedelta(hours=1))
        if len(self._push_times) >= policy.max_push_per_hour:
            logger.debug("Push rate limit reached (%d/hr)", policy.max_push_per_hour)
            return False
        return True

    def record_push(self) -> None:
        """Record a push notification send."""
        self.record_push_at(datetime.now(timezone.utc))

    def record_push_at(self, at: datetime) -> None:
        """Record a push notification at a specific time."""
        self._push_times.append(at)

    def check_slack_rate(self) -> bool:
        """Return True if a Slack message is allowed under rate limit."""
        policy = get_policy_config().suppression
        self._slack_times = self._prune_old(self._slack_times, timedelta(hours=1))
        if len(self._slack_times) >= policy.max_slack_per_hour:
            logger.debug("Slack rate limit reached (%d/hr)", policy.max_slack_per_hour)
            return False
        return True

    def record_slack(self) -> None:
        """Record a Slack message send."""
        self.record_slack_at(datetime.now(timezone.utc))

    def record_slack_at(self, at: datetime) -> None:
        """Record a Slack message at a specific time."""
        self._slack_times.append(at)

    def clear_rate_history(self) -> None:
        """Clear delivery history for both channels."""
        self._push_times.clear()
        self._slack_times.clear()

    def add_to_overflow(self, event: StoredEvent) -> None:
        """Add an event to the overflow buffer for later digest delivery."""
        policy = get_policy_config().suppression
        if len(self._overflow_buffer) >= policy.max_overflow_buffer:
            logger.warning(
                "Overflow buffer full (%d), dropping event %s",
                policy.max_overflow_buffer,
                event.event_id,
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

    def snapshot(self) -> dict[str, int]:
        """Return queue and recent-delivery counters for diagnostics."""
        self._push_times = self._prune_old(self._push_times, timedelta(hours=1))
        self._slack_times = self._prune_old(self._slack_times, timedelta(hours=1))
        return {
            "dedup_entries": len(self._dedup_log),
            "burst_dedup_entries": len(self._burst_dedup_log),
            "burst_duplicates": self._burst_duplicates,
            "queued_for_morning": len(self._quiet_queue),
            "overflow_buffered": len(self._overflow_buffer),
            "pushes_last_hour": len(self._push_times),
            "slacks_last_hour": len(self._slack_times),
        }
