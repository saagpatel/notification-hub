"""Smoke tests for notification-hub-mcp server.

These tests run entirely in-process using FastMCP's Client transport — no live
daemon is required.  HTTP calls are intercepted by patching the module-level
`_get` / `_post` helpers so we can verify each tool routes to the correct path
and builds the correct payload.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastmcp import Client

from server import mcp

EXPECTED_TOOLS = {
    "post_event",
    "get_health",
    "get_health_details",
    "get_review_data",
    "get_coordination_readiness",
    "get_import_queue",
    "get_burn_in_reports",
}


async def test_all_tools_registered():
    """All 7 tools must be discoverable via list_tools."""
    async with Client(mcp) as client:
        tools = await client.list_tools()
    names = {t.name for t in tools}
    assert names == EXPECTED_TOOLS


async def test_get_health():
    with patch("server._get", new=AsyncMock(return_value={"status": "healthy"})) as m:
        async with Client(mcp) as client:
            await client.call_tool("get_health", {})
    m.assert_called_once_with("/health")


async def test_get_health_details():
    with patch("server._get", new=AsyncMock(return_value={})) as m:
        async with Client(mcp) as client:
            await client.call_tool("get_health_details", {})
    m.assert_called_once_with("/health/details")


async def test_get_review_data():
    with patch("server._get", new=AsyncMock(return_value={})) as m:
        async with Client(mcp) as client:
            await client.call_tool("get_review_data", {})
    m.assert_called_once_with("/review/data")


async def test_get_coordination_readiness():
    with patch("server._get", new=AsyncMock(return_value={})) as m:
        async with Client(mcp) as client:
            await client.call_tool("get_coordination_readiness", {})
    m.assert_called_once_with("/review/coordination-readiness")


async def test_get_import_queue():
    with patch("server._get", new=AsyncMock(return_value={})) as m:
        async with Client(mcp) as client:
            await client.call_tool("get_import_queue", {})
    m.assert_called_once_with("/review/import-queue")


async def test_get_burn_in_reports():
    with patch("server._get", new=AsyncMock(return_value={})) as m:
        async with Client(mcp) as client:
            await client.call_tool("get_burn_in_reports", {})
    m.assert_called_once_with("/review/burn-in-reports")


async def test_post_event_required_fields_only():
    """post_event with only required args sends a minimal payload."""
    fake = {"event_id": "abc123", "suppressed": False}
    with patch("server._post", new=AsyncMock(return_value=fake)) as m:
        async with Client(mcp) as client:
            await client.call_tool(
                "post_event",
                {"source": "cc", "level": "info", "title": "t", "body": "b"},
            )
    m.assert_called_once_with(
        "/events",
        {"source": "cc", "level": "info", "title": "t", "body": "b"},
    )


async def test_post_event_optional_fields_included():
    """post_event with all optional args passes project, intent, and context."""
    fake = {"event_id": "xyz", "suppressed": False}
    with patch("server._post", new=AsyncMock(return_value=fake)) as m:
        async with Client(mcp) as client:
            await client.call_tool(
                "post_event",
                {
                    "source": "codex",
                    "level": "urgent",
                    "title": "deploy done",
                    "body": "v2 deployed",
                    "project": "myapp",
                    "intent": "completed",
                    "context": {"env": "prod"},
                },
            )
    m.assert_called_once_with(
        "/events",
        {
            "source": "codex",
            "level": "urgent",
            "title": "deploy done",
            "body": "v2 deployed",
            "project": "myapp",
            "intent": "completed",
            "context": {"env": "prod"},
        },
    )
