"""Guard README command examples against CLI inventory drift."""

from __future__ import annotations

import re
import shlex
import tomllib
from pathlib import Path
from typing import Any, NamedTuple, cast

ROOT = Path(__file__).resolve().parents[1]


class ReadmeCommand(NamedTuple):
    line: str
    executable: str
    subcommand: str | None


def _readme_notification_hub_commands() -> list[ReadmeCommand]:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    commands: list[ReadmeCommand] = []
    for block in re.findall(r"```(?:bash|sh|shell)\n(.*?)```", readme, flags=re.DOTALL):
        for raw_line in block.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or line.endswith("\\"):
                continue
            if "notification-hub" not in line:
                continue
            tokens = shlex.split(line)
            command_index = 0
            if tokens[:3] == ["uv", "run", "--frozen"]:
                command_index = 3
            elif tokens[:2] == ["uv", "run"]:
                command_index = 2
            if command_index >= len(tokens):
                continue
            executable = tokens[command_index]
            if not executable.startswith("notification-hub"):
                continue
            subcommand = (
                tokens[command_index + 1]
                if executable == "notification-hub" and command_index + 1 < len(tokens)
                else None
            )
            commands.append(ReadmeCommand(line, executable, subcommand))
    return commands


def _pyproject_scripts() -> set[str]:
    pyproject: dict[str, Any] = tomllib.loads((ROOT / "pyproject.toml").read_text())
    project = cast(dict[str, object], pyproject["project"])
    scripts = cast(dict[str, str], project["scripts"])
    return set(scripts)


def _cli_subcommands() -> set[str]:
    cli_source = (ROOT / "src" / "notification_hub" / "cli_parser.py").read_text(encoding="utf-8")
    return set(re.findall(r'subparsers\.add_parser\(\s*"([^"]+)"', cli_source))


def test_readme_notification_hub_commands_match_cli_inventory() -> None:
    commands = _readme_notification_hub_commands()
    assert commands, "README should document at least one notification-hub command"
    scripts = _pyproject_scripts()
    subcommands = _cli_subcommands()

    missing_scripts = sorted(
        {command.executable for command in commands if command.executable != "notification-hub"}
        - scripts
    )
    missing_subcommands = sorted(
        {
            command.subcommand
            for command in commands
            if command.executable == "notification-hub" and command.subcommand is not None
        }
        - subcommands
    )

    assert missing_scripts == []
    assert missing_subcommands == []
