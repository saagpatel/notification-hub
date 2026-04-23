# Current State

Last updated: 2026-04-23

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
- Runtime wiring now has repo-owned LaunchAgent and hook templates under `ops/`.
- A local explain command can preview classification, routing, and delivery without sending anything.
- A local policy-check command can audit the ruleset for overlaps, shadowing, and no-op rules,
  and now suggests likely fixes for each warning.
- Routing rules now support exact and prefix/text matchers instead of only exact source/project matching.
- Routing rules can now also opt into `continue_matching` so multiple matching rules can compose.
- Routing rules can now also use explicit `priority`, so higher-priority rules run before lower-priority ones.
- Event-log retention now runs automatically on the daemon’s schedule, not just as a manual command.
- Slack delivery is hardened so transport setup failures degrade quietly instead of escaping event
  intake.
- Quiet hours now support overnight, same-day, and disabled windows.
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
- Added `notification-hub explain` so policy behavior can be previewed before a real event is sent.
- Added `notification-hub policy-check` so the policy itself can be audited before it gets confusing,
  with concrete next-fix suggestions in the operator output.
- Added richer routing matchers like `project_prefix`, `title_contains`, `body_contains`, and `text_contains`.
- Added `continue_matching` routing behavior so one matching rule can refine level/delivery and still
  let later rules add more constraints.
- Added explicit routing rule priorities so policy authors can control evaluation order without
  rewriting the whole file.
- Added scheduled automatic retention so the live JSONL log can prune itself without relying on a
  separate operator run.
- Added repo-owned LaunchAgent and hook templates so live machine wiring can be verified against
  checked-in source.
- Hardened Slack delivery failure handling, quiet-hours policy semantics, and repeated bridge-line
  detection.

## Verified Baseline

The following checks were re-run after cleanup and merge:

```bash
uv lock --check
uv run --frozen pytest
uv run --frozen ruff check
uv run --frozen pyright
curl http://127.0.0.1:9199/health/details
uv run --frozen notification-hub-doctor
uv run --frozen notification-hub-policy-check
uv run --frozen notification-hub-explain --source codex --level info --title "Test" --body "Session complete"
uv run --frozen notification-hub smoke
uv run --frozen notification-hub retention --max-events 2000
```

Expected current outcome:

- `pytest`: 194 passed
- `ruff`: clean
- `pyright`: 0 errors
- `/health/details`: `status: ok`, watcher active, push available, Slack configured
- `notification-hub-doctor`: `status: ok`
- `notification-hub-policy-check`: `status: ok` or `warn`, depending on the active policy file,
  plus warning-specific fix suggestions when issues are found
- `notification-hub-explain`: returns a non-mutating classification/routing/delivery preview
- `notification-hub smoke`: `status: ok`
- `notification-hub retention --max-events 2000`: `status: ok`
- GitHub Actions `CI` workflow: passing on `main`
- Runtime wiring checks: LaunchAgent, Claude hook, and Codex hook match the repo-owned templates
  after the local refresh step is applied.

Additional behavioral baseline:

- `config/policy.example.toml` includes classifier, suppression, and routing examples
- Routing rules can now match on `project_prefix`, `title_contains`, `body_contains`, and `text_contains`
- Higher-priority routing rules now run before lower-priority ones, while same-priority rules still
  preserve file order
- Routing rules still stop at the first match by default, but a rule can opt into
  `continue_matching = true` when later rules should keep refining delivery
- Retention now runs automatically with the daemon’s configured interval and still supports the
  manual `notification-hub retention` command for an immediate operator-triggered pass
- `notification-hub bootstrap-config` copies that sample into `~/.config/notification-hub/config.toml`
  and preserves an existing config unless `--force` is used
- `notification-hub policy-check` is available as a non-mutating ruleset audit tool with suggested
  next fixes for the common warning cases, including disabled automatic retention and ineffective
  `continue_matching` usage, redundant rules inside a continue-matching chain, and same-priority
  ties that still depend on file order
- `notification-hub explain` is available as a non-mutating policy preview tool
- Bootstrap command wiring is verified, but live bootstrap is intentionally not part of the routine
  confidence pass when no user config exists yet because it would create local runtime state

## Runtime Notes

- LaunchAgent plist: `~/Library/LaunchAgents/com.saagar.notification-hub.plist`
- LaunchAgent template: `ops/launchagents/com.saagar.notification-hub.plist`
- Claude hook template: `ops/hooks/claude-notify.sh`
- Codex hook template: `ops/hooks/codex-notify-local.py`
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

Start future work from `main`, keep using the frozen verification commands, and treat the repo-owned
runtime templates as the source of truth for live launcher and hook wiring.
The next work here should build on the doctor/config/runtime-wiring surfaces rather than reopening
repo-baseline repair.

## Optional Follow-Up

- Delete `archive/local-history-pre-import` later if that old local-only history is no longer needed.
- Remove local untracked junk files like `.DS_Store` if you want a tidier working directory on disk.
