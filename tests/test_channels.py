"""Tests for JSONL logging channel."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from notification_hub.channels import read_jsonl, write_jsonl
from notification_hub.models import StoredEvent
import notification_hub.channels as channels_mod


@pytest.fixture
def tmp_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log_dir = tmp_path / "notification-hub"
    log_file = log_dir / "events.jsonl"
    monkeypatch.setattr(channels_mod, "EVENTS_DIR", log_dir)
    monkeypatch.setattr(channels_mod, "EVENTS_LOG", log_file)
    return log_file


def _make_event(**overrides: object) -> StoredEvent:
    defaults = {
        "source": "cc",
        "level": "info",
        "title": "Test",
        "body": "Test body",
    }
    defaults.update(overrides)
    return StoredEvent(**defaults)  # type: ignore[arg-type]


def test_write_creates_directory(tmp_log: Path) -> None:
    assert not tmp_log.parent.exists()
    event = _make_event()
    write_jsonl(event)
    assert tmp_log.parent.exists()
    assert tmp_log.exists()


def test_write_appends_valid_json(tmp_log: Path) -> None:
    e1 = _make_event(title="First")
    e2 = _make_event(title="Second")
    write_jsonl(e1)
    write_jsonl(e2)
    lines = tmp_log.read_text().strip().split("\n")
    assert len(lines) == 2
    parsed = json.loads(lines[0])
    assert parsed["title"] == "First"
    parsed2 = json.loads(lines[1])
    assert parsed2["title"] == "Second"


def test_read_jsonl_empty(tmp_log: Path) -> None:
    events = read_jsonl(tmp_log)
    assert events == []


def test_read_jsonl_roundtrip(tmp_log: Path) -> None:
    original = _make_event(title="Roundtrip", project="test-proj")
    write_jsonl(original)
    events = read_jsonl(tmp_log)
    assert len(events) == 1
    assert events[0].title == "Roundtrip"
    assert events[0].project == "test-proj"
    assert events[0].event_id == original.event_id


def test_stored_event_has_ids() -> None:
    event = _make_event()
    assert len(event.event_id) == 12
    assert event.received_at is not None
