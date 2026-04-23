# Notification Hub

`notification-hub` is a small local daemon that turns AI-tool events into routed notifications.
It accepts structured events over HTTP, watches the shared bridge file for appended activity,
classifies urgency with deterministic rules, and then delivers each event to the right channel.

## What It Does

- Accepts `POST /events` on `127.0.0.1:9199`
- Watches the Claude bridge file for new activity lines
- Classifies events as `urgent`, `normal`, or `info`
- Always writes events to a local JSONL log
- Sends urgent events to push + Slack
- Sends normal events to Slack
- Keeps info events in the log only
- Suppresses noise with dedup, quiet hours, and rate limits

## Architecture

```text
Event sources -> FastAPI intake -> classifier -> suppression -> delivery channels
```

Core modules:

- `server.py`: FastAPI app and lifecycle
- `watcher.py`: bridge file watcher and parsing
- `pipeline.py`: routing flow across classification, suppression, and delivery
- `classifier.py`: deterministic keyword rules
- `suppression.py`: dedup, quiet hours, and rate limiting
- `channels.py`: JSONL, macOS push, and Slack delivery
- `config.py`: host, paths, and Keychain-backed webhook lookup

## Local Development

```bash
uv sync --frozen --extra dev
uv run --frozen uvicorn notification_hub.server:app --host 127.0.0.1 --port 9199 --reload
```

## Operator Commands

```bash
uv run notification-hub doctor
uv run notification-hub-doctor
uv run notification-hub-doctor --json
uv run notification-hub smoke
uv run notification-hub policy-check
uv run notification-hub explain --source codex --level info --title "Test" --body "Approval needed"
uv run notification-hub bootstrap-config
uv run notification-hub retention --max-events 2000
```

The doctor command checks the local API, LaunchAgent presence, bridge file path, push notifier,
Slack Keychain setup, and policy-config load status.
The smoke command posts a harmless `info` event and verifies it lands in the live JSONL log.
The policy-check command inspects the current policy config for overlapping keywords, shadowed
routing rules, and no-op rules before they cause confusing behavior, and now also suggests likely
fixes for each warning it reports.
The explain command shows how a sample event would classify, route, and deliver without posting it
to the live daemon or sending any notifications.
The bootstrap command copies the repo sample policy file into `~/.config/notification-hub/config.toml`
without overwriting an existing config unless you pass `--force`.
The retention command archives older log entries into `~/.local/share/notification-hub/archive/`.
The daemon now also performs the same retention check automatically on a schedule, while the manual
command remains available when you want to force a run immediately.

## Policy Config

Optional runtime policy overrides live at:

```text
~/.config/notification-hub/config.toml
```

The repo includes a starter example at:

```text
config/policy.example.toml
```

Supported sections today:

```toml
[classifier]
urgent_keywords = ["database down", "approval needed"]
normal_keywords = ["session complete", "ship it"]
info_keywords = ["routine ping"]

[suppression]
quiet_start_hour = 23
quiet_end_hour = 7
dedup_window_minutes = 30
max_push_per_hour = 5
max_slack_per_hour = 20
max_overflow_buffer = 500
max_quiet_queue = 200

[retention]
enabled = true
interval_minutes = 60
max_events = 2000
keep_archives = 10

[[routing.rules]]
project = "notification-hub"
priority = 20
force_level = "normal"
disable_push = true
continue_matching = true

[[routing.rules]]
source = "bridge_watcher"
priority = 10
disable_slack = true

[[routing.rules]]
project_prefix = "notification-"
title_contains = "review"
body_contains = "verification"
disable_slack = true
```

