"""FastAPI server — event intake, health check, bridge file watcher lifecycle."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from watchdog.observers import Observer

from notification_hub.config import BRIDGE_FILE
from notification_hub.models import Event, EventResponse
from notification_hub.pipeline import process_event
from notification_hub.watcher import start_watcher

logger = logging.getLogger(__name__)

_start_time: float = 0.0
_event_count: int = 0
_observer: Observer | None = None


def _handle_bridge_event(event: Event) -> None:
    """Callback for bridge file watcher — processes events through the full pipeline."""
    global _event_count
    process_event(event)
    _event_count += 1


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start bridge watcher on startup, stop on shutdown."""
    global _start_time, _observer
    _start_time = time.monotonic()

    if BRIDGE_FILE.parent.exists():
        _observer = start_watcher(_handle_bridge_event)
        logger.info("Bridge file watcher active")
    else:
        logger.warning("Bridge file directory not found, watcher disabled")

    yield

    if _observer is not None:
        _observer.stop()
        _observer.join(timeout=5)
        logger.info("Bridge file watcher stopped")


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
