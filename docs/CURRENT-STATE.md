# Current State

Last updated: 2026-04-17

## Snapshot

`notification-hub` is in a healthy, normal operating state.

- Local `main` matches `origin/main`.
- GitHub Actions CI is configured and passing on `main`.
- The daemon is running locally via LaunchAgent on `127.0.0.1:9199`.
- Slack delivery is configured through macOS Keychain and is working.
- Policy-based runtime overrides are now supported through an optional config file.
- A local doctor command is available for operator checks.
- The repo now also includes a sample policy config, a smoke command, and a log-retention command.
- Policy config now also supports ordered routing rules, and a bootstrap command can copy the sample
  config into the live config path.
- The earlier runtime-hardening and repo-cleanup pass is complete.

## What Was Cleaned Up

- Isolated tests from real machine runtime state so local `pytest` no longer pollutes the live event log or bridge watcher paths.
- Hardened Slack-disabled behavior so a missing webhook does not create repeated noisy delivery failures.
- Added retry behavior for missing Slack webhook lookup so a restored Keychain secret is picked up automatically without relying on a manual restart.
- Added GitHub Actions CI for `pytest`, `ruff`, and `pyright`.
- Committed `uv.lock` so local installs and CI resolve the same dependency set.
- Restored a normal git baseline on `main` and merged the CI/lockfile work back into `main`.
- Added a loadable policy config for classifier keywords and suppression limits.
- Added `notification-hub-doctor` and expanded runtime diagnostics.
- Added a checked-in sample config, a smoke check command, and log-retention tooling.
- Added ordered routing rules for per-project and per-source delivery overrides.
- Added `notification-hub bootstrap-config` so first-time policy setup is a command instead of a
  manual copy step.

## Verified Baseline

The following checks were re-run after cleanup and merge:

```bash
uv lock --check
uv run pytest
uv run ruff check
uv run pyright
curl http://127.0.0.1:9199/health/details
uv run notification-hub-doctor
uv run notification-hub smoke
uv run notification-hub retention --max-events 2000
```

Expected current outcome:

- `pytest`: 160 passed
- `ruff`: clean
- `pyright`: 0 errors
- `/health/details`: `status: ok`, watcher active, push available, Slack configured
- `notification-hub-doctor`: `status: ok`
- `notification-hub smoke`: `status: ok`
- `notification-hub retention --max-events 2000`: `status: ok`
- GitHub Actions `CI` workflow: passing on `main`

Additional behavioral baseline:

- `config/policy.example.toml` includes classifier, suppression, and routing examples
- `notification-hub bootstrap-config` copies that sample into `~/.config/notification-hub/config.toml`
  and preserves an existing config unless `--force` is used
- Bootstrap command wiring is verified, but live bootstrap is intentionally not part of the routine
  confidence pass when no user config exists yet because it would create local runtime state
- Retention is still a manual operator action by choice; it is not scheduled automatically

## Runtime Notes

- LaunchAgent plist: `~/Library/LaunchAgents/com.saagar.notification-hub.plist`
- Event log: `~/.local/share/notification-hub/events.jsonl`
- Bridge file watched by the daemon: `~/.claude/projects/-Users-d/memory/claude_ai_context.md`
- Slack webhook storage: macOS Keychain, service `slack-webhook`, account `notification-hub`
- Optional policy config: `~/.config/notification-hub/config.toml`
- Sample config artifact in repo: `config/policy.example.toml`

## Git Notes

- Primary branch: `main`
- Preserved archive branch: `archive/local-history-pre-import`

The archive branch is intentionally kept as a safety branch for the older pre-import local-only history.
It is not part of normal day-to-day work.

## Safest Next Step

Start future work from `main`, keep using the existing verification commands, and treat this cleanup pass as complete.
The next work here should build on the new doctor/config surfaces rather than reopening repo-baseline repair.

## Optional Follow-Up

- Delete `archive/local-history-pre-import` later if that old local-only history is no longer needed.
- Remove local untracked junk files like `.DS_Store` if you want a tidier working directory on disk.
