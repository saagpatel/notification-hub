"""Runtime log parsing helpers for notification-hub operations."""

from __future__ import annotations

import re
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


def summarize_daemon_logs(stdout_tail: list[str], stderr_tail: list[str]) -> DaemonLogSummary:
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
    slack_delivery_failures = [
        line
        for line in current_stderr_tail
        if any(line.startswith(prefix) for prefix in _SLACK_DELIVERY_FAILURE_PREFIXES)
    ]
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
        "title": event.title,
        "body": event.body,
        "intent": infer_intent(event),
    }
