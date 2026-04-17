"""FastAPI server — event intake, health check, bridge file watcher lifecycle."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TypedDict, cast

from fastapi import FastAPI

from notification_hub.config import BRIDGE_FILE, get_policy_config
from notification_hub.diagnostics import collect_runtime_readiness
from notification_hub.models import Event, EventResponse
from notification_hub.operations import run_retention
from notification_hub.pipeline import get_suppression_engine, process_event
from notification_hub.watcher import ObserverHandle, start_watcher

logger = logging.getLogger(__name__)

_start_time: float = 0.0
_event_count: int = 0
_observer: ObserverHandle | None = None
_retention_task: asyncio.Task[None] | None = None


class RetentionRuntimeStatus(TypedDict):
    enabled: bool
    interval_minutes: int
    max_events: int
    keep_archives: int
    last_checked_at: str | None
    last_status: str | None
    last_rotated: bool
    last_archive_path: str | None


_retention_status: RetentionRuntimeStatus = {
    "enabled": False,
    "interval_minutes": 0,
    "max_events": 0,
    "keep_archives": 0,
    "last_checked_at": None,
    "last_status": None,
    "last_rotated": False,
    "last_archive_path": None,
}


def reset_retention_runtime_state() -> None:
    global _retention_status
    _retention_status = {
        "enabled": False,
        "interval_minutes": 0,
        "max_events": 0,
        "keep_archives": 0,
        "last_checked_at": None,
        "last_status": None,
        "last_rotated": False,
        "last_archive_path": None,
    }


def get_retention_runtime_status() -> RetentionRuntimeStatus:
    return cast(RetentionRuntimeStatus, dict(_retention_status))


def _configure_retention_status() -> None:
    global _retention_status
    policy = get_policy_config().retention
    _retention_status = {
        "enabled": policy.enabled,
        "interval_minutes": policy.interval_minutes,
        "max_events": policy.max_events,
        "keep_archives": policy.keep_archives,
        "last_checked_at": None,
        "last_status": None,
        "last_rotated": False,
        "last_archive_path": None,
    }


def run_retention_check_once() -> None:
    global _retention_status
    policy = get_policy_config().retention
    _retention_status["enabled"] = policy.enabled
    _retention_status["interval_minutes"] = policy.interval_minutes
    _retention_status["max_events"] = policy.max_events
    _retention_status["keep_archives"] = policy.keep_archives

    report = run_retention(
        max_events=policy.max_events,
        keep_archives=policy.keep_archives,
    )
    checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _retention_status["last_checked_at"] = checked_at
    _retention_status["last_status"] = report["status"]
    _retention_status["last_rotated"] = report["rotated"]
    _retention_status["last_archive_path"] = report["archive_path"]


async def _retention_loop() -> None:
    while True:
        policy = get_policy_config().retention
        _retention_status["enabled"] = policy.enabled
        _retention_status["interval_minutes"] = policy.interval_minutes
        _retention_status["max_events"] = policy.max_events
        _retention_status["keep_archives"] = policy.keep_archives
        if policy.enabled:
            run_retention_check_once()
        await asyncio.sleep(policy.interval_minutes * 60)


def _handle_bridge_event(event: Event) -> None:
    """Callback for bridge file watcher — processes events through the full pipeline."""
    global _event_count
    process_event(event)
    _event_count += 1


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start bridge watcher on startup, stop on shutdown."""
    global _start_time, _observer, _retention_task
    _start_time = time.monotonic()
    _configure_retention_status()

    if BRIDGE_FILE.parent.exists():
        _observer = start_watcher(_handle_bridge_event)
        logger.info("Bridge file watcher active")
    else:
        logger.warning("Bridge file directory not found, watcher disabled")

    _retention_task = asyncio.create_task(_retention_loop())

    yield

    retention_task = _retention_task
    assert retention_task is not None
    retention_task.cancel()
    try:
        await retention_task
    except asyncio.CancelledError:
        pass
    logger.info("Retention loop stopped")
    _retention_task = None

    if _observer is not None:
        _observer.stop()
        _observer.join(timeout=5)
        logger.info("Bridge file watcher stopped")
        _observer = None

    reset_retention_runtime_state()


app = FastAPI(
    title="Notification Hub",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.post("/events", response_model=EventResponse, status_code=201)
async def create_event(event: Event) -> EventResponse:
    """Accept a notification event, classify it, route to channels, and confirm."""
    global _event_count
    stored = process_event(event)
    _event_count += 1
    return EventResponse(
        event_id=stored.event_id,
        level=stored.classified_level or stored.level,
    )


@app.get("/health")
async def health() -> dict[str, object]:
    """Server health check."""
    uptime = time.monotonic() - _start_time if _start_time else 0
    return {
        "status": "ok",
        "uptime_seconds": round(uptime, 1),
        "events_processed": _event_count,
        "watcher_active": _observer is not None,
    }


async def _collect_health_details() -> dict[str, object]:
    """Detailed runtime readiness without exposing secrets."""
    base = await health()
    readiness = collect_runtime_readiness()
    base.update(readiness)
    base["suppression"] = get_suppression_engine().snapshot()
    readiness_retention = readiness["retention"]
    runtime_retention = get_retention_runtime_status()
    assert isinstance(readiness_retention, dict)
    base["retention"] = {
        **readiness_retention,
        "last_checked_at": runtime_retention["last_checked_at"],
        "last_status": runtime_retention["last_status"],
        "last_rotated": runtime_retention["last_rotated"],
        "last_archive_path": runtime_retention["last_archive_path"],
    }
    return base


@app.get("/health/details")
async def health_details() -> dict[str, object]:
    """Detailed runtime readiness without exposing secrets."""
    return await _collect_health_details()
