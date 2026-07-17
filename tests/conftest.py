"""Shared test isolation for local runtime state."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import notification_hub.channels as channels_mod
import notification_hub.config as config_mod
import notification_hub.durable_inbox as durable_inbox_mod
import notification_hub.operations as operations_mod
import notification_hub.pipeline as pipeline_mod
import notification_hub.producer_health as producer_health_mod
import notification_hub.server as server_mod
import notification_hub.watcher as watcher_mod
from notification_hub.config import clear_policy_cache, clear_webhook_cache
from notification_hub.pipeline import reset_suppression_engine


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=server_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def isolate_runtime_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep tests away from the real machine event log, bridge file, and server globals."""
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    monkeypatch.setenv("HOME", str(isolated_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("NOTIFICATION_HUB_TEST_MODE", "1")
    events_dir = tmp_path / "notification-hub"
    events_log = events_dir / "events.jsonl"
    durable_inbox_db = events_dir / "inbox.sqlite3"
    daemon_log_dir = tmp_path / "logs" / "notification-hub"
    bridge_dir = tmp_path / "bridge"
    bridge_dir.mkdir()
    bridge_file = bridge_dir / "claude_ai_context.md"
    bridge_file.write_text("# Test Bridge\n", encoding="utf-8")

    monkeypatch.setattr(config_mod, "EVENTS_DIR", events_dir)
    monkeypatch.setattr(config_mod, "EVENTS_LOG", events_log)
    monkeypatch.setattr(config_mod, "DURABLE_INBOX_DB", durable_inbox_db)
    monkeypatch.setattr(durable_inbox_mod, "DEFAULT_DB_PATH", durable_inbox_db)
    monkeypatch.setattr(
        producer_health_mod, "DEFAULT_PRODUCER_OUTBOX_DB", events_dir / "producer-outbox.sqlite3"
    )
    monkeypatch.setattr(config_mod, "DAEMON_LOG_DIR", daemon_log_dir)
    monkeypatch.setattr(config_mod, "DAEMON_STDOUT_LOG", daemon_log_dir / "stdout.log")
    monkeypatch.setattr(config_mod, "DAEMON_STDERR_LOG", daemon_log_dir / "stderr.log")
    monkeypatch.setattr(config_mod, "POLICY_CONFIG", tmp_path / "config.toml")
    monkeypatch.setattr(
        config_mod, "LAUNCH_AGENT_PLIST", tmp_path / "com.saagar.notification-hub.plist"
    )
    monkeypatch.setattr(config_mod, "CLAUDE_HOOK", tmp_path / "notify.sh")
    monkeypatch.setattr(config_mod, "CODEX_HOOK", tmp_path / "notify_local.py")
    monkeypatch.setattr(config_mod, "CLAUDE_PRODUCER_HELPER", tmp_path / "claude-producer.py")
    monkeypatch.setattr(config_mod, "CODEX_PRODUCER_HELPER", tmp_path / "codex-producer.py")
    monkeypatch.setattr(channels_mod, "EVENTS_DIR", events_dir)
    monkeypatch.setattr(channels_mod, "EVENTS_LOG", events_log)
    monkeypatch.setattr(operations_mod, "EVENTS_DIR", events_dir)
    monkeypatch.setattr(operations_mod, "EVENTS_LOG", events_log)
    monkeypatch.setattr(operations_mod, "DAEMON_STDOUT_LOG", daemon_log_dir / "stdout.log")
    monkeypatch.setattr(operations_mod, "DAEMON_STDERR_LOG", daemon_log_dir / "stderr.log")
    # Fail closed: no test may contact a live notification destination unless it
    # explicitly replaces these transports with an isolated fixture double.
    monkeypatch.setattr(pipeline_mod, "send_push", lambda _event: False)
    monkeypatch.setattr(pipeline_mod, "send_slack", lambda _event: False)
    monkeypatch.setattr(pipeline_mod, "send_slack_digest", lambda _events: False)
    monkeypatch.setattr(operations_mod, "send_push", lambda _event: False)
    monkeypatch.setattr(channels_mod, "get_slack_webhook_url", lambda: None)

    monkeypatch.setattr(config_mod, "BRIDGE_FILE", bridge_file)
    monkeypatch.setattr(server_mod, "BRIDGE_FILE", bridge_file)
    monkeypatch.setattr(watcher_mod, "BRIDGE_FILE", bridge_file)

    monkeypatch.setattr(server_mod, "_event_count", 0)
    monkeypatch.setattr(server_mod, "_observer", None)
    monkeypatch.setattr(server_mod, "_start_time", 0.0)
    monkeypatch.setattr(server_mod, "_retention_task", None)
    monkeypatch.setattr(server_mod, "_durable_inbox_task", None)
    monkeypatch.setattr(server_mod, "_bridge_cursor_task", None)
    server_mod.reset_retention_runtime_state()
    server_mod.reset_bridge_cursor_runtime_status()

    clear_webhook_cache()
    clear_policy_cache()
    reset_suppression_engine()
    yield
    server_mod.reset_retention_runtime_state()
    server_mod.reset_bridge_cursor_runtime_status()
    clear_webhook_cache()
    clear_policy_cache()
