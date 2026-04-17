# Notification Hub

Unified notification daemon for three AI systems (Claude Code, Codex, Claude.ai).
Receives events via HTTP POST + bridge file watcher, classifies urgency, routes to the right channel.

## Stack

- Python 3.12+, FastAPI, uvicorn, watchdog, httpx
- No LLM calls — pure routing/classification daemon
- Localhost only (127.0.0.1:9199)

## Architecture

```
Event Sources                    Notification Hub                    Channels
┌──────────┐                    ┌──────────────────┐               ┌─────────────────┐
│ Claude Code│──POST──────────→│ FastAPI :9199     │──urgent──→│ terminal-notifier│
│ hook      │                   │                  │               │ + sound + Slack  │
├──────────┤                    │  ┌────────────┐  │               ├─────────────────┤
│ Codex     │──POST──────────→│  │ Classifier  │  │──normal──→│ Slack webhook    │
│ hook      │                   │  └────────────┘  │               ├─────────────────┤
├──────────┤                    │  ┌────────────┐  │──info────→│ JSONL log only   │
│ Claude.ai │──(bridge file)──→│  │ Suppression │  │               └─────────────────┘
│           │  watchdog watches │  └────────────┘  │
└──────────┘                    └──────────────────┘
```

## Commands

```bash
# Dev
uv sync
uv run uvicorn notification_hub.server:app --host 127.0.0.1 --port 9199 --reload

# Test
uv run pytest

# Type check
uv run pyright
```

## Project Layout

```
src/notification_hub/
  server.py       — FastAPI app, POST /events endpoint, health check
  models.py       — Pydantic event models
  classifier.py   — Deterministic urgency rules engine
  channels.py     — Delivery: terminal-notifier, Slack webhook, JSONL
  suppression.py  — Dedup, quiet hours, rate limiting
  watcher.py      — Bridge file watchdog (Recent Activity sections)
  config.py       — Settings, paths, constants
tests/
  test_server.py
  test_classifier.py
  test_suppression.py
  test_channels.py
  test_watcher.py
```

## Key Design Decisions

- Slack webhook URL from macOS Keychain (`security find-generic-password`), never hardcoded
- Quiet hours: 11 PM - 7 AM Pacific, push notifications suppressed and queued for morning delivery. Slack messages still deliver during quiet hours
- Dedup: same project + same classified level within 30 min = merge
- Rate limit: max 5 push/hour, max 20 Slack/hour, overflow batched into digest
- Bridge file watcher generates events from Recent Activity section diffs
- LaunchAgent at ~/Library/LaunchAgents/com.saagar.notification-hub.plist

## Rules

- No `any` types — use `unknown` equivalent patterns or narrow properly
- All timestamps in ISO 8601 UTC
- Event log at ~/.local/share/notification-hub/events.jsonl
- Existing hook behavior must be preserved — notification-hub POST is additive
