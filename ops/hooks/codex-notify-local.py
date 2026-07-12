#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

from common import parse_notification_payload

SOUNDS = {
    "waiting": "/System/Library/Sounds/Funk.aiff",
}

WAITING_MARKERS = (
    "awaiting response",
    "awaiting your response",
    "awaiting input",
    "waiting for your response",
    "waiting for input",
    "waiting for approval",
    "needs approval",
    "needs your input",
    "needs user input",
    "user input required",
    "approval required",
)

ATTENTION_MARKERS = (
    "needs attention",
    "verification failed",
    "review required",
    "action required",
    "runtime issue",
    "command failed",
    "tool failed",
)

COMPLETE_MARKERS = (
    "turn completed",
    "completed",
    "finished",
    "done",
)

MAX_TITLE_LENGTH = 200
MAX_BODY_LENGTH = 2000
MAX_PROJECT_LENGTH = 100
MAX_SESSION_LABEL_LENGTH = 200
HOME_ADHOC_PROJECT = "home-adhoc"
UNRESOLVED_PROJECT = "unresolved"
REPO_FULL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def clamp_text(value: str, max_length: int) -> str:
    value = value.strip()
    if len(value) <= max_length:
        return value
    return value[: max_length - 3].rstrip() + "..."


def iter_string_values(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, dict):
        for nested in value.values():
            yield from iter_string_values(nested)
        return
    if isinstance(value, list):
        for nested in value:
            yield from iter_string_values(nested)


def payload_text(payload: dict) -> str:
    parts = [part.strip().lower() for part in iter_string_values(payload) if part.strip()]
    return " | ".join(parts)


def classify_notification(payload: dict) -> tuple[str, str, str]:
    combined = payload_text(payload)
    if any(marker in combined for marker in WAITING_MARKERS):
        return (
            "waiting",
            "Codex is waiting",
            "Codex is waiting for your response.",
        )
    if any(marker in combined for marker in ATTENTION_MARKERS):
        return (
            "attention",
            "Codex needs attention",
            "A verification or runtime issue needs review.",
        )
    if any(marker in combined for marker in COMPLETE_MARKERS):
        return (
            "complete",
            "Codex finished a turn",
            "A Codex turn completed.",
        )
    return (
        "complete",
        "Codex finished a turn",
        "A Codex turn completed.",
    )


def run_safely(args: list[str]) -> None:
    try:
        subprocess.run(args, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return


def command_output(args: list[str]) -> str | None:
    try:
        output = subprocess.check_output(args, stderr=subprocess.DEVNULL, timeout=1)
    except Exception:
        return None
    text = output.decode("utf-8", errors="replace").strip()
    return text or None


def repo_full_name_from_remote(remote_url: str) -> str | None:
    value = remote_url.strip()
    if value.endswith(".git"):
        value = value[:-4]
    match = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+)$", value)
    if match is None:
        return None
    candidate = f"{match.group(1)}/{match.group(2)}"
    if REPO_FULL_NAME_RE.fullmatch(candidate):
        return clamp_text(candidate, MAX_PROJECT_LENGTH)
    return None


def repo_full_name_from_cwd(cwd: str) -> str | None:
    remote_url = command_output(["git", "-C", cwd, "config", "--get", "remote.origin.url"])
    if remote_url is None:
        return None
    return repo_full_name_from_remote(remote_url)


def raw_session_label_from_payload(payload: dict) -> str | None:
    for key in ("project", "project_name", "repo", "repository"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return clamp_text(value, MAX_SESSION_LABEL_LENGTH)
    return None


def explicit_repo_full_name_from_payload(payload: dict) -> str | None:
    for key in ("repo_full_name", "repository_full_name", "repo", "repository", "project"):
        value = payload.get(key)
        if isinstance(value, str) and REPO_FULL_NAME_RE.fullmatch(value.strip()):
            return clamp_text(value.strip(), MAX_PROJECT_LENGTH)
    return None


def project_from_payload(payload: dict) -> str:
    explicit = explicit_repo_full_name_from_payload(payload)
    if explicit is not None:
        return explicit
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        cwd_path = Path(cwd).expanduser()
        if cwd_path == Path.home():
            return HOME_ADHOC_PROJECT
        repo_full_name = repo_full_name_from_cwd(str(cwd_path))
        if repo_full_name is not None:
            return repo_full_name
    return UNRESOLVED_PROJECT


def post_to_hub(
    level: str,
    title: str,
    message: str,
    project: str,
    session_label: str | None = None,
    event_id: str | None = None,
    event_type: str | None = None,
    source_revision: str | None = None,
) -> None:
    """Fire-and-forget POST to notification hub. Ignores all errors."""
    hub_level = "urgent" if level == "waiting" else "normal"
    payload = {
        "source": "codex",
        "level": hub_level,
        "title": clamp_text(title, MAX_TITLE_LENGTH) or "Codex notification",
        "body": clamp_text(message, MAX_BODY_LENGTH) or "Codex notification.",
        "project": clamp_text(project, MAX_PROJECT_LENGTH),
    }
    if event_id is not None:
        payload["event_id"] = event_id
    if event_type is not None:
        payload["event_type"] = event_type
    if source_revision is not None:
        payload["source_revision"] = clamp_text(source_revision, 200)
    if session_label is not None:
        payload["session_label"] = clamp_text(session_label, MAX_SESSION_LABEL_LENGTH)
    producer = Path(__file__).with_name("notification-hub-producer.py")
    try:
        subprocess.run(
            [sys.executable, str(producer)],
            input=json.dumps(payload),
            text=True,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
    except Exception:
        pass


def main() -> int:
    payload = parse_notification_payload()
    level, title, message = classify_notification(payload)
    source_revision = None
    for key in ("turn_id", "session_id", "thread_id"):
        value = payload.get(key)
        if isinstance(value, (str, int)) and str(value):
            source_revision = str(value)
            break
    sound_file = SOUNDS.get(level)
    if sound_file:
        sound_path = Path(sound_file)
        if sound_path.exists():
            run_safely(["/usr/bin/afplay", str(sound_path)])
    script = ('display notification "{message}" with title "{title}" subtitle "Codex"').format(
        message=message.replace('"', '\\"'),
        title=title.replace('"', '\\"'),
    )
    run_safely(["/usr/bin/osascript", "-e", script])
    post_to_hub(
        level,
        title,
        message,
        project_from_payload(payload),
        raw_session_label_from_payload(payload),
        "codex:"
        + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:32],
        f"codex.turn.{level}",
        source_revision,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
