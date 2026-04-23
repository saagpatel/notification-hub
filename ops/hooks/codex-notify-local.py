#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import urllib.request
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


def project_from_payload(payload: dict) -> str | None:
    for key in ("project", "project_name", "repo", "repository"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        return Path(cwd).name
    return None


def post_to_hub(level: str, title: str, message: str, project: str | None = None) -> None:
    """Fire-and-forget POST to notification hub. Ignores all errors."""
    hub_level = "urgent" if level == "waiting" else "normal"
    payload = {
        "source": "codex",
        "level": hub_level,
        "title": title,
        "body": message,
    }
    if project is not None:
        payload["project"] = project
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:9199/events",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass


def main() -> int:
    payload = parse_notification_payload()
    level, title, message = classify_notification(payload)
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
    post_to_hub(level, title, message, project_from_payload(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
