# AGENTS.md

<!-- comm-contract:start -->

## Communication Contract

- Follow your global Codex communication and reporting conventions (voice, status-reporting format).
- Repo-specific instructions below add project constraints only; do not restate global voice or status-reporting rules here.
<!-- comm-contract:end -->

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

## Review guidelines

Focus Codex review on daemon determinism, localhost-only boundaries,
LaunchAgent and hook template drift, install/runtime verification truthfulness,
event durability, identity mapping, stale notification state, and additive
failure behavior when notification-hub is down. Treat docs or scripts that make
operator recovery commands target the wrong label, path, port, or runtime as
merge-relevant.

For docs-only PRs, comment only when docs claim a runtime state, hook behavior,
health result, install command, or burn-in result that is not supported by the
reviewed files or canonical checks.

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

<!-- secondbrain-breadcrumb -->
## SecondBrain knowledge vault

Prior lessons, decisions, and context for this project live in SecondBrain at `wiki/maps/projects/notification-hub.md`. The whole vault is searchable via the `engraph` MCP — query it for this project + its stack before non-trivial work.

<!-- portfolio-context:start -->
# Portfolio Context

## What This Project Is

notification-hub: `notification-hub` is a small local daemon that turns AI-tool events into routed notifications.

## Current State

Portfolio truth currently marks this project as `active` with `boilerplate` context. Phase 104 recovered minimum-viable context so future sessions can resume without rediscovery.

## Stack

- Primary stack: Python

## How To Run

```bash
uv sync --frozen --group dev
uv run --frozen uvicorn notification_hub.server:app --host 127.0.0.1 --port 9199 --reload
```

## Known Risks

- This repo only has minimum-viable recovery context today; deeper handoff details may still live in the README and supporting docs.

## Next Recommended Move

Use this context plus the README and supporting docs to resume the next active task, then promote the repo beyond minimum-viable by capturing a dedicated handoff, roadmap, or discovery artifact.

<!-- portfolio-context:end -->
