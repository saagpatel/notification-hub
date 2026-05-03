# AGENTS.md

## Communication Contract

- Follow `/Users/d/.codex/policies/communication/BigPictureReportingV1.md` for all user-facing updates.
- Use exact section labels from `BigPictureReportingV1.md` for default status/progress updates.
- Keep default updates beginner-friendly, big-picture, and low-noise.
- Keep technical details in internal artifacts unless explicitly requested by the user.
- Honor toggles literally: `simple mode`, `show receipts`, `tech mode`, `debug mode`.

## Project Goal

notification-hub is a localhost-only notification daemon for Claude Code, Codex, and Claude.ai workflows. Keep it deterministic, additive, and easy to inspect.

## First Read

- `README.md` for setup, commands, and runtime behavior.
- `docs/CURRENT-STATE.md` for the resume-ready current state.
- `CLAUDE.md` for maintainer notes and portfolio context.
- `ops/` for LaunchAgent and hook templates.

## Core Rules

- Keep the daemon localhost-only unless there is an explicit product decision to expand scope.
- Preserve additive hook behavior: upstream Claude Code and Codex hooks should still work if notification-hub is unavailable.
- Prefer deterministic logic over heuristic complexity.
- Treat tests, `ruff`, and `pyright` as required quality gates for changes.
- Do not touch machine-local LaunchAgent state, hooks, or runtime logs unless explicitly requested.

## Codex App Usage

- Use Codex App Projects for repo-specific implementation, review, and verification in this checkout.
- Use a Worktree when changing daemon behavior, hook wiring, LaunchAgent templates, retention, policy checks, or runtime verification.
- Use the in-app browser only for HTTP/API behavior that benefits from rendered or interactive inspection.
- Use computer use only for GUI-only macOS settings or LaunchAgent behavior that cannot be verified through CLI, tests, MCP, or browser tools.
- Use artifacts for burn-in reports, runtime evidence summaries, policy notes, and handoff docs.
- Keep connectors read-first and task-scoped. Do not send notifications or mutate external systems unless explicitly requested.

## Verification

- Prefer the repo's documented commands before inventing new ones.
- Useful current checks include:
  - `uv run pytest`
  - `uv run pyright`
  - `uv run ruff check`
  - `notification-hub-doctor`
  - `notification-hub-verify-runtime`
  - `notification-hub-burn-in`
- If a command is missing, unclear, or unsafe to run, stop and report the blocker instead of guessing.

## Done Criteria

- The requested change is implemented.
- Relevant tests or checks were run, or the exact reason they were not run is stated.
- Runtime or ops docs are updated when daemon behavior, hook behavior, or maintenance workflow changes.
- Assumptions, risks, and next steps are summarized before closeout.
