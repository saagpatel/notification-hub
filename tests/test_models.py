"""Unit tests for notification_hub.models field validators."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from notification_hub.models import Event


def _event(**overrides: object) -> Event:
    payload: dict[str, object] = {
        "source": "cc",
        "level": "info",
        "title": "t",
        "body": "b",
    }
    payload.update(overrides)
    return Event(**payload)  # type: ignore[arg-type]


class TestSourceAliasNormalization:
    """F3: the hub accepts bridge-db's underscore caller ids and normalizes them
    to its canonical hyphenated source names, instead of hard-failing with a 422."""

    def test_bridge_underscore_ids_normalize_to_hyphen_form(self) -> None:
        assert _event(source="personal_ops").source == "personal-ops"
        assert _event(source="notion_os").source == "notion-os"

    def test_canonical_hyphen_form_passes_through(self) -> None:
        assert _event(source="personal-ops").source == "personal-ops"
        assert _event(source="notion-os").source == "notion-os"

    def test_underscore_bearing_valid_sources_are_not_corrupted(self) -> None:
        # claude_ai and the internal bridge_watcher are valid Source values *with*
        # underscores; a blanket "_"->"-" replace would wrongly invalidate them, so
        # normalization must go strictly through the explicit alias map.
        assert _event(source="claude_ai").source == "claude_ai"
        assert _event(source="bridge_watcher").source == "bridge_watcher"

    def test_unknown_source_still_rejected(self) -> None:
        # The normalizer only widens the accepted set by the known aliases; junk
        # still falls through to the Source Literal and 422s.
        with pytest.raises(ValidationError):
            _event(source="totally_unknown")
