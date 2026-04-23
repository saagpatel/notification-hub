# Notification Hub Maintainer Notes

Use `README.md` as the main project entry point. This file stays intentionally short and only
captures maintainer-specific context that does not belong in the general setup guide.

## Working Rules

- Keep the daemon localhost-only unless there is an explicit product decision to expand scope.
- Preserve additive hook behavior: upstream Claude Code and Codex hooks should still work even if
  notification-hub is unavailable.
- Prefer deterministic logic over heuristic complexity. This project should stay easy to reason about.
- Treat tests, `ruff`, and `pyright` as required quality gates for changes.

## Operational Context

- LaunchAgent path: `~/Library/LaunchAgents/com.saagar.notification-hub.plist`
- Event log path: `~/.local/share/notification-hub/events.jsonl`
- Bridge file path: `~/.claude/projects/-Users-d/memory/claude_ai_context.md`
- Repo-owned runtime templates: `ops/launchagents/` and `ops/hooks/`

## Documentation Map

- `README.md`: overview, setup, verification, runtime behavior
- `docs/CURRENT-STATE.md`: resume-ready current state and verification baseline
- `IMPLEMENTATION-ROADMAP.md`: phased delivery history
- `ops/`: source-of-truth templates for machine-local LaunchAgent and hook wiring

<!-- portfolio-context:start -->
# Portfolio Context

## What This Project Is

notification-hub is an active local project in the `/Users/d/Projects` portfolio.

## Current State

This repo now has a maintained `README.md` as its primary entry point, with `CLAUDE.md` reserved
for maintainer notes and portfolio context. The cleanup and hardening pass is complete, and
`docs/CURRENT-STATE.md` is now the best resume point for the next work session.

## Next Recommended Move

Use `docs/CURRENT-STATE.md` to resume quickly, use the README for day-to-day commands, keep
implementation history in the roadmap, and capture future design changes close to the code instead
of reopening repo-baseline cleanup work.

<!-- portfolio-context:end -->
