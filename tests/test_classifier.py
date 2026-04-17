"""Tests for the urgency classification rules engine."""

from __future__ import annotations

import pytest

from notification_hub.config import ClassificationPolicy, PolicyConfig
from notification_hub.classifier import classify
from notification_hub.models import Event, Level


def _event(title: str = "Test", body: str = "Test body", level: Level = "info") -> Event:
    return Event(
        source="cc",
        level=level,
        title=title,
        body=body,
    )


class TestUrgentClassification:
    def test_verification_failure(self) -> None:
        assert classify(_event(body="Build verification failed on main")) == "urgent"

    def test_test_regression(self) -> None:
        assert classify(_event(body="Test regression detected in auth module")) == "urgent"

    def test_eval_degradation(self) -> None:
        assert classify(_event(body="Eval degradation: pass rate dropped 15%")) == "urgent"

    def test_approval_needed(self) -> None:
        assert classify(_event(body="Mail draft approval needed")) == "urgent"

    def test_security_finding(self) -> None:
        assert classify(_event(body="Semgrep security finding in api/routes.py")) == "urgent"

    def test_can_auto_archive_false(self) -> None:
        assert (
            classify(_event(body="Portfolio review: can_auto_archive=false for 3 repos"))
            == "urgent"
        )

    def test_action_required(self) -> None:
        assert classify(_event(body="Codex action required: PR merge conflict")) == "urgent"


class TestNormalClassification:
    def test_session_complete(self) -> None:
        assert classify(_event(body="Claude Code session complete for ink")) == "normal"

    def test_automation_report(self) -> None:
        assert classify(_event(body="Weekly automation report generated")) == "normal"

    def test_milestone(self) -> None:
        assert classify(_event(body="Phase 2 milestone reached")) == "normal"

    def test_shipped_tag(self) -> None:
        assert classify(_event(body="[SHIPPED] Chromafield v1.0 released")) == "normal"

    def test_deployed(self) -> None:
        assert classify(_event(body="HowMoneyMoves deployed to Vercel")) == "normal"

    def test_submitted_to_app_store(self) -> None:
        assert classify(_event(body="Ghost Routes submitted to App Store")) == "normal"


class TestInfoClassification:
    def test_can_auto_archive_true(self) -> None:
        assert classify(_event(body="Portfolio review: can_auto_archive=true, all clean")) == "info"

    def test_status_update(self) -> None:
        assert classify(_event(body="Routine status update from Codex")) == "info"

    def test_bridge_file_read(self) -> None:
        assert classify(_event(body="Bridge file read by weekly-review")) == "info"

    def test_info_keyword_overrides_normal_keyword(self) -> None:
        assert classify(_event(body="Session complete status update")) == "info"


class TestFallback:
    def test_falls_back_to_source_level(self) -> None:
        event = _event(title="Unknown pattern", body="Something happened", level="normal")
        assert classify(event) == "normal"

    def test_falls_back_info(self) -> None:
        event = _event(title="Misc", body="Nothing matches", level="info")
        assert classify(event) == "info"

    def test_urgent_overrides_source_level(self) -> None:
        event = _event(body="Verification failed in tests", level="info")
        assert classify(event) == "urgent"

    def test_keyword_in_title_only(self) -> None:
        event = _event(title="Approval needed for draft", body="No keywords here")
        assert classify(event) == "urgent"

    def test_normal_keyword_in_title_only(self) -> None:
        event = _event(title="Session complete", body="No keywords here")
        assert classify(event) == "normal"


def test_uses_policy_config_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = PolicyConfig(
        classification=ClassificationPolicy(
            urgent_keywords=("database down",),
            normal_keywords=("ship it",),
            info_keywords=("routine ping",),
        )
    )
    monkeypatch.setattr("notification_hub.classifier.get_policy_config", lambda: policy)

    assert classify(_event(body="Database down in production")) == "urgent"
    assert classify(_event(body="Ship it before lunch")) == "normal"
    assert classify(_event(body="Routine ping from watchdog")) == "info"
