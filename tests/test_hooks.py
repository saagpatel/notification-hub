"""Tests for repo-owned runtime hook templates."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
import types
from pathlib import Path
from typing import cast
from unittest.mock import patch

from notification_hub.models import Event

REPO_ROOT = Path(__file__).resolve().parents[1]
CLAUDE_TEMPLATE = REPO_ROOT / "ops" / "hooks" / "claude-notify.sh"
CODEX_TEMPLATE = REPO_ROOT / "ops" / "hooks" / "codex-notify-local.py"
LAUNCH_AGENT_TEMPLATE = REPO_ROOT / "ops" / "launchagents" / "com.saagar.notification-hub.plist"


def _load_codex_template(payload: dict[str, object]):
    common = types.ModuleType("common")
    setattr(common, "parse_notification_payload", lambda: payload)
    sys.modules["common"] = common
    try:
        spec = importlib.util.spec_from_file_location("codex_notify_template", CODEX_TEMPLATE)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop("common", None)


def test_claude_hook_template_builds_valid_json_for_shell_sensitive_values(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text('[{"timestamp":"2026-04-23T01:00:00.000Z"}]\n', encoding="utf-8")
    cwd = tmp_path / 'repo "quoted" name'
    cwd.mkdir()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    payload_path = tmp_path / "payload.json"
    (bin_dir / "terminal-notifier").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (bin_dir / "git").write_text(
        """#!/bin/sh
if [ "$3" = "config" ]; then
  printf "%s\\n" "git@github.com:saagpatel/repo-json-safe.git"
  exit 0
fi
printf "%s\\n" "feat/json safe"
""",
        encoding="utf-8",
    )
    (bin_dir / "curl").write_text(
        """#!/bin/sh
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-d" ]; then
    shift
    printf "%s" "$1" > "$CURL_PAYLOAD"
    exit 0
  fi
  shift
