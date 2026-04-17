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
uv sync
uv run uvicorn notification_hub.server:app --host 127.0.0.1 --port 9199 --reload
```

## Verification

```bash
uv run pytest
uv run ruff check
uv run pyright
```

The test suite uses temporary runtime paths, so local verification does not write into the live
machine event log or watch the real bridge file.

Runtime diagnostics:

```bash
curl http://127.0.0.1:9199/health
curl http://127.0.0.1:9199/health/details
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
  and whether key local files exist, without exposing secrets.

## Docs

- `README.md`: project overview, setup, and verification
- `IMPLEMENTATION-ROADMAP.md`: phased implementation history
- `CLAUDE.md`: maintainer notes and portfolio context
