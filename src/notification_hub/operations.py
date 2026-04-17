"""Operator actions beyond diagnostics: smoke checks and log retention."""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from typing import TypedDict

import httpx

from notification_hub.channels import ensure_log_dir, read_jsonl
from notification_hub.config import (
    EVENTS_DIR,
    EVENTS_LOG,
    EXAMPLE_POLICY_CONFIG,
    HOST,
    POLICY_CONFIG,
    PORT,
)
from notification_hub.models import Event


class SmokeReport(TypedDict):
    status: str
    health_url: str
    event_url: str
    event_id: str | None
    log_verified: bool
    response_status: int | None
    error: str | None


class RetentionReport(TypedDict):
    status: str
    rotated: bool
    archive_path: str | None
    events_before: int
    events_after: int
    archived_events: int
    deleted_archives: list[str]


class BootstrapConfigReport(TypedDict):
    status: str
    copied: bool
    config_path: str
    example_path: str
    error: str | None


def run_smoke_check() -> SmokeReport:
    """Post a harmless info event and verify it lands in the live event log."""
    base_url = f"http://{HOST}:{PORT}"
    event_url = f"{base_url}/events"
    health_url = f"{base_url}/health/details"
    payload = Event(
        source="codex",
        level="info",
        title="Notification Hub smoke check",
        body=f"Smoke check at {datetime.now(timezone.utc).isoformat()}",
        project="notification-hub",
    )

    try:
        response = httpx.post(event_url, json=payload.model_dump(mode="json"), timeout=5.0)
        if response.status_code != 201:
            return {
                "status": "degraded",
                "health_url": health_url,
                "event_url": event_url,
                "event_id": None,
                "log_verified": False,
                "response_status": response.status_code,
                "error": f"unexpected status {response.status_code}",
            }

        event_id = response.json().get("event_id")
        log_verified = False
        if isinstance(event_id, str):
            log_verified = any(event.event_id == event_id for event in read_jsonl())

        return {
            "status": "ok" if log_verified else "degraded",
            "health_url": health_url,
            "event_url": event_url,
            "event_id": event_id if isinstance(event_id, str) else None,
            "log_verified": log_verified,
            "response_status": response.status_code,
            "error": None if log_verified else "event not found in log",
        }
    except httpx.HTTPError as exc:
        return {
            "status": "degraded",
            "health_url": health_url,
            "event_url": event_url,
            "event_id": None,
            "log_verified": False,
            "response_status": None,
            "error": str(exc),
        }


def run_retention(*, max_events: int, keep_archives: int) -> RetentionReport:
    """Archive older events when the live JSONL log exceeds the configured size."""
    ensure_log_dir()
    if not EVENTS_LOG.exists():
        return {
            "status": "ok",
            "rotated": False,
            "archive_path": None,
            "events_before": 0,
            "events_after": 0,
            "archived_events": 0,
            "deleted_archives": [],
        }

    with EVENTS_LOG.open("r", encoding="utf-8") as handle:
        lines = handle.readlines()

    events_before = len(lines)
    if events_before <= max_events:
        return {
            "status": "ok",
            "rotated": False,
            "archive_path": None,
            "events_before": events_before,
            "events_after": events_before,
            "archived_events": 0,
            "deleted_archives": [],
        }

    archive_dir = EVENTS_DIR / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    archive_path = archive_dir / f"events-{datetime.now(timezone.utc):%Y%m%d-%H%M%S-%f}.jsonl"

    archived_lines = lines[:-max_events]
    remaining_lines = lines[-max_events:]

    archive_fd = os.open(str(archive_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(archive_fd, "".join(archived_lines).encode("utf-8"))
    finally:
        os.close(archive_fd)

    log_fd = os.open(str(EVENTS_LOG), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(log_fd, "".join(remaining_lines).encode("utf-8"))
    finally:
        os.close(log_fd)

    deleted_archives: list[str] = []
    archive_paths = sorted(archive_dir.glob("events-*.jsonl"))
    if len(archive_paths) > keep_archives:
        for extra in archive_paths[: len(archive_paths) - keep_archives]:
            extra.unlink(missing_ok=True)
            deleted_archives.append(str(extra))

    return {
        "status": "ok",
        "rotated": True,
        "archive_path": str(archive_path),
        "events_before": events_before,
        "events_after": len(remaining_lines),
        "archived_events": len(archived_lines),
        "deleted_archives": deleted_archives,
    }


def bootstrap_policy_config(*, force: bool = False) -> BootstrapConfigReport:
    """Copy the repo sample policy file into the live config location."""
    example_path = EXAMPLE_POLICY_CONFIG
    config_path = POLICY_CONFIG

    if not example_path.exists():
        return {
            "status": "degraded",
            "copied": False,
            "config_path": str(config_path),
            "example_path": str(example_path),
            "error": "sample policy config not found",
        }

    if config_path.exists() and not force:
        return {
            "status": "ok",
            "copied": False,
            "config_path": str(config_path),
            "example_path": str(example_path),
            "error": None,
        }

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(example_path, config_path)
        os.chmod(config_path, 0o600)
    except OSError as exc:
        return {
            "status": "degraded",
            "copied": False,
            "config_path": str(config_path),
            "example_path": str(example_path),
            "error": str(exc),
        }

    return {
        "status": "ok",
        "copied": True,
        "config_path": str(config_path),
        "example_path": str(example_path),
        "error": None,
    }
