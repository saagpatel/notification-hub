"""Noise suppression: dedup, quiet hours, rate limiting."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from notification_hub.config import NoiseRule, get_policy_config
from notification_hub.models import Level, StoredEvent

logger = logging.getLogger(__name__)

PACIFIC = ZoneInfo("America/Los_Angeles")
DEFAULT_NOISE_RULES = (NoiseRule(source="personal-ops", window_minutes=5),)


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
        # Semantic dedup is opt-in and records the predecessor event id.
        self._dedup_log: dict[tuple[str, str], tuple[datetime, str]] = {}
        # Burst dedup: exact producer signature -> last event timestamp
        self._burst_dedup_log: dict[
            tuple[int, str, str | None, str, str, Level], tuple[datetime, str]
        ] = {}
        self._burst_duplicates = 0
        # Rate counters: channel -> list of timestamps
        self._push_times: list[datetime] = []
        self._slack_times: list[datetime] = []
        # Quiet hours queue
        self._quiet_queue: list[StoredEvent] = []
        # Rate limit overflow buffer
        self._overflow_buffer: list[StoredEvent] = []

    def burst_duplicate_predecessor(self, event: StoredEvent) -> str | None:
        """Suppress exact duplicate producer bursts before storage and delivery."""
        effective_level = event.classified_level or event.level
        rules = get_policy_config().noise.rules or DEFAULT_NOISE_RULES
        matching_rules = [
            (index, rule)
            for index, rule in enumerate(rules, start=1)
            if _noise_rule_matches(rule, event, effective_level)
        ]
        if not matching_rules:
            return None

        now = datetime.now(UTC)
        max_window = max(rule.window_minutes for _index, rule in matching_rules)
        cutoff = now - timedelta(minutes=max_window)
        self._burst_dedup_log = {
            signature: receipt
            for signature, receipt in self._burst_dedup_log.items()
            if receipt[0] > cutoff
        }
        for index, rule in matching_rules:
            key = (index, event.source, event.project, event.title, event.body, effective_level)
            prior = self._burst_dedup_log.get(key)
            if prior and (now - prior[0]) < timedelta(minutes=rule.window_minutes):
                self._burst_duplicates += 1
                logger.debug("Burst suppressed: %s/%s/%s", event.source, event.project, event.title)
                return prior[1]
        for index, _rule in matching_rules:
            key = (index, event.source, event.project, event.title, event.body, effective_level)
            self._burst_dedup_log[key] = (now, event.event_id)
        return None

    def is_burst_duplicate(self, event: StoredEvent) -> bool:
        return self.burst_duplicate_predecessor(event) is not None

    def semantic_duplicate_predecessor(self, event: StoredEvent) -> str | None:
        """Return the predecessor for an explicit semantic key, never project/level alone."""
        if event.semantic_dedupe_key is None:
            return None
        policy = get_policy_config().suppression
        key = (event.source, event.semantic_dedupe_key)
        now = datetime.now(UTC)
        prior = self._dedup_log.get(key)
        if prior and (now - prior[0]) < timedelta(minutes=policy.dedup_window_minutes):
            logger.debug("Semantic dedup suppressed: %s/%s", event.source, key[1])
            return prior[1]
        self._dedup_log[key] = (now, event.event_id)
        return None

    def is_duplicate(self, event: StoredEvent) -> bool:
        return self.semantic_duplicate_predecessor(event) is not None

    def is_quiet_hours(self, at: datetime | None = None) -> bool:
        """Check if current time is in configured quiet hours."""
        policy = get_policy_config().suppression
        now_pacific = (at or datetime.now(UTC)).astimezone(PACIFIC)
        hour = now_pacific.hour
        if policy.quiet_start_hour == policy.quiet_end_hour:
            return False
        if policy.quiet_start_hour < policy.quiet_end_hour:
            return policy.quiet_start_hour <= hour < policy.quiet_end_hour
        return hour >= policy.quiet_start_hour or hour < policy.quiet_end_hour

    def next_quiet_end(self, at: datetime | None = None) -> datetime:
        """Return the next configured quiet-hours end in UTC."""
        policy = get_policy_config().suppression
        now = (at or datetime.now(UTC)).astimezone(PACIFIC)
        candidate = now.replace(
            hour=policy.quiet_end_hour,
            minute=0,
            second=0,
            microsecond=0,
        )
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate.astimezone(UTC)

    def queue_for_morning(self, event: StoredEvent) -> bool:
        """Queue an event for delivery when quiet hours end."""
        policy = get_policy_config().suppression
        if len(self._quiet_queue) >= policy.max_quiet_queue:
            logger.warning(
                "Quiet queue full (%d), dropping event %s",
                policy.max_quiet_queue,
                event.event_id,
            )
            return False
        self._quiet_queue.append(event)
        logger.info("Queued event %s for morning delivery", event.event_id)
        return True

    def drain_quiet_queue(self) -> list[StoredEvent]:
        """Return and clear all queued events. Called when quiet hours end."""
        events = list(self._quiet_queue)
        self._quiet_queue.clear()
        return events

    def _prune_old(self, timestamps: list[datetime], window: timedelta) -> list[datetime]:
        """Remove timestamps older than window."""
        cutoff = datetime.now(UTC) - window
        return [t for t in timestamps if t > cutoff]

    def check_push_rate(self) -> bool:
        """Return True if a push notification is allowed under rate limit."""
        policy = get_policy_config().suppression
        self._push_times = self._prune_old(self._push_times, timedelta(hours=1))
        if len(self._push_times) >= policy.max_push_per_hour:
            logger.debug("Push rate limit reached (%d/hr)", policy.max_push_per_hour)
            return False
        return True

    @staticmethod
    def _next_rate_available(timestamps: list[datetime], limit: int) -> datetime:
        """Return when one rate-limit slot is guaranteed to be available."""
        now = datetime.now(UTC)
        recent = sorted(timestamp for timestamp in timestamps if timestamp > now - timedelta(hours=1))
        if len(recent) < limit:
            return now
        if limit <= 0:
            return now + timedelta(hours=1)
        # If policy tightened below the restored history count, wait until enough
        # entries expire to leave one slot rather than immediately churning backlog.
        return recent[len(recent) - limit] + timedelta(hours=1, microseconds=1)

    def next_push_rate_available(self) -> datetime:
        """Return the next time a durable push attempt may proceed."""
        policy = get_policy_config().suppression
        return self._next_rate_available(self._push_times, policy.max_push_per_hour)

    def record_push(self) -> None:
        """Record a push notification send."""
        self.record_push_at(datetime.now(UTC))

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

    def next_slack_rate_available(self) -> datetime:
        """Return the next time a durable Slack attempt may proceed."""
        policy = get_policy_config().suppression
        return self._next_rate_available(self._slack_times, policy.max_slack_per_hour)

    def record_slack(self) -> None:
        """Record a Slack message send."""
        self.record_slack_at(datetime.now(UTC))

    def record_slack_at(self, at: datetime) -> None:
        """Record a Slack message at a specific time."""
        self._slack_times.append(at)

    def clear_rate_history(self) -> None:
        """Clear delivery history for both channels."""
        self._push_times.clear()
        self._slack_times.clear()

    def restore_rate_history(
        self, *, push_times: tuple[datetime, ...], slack_times: tuple[datetime, ...]
    ) -> None:
        """Restore recent durable acceptance times after a process restart."""
        self._push_times = self._prune_old(list(push_times), timedelta(hours=1))
        self._slack_times = self._prune_old(list(slack_times), timedelta(hours=1))

    def add_to_overflow(self, event: StoredEvent) -> bool:
        """Add an event to the overflow buffer for later digest delivery."""
        policy = get_policy_config().suppression
        if len(self._overflow_buffer) >= policy.max_overflow_buffer:
            logger.warning(
                "Overflow buffer full (%d), dropping event %s",
                policy.max_overflow_buffer,
                event.event_id,
            )
            return False
        self._overflow_buffer.append(event)
        logger.debug(
            "Event %s added to overflow buffer (%d total)",
            event.event_id,
            len(self._overflow_buffer),
        )
        return True

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
