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

notification-hub is the local daemon that turns AI-tool events into routed operator notifications. It accepts structured local HTTP events, watches bridge activity, classifies urgency with deterministic rules, suppresses noise, writes JSONL logs, and routes notifications to local, Slack, and operator-review surfaces.

## Current State

This repo is in healthy monitor-mode after the latest cleanup pass. `README.md` is the primary command guide, `docs/CURRENT-STATE.md` is the best restart point, and the current lane is continued observation of real operator handoff signals rather than expansion of apply behavior.

## Stack

- Python package managed with `uv`
- FastAPI daemon on `127.0.0.1:9199`
- Local JSONL runtime logs and queue state
- macOS LaunchAgent integration
- Slack delivery via Keychain-backed webhook lookup
- Bridge/coordination command surfaces for read-only operator review

## How To Run

```bash
uv sync --frozen --group dev
uv run --frozen uvicorn notification_hub.server:app --host 127.0.0.1 --port 9199 --reload
```

## Known Risks

- Do not add apply behavior to notification-hub; it stages and reviews handoffs only.
- Keep personal-ops promotion and outcome sync operator-mediated.
- Treat bridge-db saves, queue imports, and live delivery checks as explicit operator actions.
- Continue watching near-rollup singles before adding suppression or policy changes.

## Next Recommended Move

Use `docs/CURRENT-STATE.md` to resume quickly. Keep the system in monitor mode unless real repeated operator noise or a real promoted rich handoff gives enough evidence for the next narrow change.

<!-- portfolio-context:end -->

<!-- secondbrain-breadcrumb -->
## SecondBrain knowledge vault

Prior lessons, decisions, and context for this project live in SecondBrain at `wiki/maps/projects/notification-hub.md`. The whole vault is searchable via the `engraph` MCP — query it for this project + its stack before non-trivial work.
