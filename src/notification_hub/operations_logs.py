"""Runtime log parsing helpers for notification-hub operations."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from notification_hub.coordination import infer_intent
from notification_hub.models import StoredEvent
from notification_hub.operations_types import DaemonLogSummary, RecentEventReport

_EVENT_ACCESS_RE = re.compile(r'"POST /events HTTP/1\.1" (?P<status>\d{3})')
_DAEMON_START_MARKERS = (
    "INFO:     Started server process",
    "INFO:     Uvicorn running on ",
)
_SLACK_DELIVERY_FAILURE_PREFIXES = (
    "Slack send failed",
    "Slack digest failed",
    "Slack webhook returned",
    "Slack digest webhook returned",
)
_SLACK_FAILURE_CONTEXT_PREFIXES = (
    *_SLACK_DELIVERY_FAILURE_PREFIXES,
    "Slack delivery failed",
    "Failed to flush overflow digest",
)
_SLACK_SEND_FAILURE_EVENT_RE = re.compile(r"^Slack send failed for (?P<event_id>[0-9a-f]{12}):")
_SLACK_WEBHOOK_FAILURE_EVENT_RE = re.compile(
    r"^Slack webhook returned \d+ for (?P<event_id>[0-9a-f]{12})"
)


def tail_text_file(path: Path, *, lines: int) -> list[str]:
    if lines <= 0 or not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return [line.rstrip("\n") for line in handle.readlines()[-lines:]]


def _lines_since_latest_daemon_start(lines: list[str]) -> list[str]:
    """Return log lines scoped to the latest visible daemon start marker."""
    latest_start_index: int | None = None
    for index, line in enumerate(lines):
        if any(line.startswith(marker) for marker in _DAEMON_START_MARKERS):
            latest_start_index = index
    if latest_start_index is None:
        return lines
    return lines[latest_start_index + 1 :]


def _is_slack_failure_line(line: str) -> bool:
    return any(line.startswith(prefix) for prefix in _SLACK_DELIVERY_FAILURE_PREFIXES)


def _is_slack_failure_context_line(line: str) -> bool:
    return any(line.startswith(prefix) for prefix in _SLACK_FAILURE_CONTEXT_PREFIXES)


def _slack_failure_event_id(line: str) -> str | None:
    for pattern in (_SLACK_SEND_FAILURE_EVENT_RE, _SLACK_WEBHOOK_FAILURE_EVENT_RE):
        match = pattern.match(line)
        if match is not None:
            return match.group("event_id")
    return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _current_slack_delivery_failures(
    stderr_tail: list[str],
    *,
    event_timestamps: dict[str, datetime] | None,
    slack_success_at: datetime | None,
) -> list[str]:
    failures = [
        (index, line, _slack_failure_event_id(line))
        for index, line in enumerate(stderr_tail)
        if _is_slack_failure_line(line)
    ]
    if slack_success_at is None:
        return [line for _, line, _ in failures]

    success_at = _as_utc(slack_success_at)
    latest_unrelated_index = max(
        (
            index
            for index, line in enumerate(stderr_tail)
            if not _is_slack_failure_context_line(line)
        ),
        default=-1,
    )
    current_event_failure_indexes: set[int] = set()
    for index, _, event_id in failures:
        if event_id is None or event_timestamps is None:
            continue
        event_at = event_timestamps.get(event_id)
        if event_at is not None and _as_utc(event_at) >= success_at:
            current_event_failure_indexes.add(index)

    current_failures: list[str] = []
    for index, line, event_id in failures:
        if index in current_event_failure_indexes:
            current_failures.append(line)
            continue
        if event_id is None:
            if current_event_failure_indexes or index > latest_unrelated_index:
                current_failures.append(line)
            continue
        if event_timestamps is None or event_id not in event_timestamps:
            if index > latest_unrelated_index:
                current_failures.append(line)

    return current_failures


def summarize_daemon_logs(
    stdout_tail: list[str],
    stderr_tail: list[str],
    *,
    event_timestamps: dict[str, datetime] | None = None,
    slack_success_at: datetime | None = None,
) -> DaemonLogSummary:
    status_counts: dict[str, int] = {}
    for line in stdout_tail:
        match = _EVENT_ACCESS_RE.search(line)
        if match is None:
            continue
        status = match.group("status")
        status_counts[status] = status_counts.get(status, 0) + 1

    current_stderr_tail = _lines_since_latest_daemon_start(stderr_tail)
    validation_errors = [
        line for line in current_stderr_tail if line.startswith("Rejected event payload")
    ]
    slack_delivery_failures = _current_slack_delivery_failures(
        current_stderr_tail,
        event_timestamps=event_timestamps,
        slack_success_at=slack_success_at,
    )
    return {
        "access_status_counts": status_counts,
        "accepted_event_posts": sum(
            count for status, count in status_counts.items() if status.startswith("2")
        ),
        "rejected_event_posts": status_counts.get("422", 0),
        "validation_error_count": len(validation_errors),
        "recent_validation_errors": validation_errors[-5:],
        "slack_delivery_failure_count": len(slack_delivery_failures),
        "recent_slack_delivery_failures": slack_delivery_failures[-5:],
    }


def event_report(event: StoredEvent) -> RecentEventReport:
    return {
        "event_id": event.event_id,
        "timestamp": event.timestamp.isoformat(),
        "source": event.source,
        "level": event.level,
        "classified_level": event.classified_level,
        "project": event.project,
        "session_label": event.session_label,
        "title": event.title,
        "body": event.body,
        "intent": infer_intent(event),
    }