done
exit 1
""",
        encoding="utf-8",
    )
    (bin_dir / "python3").write_text('#!/bin/sh\ncat > "$CURL_PAYLOAD"\n', encoding="utf-8")
    for script in bin_dir.iterdir():
        script.chmod(0o755)

    hook_input = json.dumps({"transcript_path": str(transcript), "cwd": str(cwd)})
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CURL_PAYLOAD": str(payload_path),
    }
    subprocess.run(
        ["bash", str(CLAUDE_TEMPLATE)],
        input=hook_input,
        text=True,
        env=env,
        check=True,
        timeout=5,
    )
    for _ in range(20):
        if payload_path.exists():
            break
        time.sleep(0.05)

    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    event = Event.model_validate(payload)
    assert event.source == "cc"
    assert event.level == "normal"
    assert event.project == "saagpatel/repo-json-safe"
    assert event.session_label == 'repo "quoted" name'
    assert "saagpatel/repo-json-safe (feat/json safe): Done" in event.body
    assert event.event_id.startswith("cc:")
    assert len(event.event_id) == 35
    assert event.event_type == "claude.session.completed"
    assert event.source_revision == "2026-04-23T01:00:00.000Z"


def test_codex_hook_template_posts_valid_payload_with_repo_project_from_cwd() -> None:
    module = _load_codex_template(
        {
            "cwd": str(REPO_ROOT),
            "project": "task-clear-my-portfolio-s-dependabot",
            "message": "done",
        }
    )
    captured: dict[str, object] = {}

    def _capture_run(args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["timeout"] = kwargs["timeout"]
        captured["data"] = kwargs["input"]
        return subprocess.CompletedProcess(cast(list[str], args), 0)

    hook_payload = {
        "cwd": str(REPO_ROOT),
        "project": "task-clear-my-portfolio-s-dependabot",
    }
    project = module.project_from_payload(hook_payload)
    session_label = module.raw_session_label_from_payload(hook_payload)
    with patch.object(module.subprocess, "run", side_effect=_capture_run):
        module.post_to_hub(
            "attention",
            "Codex needs attention",
            "A verification or runtime issue needs review.",
            project,
            session_label,
        )

    assert captured["timeout"] == 3
    payload = json.loads(cast(str, captured["data"]))
    event = Event.model_validate(payload)
    assert event.source == "codex"
    assert event.level == "normal"
    assert event.project == "saagpatel/notification-hub"
    assert event.session_label == "task-clear-my-portfolio-s-dependabot"


def test_codex_hook_template_home_event_uses_named_fallback_not_prompt_slug() -> None:
    module = _load_codex_template({"message": "done"})
    payload = {
        "cwd": str(Path.home()),
        "project": "task-clear-my-portfolio-s-dependabot",
    }

    assert module.project_from_payload(payload) == "home-adhoc"
    assert module.raw_session_label_from_payload(payload) == "task-clear-my-portfolio-s-dependabot"


def test_codex_hook_template_reuses_deterministic_id_for_same_input() -> None:
    payload = {"cwd": str(REPO_ROOT), "message": "turn completed", "turn_id": "turn-7"}
    module = _load_codex_template(payload)
    captured: list[str] = []

    def _capture_run(args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs["timeout"] == 3
        body = json.loads(cast(str, kwargs["input"]))
        captured.append(str(body["event_id"]))
        return subprocess.CompletedProcess(cast(list[str], args), 0)

    with (
        patch.object(module, "run_safely"),
        patch.object(module.subprocess, "run", side_effect=_capture_run),
    ):
        assert module.main() == 0
        assert module.main() == 0

    assert captured[0] == captured[1]
    assert captured[0].startswith("codex:")
    assert len(captured[0]) == 38


def test_codex_hook_template_missing_identity_uses_unresolved_not_empty() -> None:
    module = _load_codex_template({"message": "done"})

    assert module.project_from_payload({"project": ""}) == "unresolved"


def test_codex_hook_template_clamps_payload_to_event_schema() -> None:
    module = _load_codex_template({"message": "done"})
    captured: dict[str, object] = {}

    def _capture_run(args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["timeout"] = kwargs["timeout"]
        captured["data"] = kwargs["input"]
        return subprocess.CompletedProcess(cast(list[str], args), 0)

    with patch.object(module.subprocess, "run", side_effect=_capture_run):
        module.post_to_hub(
            "complete",
            "T" * 250,
            "B" * 2500,
            "saagpatel/notification-hub",
            "P" * 250,
        )

    payload = json.loads(cast(str, captured["data"]))
    event = Event.model_validate(payload)
    assert len(event.title) <= 200
    assert len(event.body) <= 2000
    assert event.project is not None
    assert len(event.project) <= 100
    assert event.session_label is not None
    assert len(event.session_label) <= 200


def test_claude_hook_template_clamps_long_repo_names(tmp_path: Path) -> None:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text('[{"timestamp":"2026-04-23T01:00:00.000Z"}]\n', encoding="utf-8")
    cwd = tmp_path / ("repo-" + ("x" * 160))
    cwd.mkdir()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    payload_path = tmp_path / "payload.json"
    (bin_dir / "terminal-notifier").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (bin_dir / "git").write_text(
        '#!/bin/sh\nprintf "%s\\n" "{}"\n'.format("b" * 250),
        encoding="utf-8",
    )
    (bin_dir / "curl").write_text(
        """#!/bin/sh
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-d" ]; then
    shift
    printf "%s" "$1" > "$CURL_PAYLOAD"
    exit 0
  fi
  shift
done
exit 1
""",
        encoding="utf-8",
    )
    (bin_dir / "python3").write_text('#!/bin/sh\ncat > "$CURL_PAYLOAD"\n', encoding="utf-8")
    for script in bin_dir.iterdir():
        script.chmod(0o755)

    hook_input = json.dumps({"transcript_path": str(transcript), "cwd": str(cwd)})
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CURL_PAYLOAD": str(payload_path),
    }
    subprocess.run(
        ["bash", str(CLAUDE_TEMPLATE)],
        input=hook_input,
        text=True,
        env=env,
        check=True,
        timeout=5,
    )
    for _ in range(20):
        if payload_path.exists():
            break
        time.sleep(0.05)

    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    event = Event.model_validate(payload)
    assert event.project is not None
    assert len(event.project) <= 100
    assert event.project == "unresolved"
    assert event.session_label is not None
    assert len(event.session_label) <= 200
    assert len(event.body) <= 2000


def test_launch_agent_template_uses_frozen_runtime() -> None:
    text = LAUNCH_AGENT_TEMPLATE.read_text(encoding="utf-8")
    assert "/opt/homebrew/bin/uv" in text
    assert "<string>--frozen</string>" in text
    assert "<string>127.0.0.1</string>" in text
    assert "<string>9199</string>" in text
