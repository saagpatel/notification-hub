"""Shared test isolation for local runtime state."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import notification_hub.channels as channels_mod
import notification_hub.config as config_mod
import notification_hub.operations as operations_mod
import notification_hub.server as server_mod
import notification_hub.watcher as watcher_mod
from notification_hub.config import clear_policy_cache, clear_webhook_cache
from notification_hub.pipeline import reset_suppression_engine


@pytest.fixture(autouse=True)
def isolate_runtime_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep tests away from the real machine event log, bridge file, and server globals."""
    events_dir = tmp_path / "notification-hub"
    events_log = events_dir / "events.jsonl"
    bridge_dir = tmp_path / "bridge"
    bridge_dir.mkdir()
    bridge_file = bridge_dir / "claude_ai_context.md"
    bridge_file.write_text("# Test Bridge\n", encoding="utf-8")

    monkeypatch.setattr(config_mod, "EVENTS_DIR", events_dir)
    monkeypatch.setattr(config_mod, "EVENTS_LOG", events_log)
    monkeypatch.setattr(config_mod, "POLICY_CONFIG", tmp_path / "config.toml")
    monkeypatch.setattr(config_mod, "LAUNCH_AGENT_PLIST", tmp_path / "com.saagar.notification-hub.plist")
    monkeypatch.setattr(channels_mod, "EVENTS_DIR", events_dir)
    monkeypatch.setattr(channels_mod, "EVENTS_LOG", events_log)
    monkeypatch.setattr(operations_mod, "EVENTS_DIR", events_dir)
    monkeypatch.setattr(operations_mod, "EVENTS_LOG", events_log)

    monkeypatch.setattr(config_mod, "BRIDGE_FILE", bridge_file)
    monkeypatch.setattr(server_mod, "BRIDGE_FILE", bridge_file)
    monkeypatch.setattr(watcher_mod, "BRIDGE_FILE", bridge_file)

    monkeypatch.setattr(server_mod, "_event_count", 0)
    monkeypatch.setattr(server_mod, "_observer", None)
    monkeypatch.setattr(server_mod, "_start_time", 0.0)

    clear_webhook_cache()
    clear_policy_cache()
    reset_suppression_engine()
    yield
    clear_webhook_cache()
    clear_policy_cache()
