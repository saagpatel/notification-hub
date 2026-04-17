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
uv sync --extra dev
uv run uvicorn notification_hub.server:app --host 127.0.0.1 --port 9199 --reload
```

## Operator Commands

```bash
uv run notification-hub doctor
uv run notification-hub-doctor
uv run notification-hub-doctor --json
uv run notification-hub smoke
uv run notification-hub bootstrap-config
uv run notification-hub retention --max-events 2000
```

The doctor command checks the local API, LaunchAgent presence, bridge file path, push notifier,
Slack Keychain setup, and policy-config load status.
The smoke command posts a harmless `info` event and verifies it lands in the live JSONL log.
The bootstrap command copies the repo sample policy file into `~/.config/notification-hub/config.toml`
without overwriting an existing config unless you pass `--force`.
The retention command archives older log entries into `~/.local/share/notification-hub/archive/`.
Retention remains a manual operator action by design for now, so log pruning only happens when you
explicitly run the command.

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

[[routing.rules]]
project = "notification-hub"
force_level = "normal"
disable_push = true

[[routing.rules]]
source = "bridge_watcher"
disable_slack = true
```

If the file is missing or invalid, notification-hub falls back to built-in defaults and reports the
config status through the doctor command and `GET /health/details`.
Routing rules are matched in order, and the first matching rule can override the classified level or
disable push/Slack delivery for that event.

First-time setup shortcut:

```bash
uv run notification-hub bootstrap-config
```

## Verification

```bash
uv lock --check
uv run pytest
uv run ruff check
uv run pyright
```

The test suite uses temporary runtime paths, so local verification does not write into the live
machine event log or watch the real bridge file.
The committed `uv.lock` file keeps local installs and CI in sync.

Runtime diagnostics:

```bash
curl http://127.0.0.1:9199/health
curl http://127.0.0.1:9199/health/details
uv run notification-hub-doctor
uv run notification-hub smoke
uv run notification-hub retention --max-events 2000
```

## Runtime Notes

- The daemon is localhost-only.
- The event log is written to `~/.local/share/notification-hub/events.jsonl`.
- Slack webhook secrets are read from macOS Keychain and are never stored in repo files.
- If the Slack webhook is not configured, the daemon stays healthy and continues local delivery
  without spamming repeated Slack-failure warnings.
- If a Slack webhook is added later, the daemon will retry Keychain lookup automatically within
  about a minute, so a manual restart is usually not required.
- LaunchAgent support lives at `~/Library/LaunchAgents/com.saagar.notification-hub.plist`.
- `GET /health/details` reports whether push delivery is available, whether Slack is configured,
  whether key local files exist, whether a policy config file was loaded, and current suppression
  queue counters, without exposing secrets.

## Docs

- `README.md`: project overview, setup, and verification
- `docs/CURRENT-STATE.md`: current repo/runtime status and the safest restart point
- `IMPLEMENTATION-ROADMAP.md`: phased implementation history
- `CLAUDE.md`: maintainer notes and portfolio context
