"""notification-hub MCP server.

Thin stdio MCP wrapper around the notification-hub HTTP daemon at
http://127.0.0.1:9199. Exposes event ingestion plus health/review reads
so Hermes (and any other MCP client) can interact with the daemon.

The notification-hub daemon must be running locally — this wrapper is
HTTP-only and does not start the daemon itself.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastmcp import FastMCP

BASE_URL = os.environ.get("NOTIFICATION_HUB_URL", "http://127.0.0.1:9199")
TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=15.0)

mcp = FastMCP("notification-hub-mcp")


async def _get(path: str) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
        response = await client.get(path)
        response.raise_for_status()
        return response.json()


async def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
        response = await client.post(path, json=payload)
        response.raise_for_status()
        return response.json()


@mcp.tool
async def post_event(
    source: str,
    level: str,
    title: str,
    body: str,
    project: str | None = None,
    intent: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Push an event into notification-hub.

    Args:
        source: One of "codex", "cc", "claude_ai", "bridge_watcher", "personal-ops", "notion-os".
        level: One of "urgent", "normal", "info".
        title: Short headline (1-200 chars).
        body: Event body (1-2000 chars).
        project: Optional project tag.
        intent: Optional intent ("needs_attention", "blocked", "waiting_on_user",
            "ready_to_review", "ready_to_merge", "handoff_created", "automation_failed",
            "completed").
        context: Optional flat dict of additional context values.

    Returns the daemon's EventResponse (event id, timestamp, suppression status).
    """
    payload: dict[str, Any] = {
        "source": source,
        "level": level,
        "title": title,
        "body": body,
    }
    if project is not None:
        payload["project"] = project
    if intent is not None:
        payload["intent"] = intent
    if context:
        payload["context"] = context
    return await _post("/events", payload)


@mcp.tool
async def get_health() -> dict[str, Any]:
    """Return the daemon's basic health status."""
    return await _get("/health")


@mcp.tool
async def get_health_details() -> dict[str, Any]:
    """Return the daemon's detailed health snapshot — uptime, events processed,
    delivery channel status, retention state, runtime wiring, and suppression
    counters."""
    return await _get("/health/details")


@mcp.tool
async def get_review_data() -> dict[str, Any]:
    """Return the operator review JSON — the data that backs the /review HTML page."""
    return await _get("/review/data")


@mcp.tool
async def get_coordination_readiness() -> dict[str, Any]:
    """Return the coordination-readiness summary used by the operator console."""
    return await _get("/review/coordination-readiness")


@mcp.tool
async def get_import_queue() -> dict[str, Any]:
    """Return the current notification-hub import queue (pending action proposals
    awaiting operator review)."""
    return await _get("/review/import-queue")


@mcp.tool
async def get_burn_in_reports() -> dict[str, Any]:
    """Return the list of available burn-in reports for the operator review console."""
    return await _get("/review/burn-in-reports")


if __name__ == "__main__":
    mcp.run(transport="stdio")