If the file is missing or invalid, notification-hub falls back to built-in defaults and reports the
config status through the doctor command and `GET /health/details`.
Routing rules are matched in order, and the first matching rule can override the classified level or
disable push/Slack delivery for that event.
Matchers can now use exact source/project, `project_prefix`, and lowercase `title_contains`,
`body_contains`, or `text_contains` checks.
Rules with a higher `priority` run first, and rules with the same priority keep their file order.
If a rule sets `continue_matching = true`, notification-hub keeps evaluating later rules so a policy
can compose multiple overrides instead of stopping at the first match.
Retention is enabled by default with a conservative hourly check. It only rotates the log when the
live JSONL file grows beyond `max_events`, and it keeps up to `keep_archives` archived files.
Quiet hours use a start-inclusive, end-exclusive window. When `quiet_start_hour < quiet_end_hour`,
the window is same-day. When `quiet_start_hour > quiet_end_hour`, the window crosses midnight.
When both values are equal, quiet hours are disabled.

First-time setup shortcut:

```bash
uv run notification-hub bootstrap-config
```

Safe policy-preview shortcut:

```bash
uv run notification-hub explain \
  --source codex \
  --level info \
  --title "Review ready" \
  --body "Session complete after verification"
```

Safe policy-audit shortcut:

```bash
uv run notification-hub policy-check
```

The audit output is intentionally non-mutating. It reports warnings plus likely next fixes such as
moving a narrower rule earlier, removing a redundant matcher, or deleting a rule that does not
change behavior. It also flags disabled automatic retention and `continue_matching` rules that
cannot actually continue into a later rule, redundant rules that add nothing beyond an earlier
continue-matching chain, and same-priority rules where file order is still breaking the tie.

## Verification

```bash
uv lock --check
uv run --frozen pytest
uv run --frozen ruff check
uv run --frozen pyright
```

The test suite uses temporary runtime paths, so local verification does not write into the live
machine event log or watch the real bridge file.
The committed `uv.lock` file keeps local installs and CI in sync.

Runtime diagnostics:

```bash
curl http://127.0.0.1:9199/health
curl http://127.0.0.1:9199/health/details
uv run --frozen notification-hub-doctor
uv run --frozen notification-hub policy-check
uv run --frozen notification-hub explain --source codex --level info --title "Test" --body "Approval needed"
uv run --frozen notification-hub smoke
uv run --frozen notification-hub retention --max-events 2000
```

## Runtime Notes

- The daemon is localhost-only.
- The canonical local Python version is pinned in `.python-version` and matches CI's Python 3.12
  target.
- The event log is written to `~/.local/share/notification-hub/events.jsonl`.
- Slack webhook secrets are read from macOS Keychain and are never stored in repo files.
- If the Slack webhook is not configured, the daemon stays healthy and continues local delivery
  without spamming repeated Slack-failure warnings.
- If a Slack webhook is added later, the daemon will retry Keychain lookup automatically within
  about a minute, so a manual restart is usually not required.
- LaunchAgent support lives at `~/Library/LaunchAgents/com.saagar.notification-hub.plist`.
- Repo-owned runtime templates live under `ops/`: the LaunchAgent template, Claude Code hook
  template, and Codex hook template are the source of truth for machine-local wiring.
- `GET /health/details` reports whether push delivery is available, whether Slack is configured,
  whether key local files exist, whether a policy config file was loaded, how many policy warnings
  were found, the current retention settings plus the last retention result, and current
  suppression queue counters, and whether runtime wiring matches the checked-in templates, without
  exposing secrets.

Refresh local runtime wiring from repo templates:

```bash
install -m 644 ops/launchagents/com.saagar.notification-hub.plist ~/Library/LaunchAgents/com.saagar.notification-hub.plist
install -m 755 ops/hooks/claude-notify.sh ~/.claude/hooks/notify.sh
install -m 755 ops/hooks/codex-notify-local.py ~/.codex/hooks/notify_local.py
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/com.saagar.notification-hub.plist 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.saagar.notification-hub.plist
launchctl kickstart -k "gui/$(id -u)/com.saagar.notification-hub"
```

## Docs

- `README.md`: project overview, setup, and verification
- `docs/CURRENT-STATE.md`: current repo/runtime status and the safest restart point
- `IMPLEMENTATION-ROADMAP.md`: phased implementation history
- `CLAUDE.md`: maintainer notes and portfolio context
