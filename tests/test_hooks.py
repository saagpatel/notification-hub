"""Tests for hook integration: verify payloads match the Event schema."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from notification_hub.models import Event


class TestClaudeCodeHookPayload:
    """Verify the JSON shape that notify.sh POSTs matches the Event model."""

    def test_standard_payload(self) -> None:
        # Simulates the curl -d payload from notify.sh
        payload = {
            "source": "cc",
            "level": "normal",
            "title": "Session Complete",
            "body": "notification-hub (feat/phase-3): Done (120s)",
            "project": "notification-hub",
        }
        event = Event.model_validate(payload)
        assert event.source == "cc"
        assert event.level == "normal"
        assert event.project == "notification-hub"

    def test_no_branch_payload(self) -> None:
        payload = {
            "source": "cc",
            "level": "normal",
            "title": "Session Complete",
            "body": "some-repo: Done (45s)",
            "project": "some-repo",
        }
        event = Event.model_validate(payload)
        assert event.source == "cc"

    def test_short_session_still_valid(self) -> None:
        payload = {
            "source": "cc",
            "level": "normal",
            "title": "Session Complete",
            "body": "ink: Done (31s)",
            "project": "ink",
        }
        event = Event.model_validate(payload)
        assert event.project == "ink"


class TestCodexHookPayload:
    """Verify the JSON shape that notify_local.py POSTs matches the Event model."""

    def test_waiting_payload(self) -> None:
        payload = {
            "source": "codex",
            "level": "urgent",  # waiting maps to urgent
            "title": "Codex is waiting",
            "body": "Codex is waiting for your response.",
        }
        event = Event.model_validate(payload)
        assert event.source == "codex"
        assert event.level == "urgent"

    def test_attention_payload(self) -> None:
        payload = {
            "source": "codex",
            "level": "normal",  # attention maps to normal
            "title": "Codex needs attention",
            "body": "A verification or runtime issue needs review.",
        }
        event = Event.model_validate(payload)
        assert event.source == "codex"
        assert event.level == "normal"

    def test_complete_payload(self) -> None:
        payload = {
            "source": "codex",
            "level": "normal",  # complete maps to normal
            "title": "Codex finished a turn",
            "body": "A Codex turn completed.",
        }
        event = Event.model_validate(payload)
        assert event.source == "codex"
        assert event.level == "normal"

    @pytest.mark.parametrize(
        "codex_level,expected_hub_level",
        [
            ("waiting", "urgent"),
            ("attention", "normal"),
            ("complete", "normal"),
        ],
    )
    def test_codex_level_mapping_produces_valid_event(
        self, codex_level: str, expected_hub_level: str
    ) -> None:
        """Verify the mapping logic from notify_local.py produces valid Event payloads."""
        # Replicate the actual mapping from notify_local.py post_to_hub()
        hub_level = "urgent" if codex_level == "waiting" else "normal"
        assert hub_level == expected_hub_level
        # Validate the mapped level is accepted by the Event model
        event = Event.model_validate(
            {
                "source": "codex",
                "level": hub_level,
                "title": f"Codex {codex_level}",
                "body": f"Codex event at level {codex_level}",
            }
        )
        assert event.level == expected_hub_level


class TestPayloadEdgeCases:
    def test_special_chars_in_repo_name(self) -> None:
        payload = {
            "source": "cc",
            "level": "normal",
            "title": "Session Complete",
            "body": "my-cool-project_v2 (fix/weird-bug): Done (60s)",
            "project": "my-cool-project_v2",
        }
        event = Event.model_validate(payload)
        assert event.project == "my-cool-project_v2"

    def test_long_elapsed_time(self) -> None:
        payload = {
            "source": "cc",
            "level": "normal",
            "title": "Session Complete",
            "body": "big-refactor (feat/migration): Done (3600s)",
            "project": "big-refactor",
        }
        event = Event.model_validate(payload)
        assert "3600s" in event.body
