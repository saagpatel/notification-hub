"""Read-only health facts for the hook producer outbox."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import TypedDict

from notification_hub.config import PRODUCER_OUTBOX_DB

DEFAULT_PRODUCER_OUTBOX_DB = PRODUCER_OUTBOX_DB


class ProducerOutboxHealth(TypedDict):
    status: str
    db_path: str
    db_exists: bool
    queued_count: int
    accepted_count: int
    dead_letter_count: int
    rejected_count: int
    oldest_queued_age_seconds: float | None
    total_attempt_count: int
    next_action: str
    error: str | None


def collect_producer_health(path: Path | None = None) -> ProducerOutboxHealth:
    path = path or DEFAULT_PRODUCER_OUTBOX_DB
    if not path.exists():
        return {
            "status": "ok",
            "db_path": str(path),
            "db_exists": False,
            "queued_count": 0,
            "accepted_count": 0,
            "dead_letter_count": 0,
            "rejected_count": 0,
            "oldest_queued_age_seconds": None,
            "total_attempt_count": 0,
            "next_action": "Producer outbox has no events yet.",
            "error": None,
        }
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
            row = conn.execute(
                "SELECT "
                "SUM(CASE WHEN state = 'queued' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN state = 'accepted' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN state = 'dead_lettered' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN state = 'rejected' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN state = 'dead_lettered' AND terminal_disposition IS NULL "
                "THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN state = 'rejected' AND terminal_disposition IS NULL "
                "THEN 1 ELSE 0 END), "
                "MIN(CASE WHEN state = 'queued' THEN created_at END), "
                "COALESCE(SUM(attempt_count), 0) FROM producer_events"
            ).fetchone()
    except sqlite3.Error as exc:
        return {
            "status": "degraded",
            "db_path": str(path),
            "db_exists": True,
            "queued_count": 0,
            "accepted_count": 0,
            "dead_letter_count": 0,
            "rejected_count": 0,
            "oldest_queued_age_seconds": None,
            "total_attempt_count": 0,
            "next_action": "Inspect the producer outbox schema and preserve its rows.",
            "error": str(exc),
        }
    queued = int(row[0] or 0)
    accepted = int(row[1] or 0)
    dead = int(row[2] or 0)
    rejected = int(row[3] or 0)
    unresolved_dead = int(row[4] or 0)
    unresolved_rejected = int(row[5] or 0)
    oldest = float(row[6]) if row[6] is not None else None
    age = max(0.0, time.time() - oldest) if oldest is not None else None
    return {
        "status": "degraded" if queued or unresolved_dead or unresolved_rejected else "ok",
        "db_path": str(path),
        "db_exists": True,
        "queued_count": queued,
        "accepted_count": accepted,
        "dead_letter_count": dead,
        "rejected_count": rejected,
        "oldest_queued_age_seconds": age,
        "total_attempt_count": int(row[7] or 0),
        "next_action": (
            "Retry queued producer events and verify hub acceptance receipts."
            if queued
            else "Disposition rejected or dead-lettered producer events without deleting history."
            if unresolved_dead or unresolved_rejected
            else "Producer outbox is clear."
        ),
        "error": None,
    }
