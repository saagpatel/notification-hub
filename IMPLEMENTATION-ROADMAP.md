# Implementation Roadmap — Notification Hub

## Current Baseline

All roadmap phases listed below are complete, and the post-build cleanup/hardening pass is also complete.
Phase 4 operator/config work has now started with a small, shipped first slice.

Current repo/runtime status:

- `main` is the active branch and matches `origin/main`
- GitHub Actions CI now runs `pytest`, `ruff`, and `pyright`
- `uv.lock` is committed and used as part of the verification baseline
- LaunchAgent-backed local runtime is healthy, with Slack delivery configured through Keychain
- An optional policy config can override classifier and suppression defaults
- A doctor command is available for local operator checks

For the best resume checkpoint, use `docs/CURRENT-STATE.md` first and treat the rest of this file
as implementation history rather than the primary current-state source.

---

## Phase 4: Operator Hardening + Configurable Policy

**Goal:** Make the daemon easier to trust and easier to tune without changing code for every policy tweak.

### Initial Slice Completed

- [x] Add optional TOML policy config at `~/.config/notification-hub/config.toml`
- [x] Externalize classifier keyword lists into the loadable policy layer
- [x] Externalize suppression settings for quiet hours, dedup window, and rate limits
- [x] Add a `notification-hub-doctor` operator command
- [x] Expand `GET /health/details` with config and suppression diagnostics
- [x] Add tests for policy config loading, diagnostics, and doctor output

### Next Likely Slice

- [x] Add a sample/default config artifact for easier first-time customization
- [x] Add event-log retention or rotation policy
- [x] Add a small end-to-end smoke command for runtime validation
- [x] Add project/source-specific routing rules on top of the policy layer
- [x] Add a bootstrap helper to copy the sample config into the live config path
- [x] Keep retention manual by default and document that operator posture

### Current Operator/Config Shape

- Policy config supports classifier, suppression, and ordered routing rules
- Routing rules can match by exact `project`/`source`, `project_prefix`, and title/body/text contains checks
- The first matching rule may force a classified level or disable push/Slack delivery, unless a rule
  opts into `continue_matching` so later rules can keep refining the decision
- `notification-hub policy-check` audits overlaps, shadowing, and no-op policy rules and suggests likely fixes
- `notification-hub policy-check` now also flags disabled automatic retention and ineffective
  `continue_matching` usage, plus redundant rules inside continue-matching chains
- `notification-hub explain` previews classification, routing, and delivery without sending anything
- `notification-hub bootstrap-config` installs the sample config locally without overwriting an
  existing config unless `--force` is used
- Retention now runs automatically on the daemon schedule, while the manual command still exists for
  immediate operator-triggered passes

## Phase 0: FastAPI Skeleton + JSONL Logging + Bridge File Watcher

**Goal:** Minimal running server that accepts events, logs them, and watches the bridge file.

### Tasks

- [x] **0.1** Set up pyproject.toml with dependencies (fastapi, uvicorn, watchdog, httpx, pydantic)
- [x] **0.2** Create Pydantic models: `Event` (source, level, title, body, project?, timestamp), `EventResponse`
- [x] **0.3** FastAPI app with `POST /events` endpoint — validates event, writes to JSONL log
- [x] **0.4** `GET /health` endpoint returning server status, uptime, event count
- [x] **0.5** JSONL writer — append events to `~/.local/share/notification-hub/events.jsonl`, auto-create directory
- [x] **0.6** Bridge file watcher using watchdog — monitors `~/.claude/projects/-Users-d/memory/claude_ai_context.md`
- [x] **0.7** Watcher diff logic — detect changes to "Recent Claude Code Activity" and "Recent Codex Activity" sections, parse new lines into events
- [x] **0.8** Wire watcher to event pipeline (watcher generates events that flow through the same POST path internally)
- [x] **0.9** Tests: server endpoint validation, JSONL write/read, watcher diff detection
- [x] **0.10** Manual smoke test: POST an event via curl, confirm JSONL entry

### Acceptance Criteria

- Server starts on 127.0.0.1:9199, accepts POST /events, returns 200 with event ID
- Invalid events return 422 with Pydantic validation errors
- All events written to events.jsonl with consistent schema
- Bridge file watcher detects changes and generates events
- pytest passes with >90% coverage on Phase 0 code

---

## Phase 1: Urgency Classification + Terminal-Notifier Delivery

**Goal:** Classify events by urgency and deliver urgent events via macOS push notification.

### Tasks

- [x] **1.1** Rules engine in classifier.py — keyword/pattern matching for urgent/normal/info
- [x] **1.2** Urgent keywords: "verification fail", "test regression", "eval degradation", "approval needed", "can_auto_archive=false", "security finding", "security audit"
- [x] **1.3** Normal keywords: "session complete", "automation report", "milestone", "bridge sync", "[SHIPPED]"
- [x] **1.4** Info: everything else, plus "can_auto_archive=true", "bridge file read", "status update"
- [x] **1.5** Terminal-notifier channel — shell out to terminal-notifier for urgent events with sound
- [x] **1.6** Classification pipeline: event → classify → route to appropriate channel
- [x] **1.7** Tests: classifier coverage for all keyword categories, channel delivery mocking
- [x] **1.8** Manual test: POST urgent event, confirm macOS notification appears with sound

### Acceptance Criteria

- Every event gets classified into exactly one level
- Urgent events trigger terminal-notifier with sound
- Normal and info events skip push notification
- Classifier is deterministic, no LLM calls, <1ms per classification

---

## Phase 2: Slack Webhook + Dedup/Rate-Limit

**Goal:** Slack delivery for urgent+normal events, with noise suppression.

### Tasks

- [x] **2.1** Slack webhook channel — POST formatted message to webhook URL
- [x] **2.2** Keychain integration — read Slack webhook URL from macOS Keychain via `security find-generic-password`
- [x] **2.3** Slack message formatting — source icon, level badge, project name, body, timestamp
- [x] **2.4** Dedup engine — track (project, level) pairs, merge if same combo within 30 min window
- [x] **2.5** Quiet hours — 11 PM to 7 AM Pacific, queue urgent events, deliver at 7 AM
- [x] **2.6** Rate limiter — 5 push/hour, 20 Slack/hour, overflow into batched digest
- [x] **2.7** Digest formatter — combine overflow events into single summary message
- [x] **2.8** Tests: dedup windowing, quiet hours boundary cases, rate limit overflow, Slack formatting
- [x] **2.9** Manual test: rapid-fire events to confirm dedup and rate limiting work

### Acceptance Criteria

- Urgent events go to both push and Slack
- Normal events go to Slack only
- Info events go to JSONL only
- Dedup merges duplicate (project, level) within 30 min
- Quiet hours suppress push/sound, queue for morning
- Rate limiter batches overflow into digest messages
- Slack webhook URL never appears in code, config, or logs

---

## Phase 3: Hook Modifications + LaunchAgent

**Goal:** Wire existing Claude Code and Codex hooks into the hub, set up auto-start.

### Tasks

- [x] **3.1** Modify `~/.claude/hooks/notify.sh` — add curl POST to localhost:9199 alongside existing terminal-notifier
- [x] **3.2** Modify `~/.codex/hooks/notify_local.py` — add urllib POST to localhost:9199 alongside existing osascript
- [x] **3.3** Both hooks: fire-and-forget POST (timeout 2s, ignore failures — existing behavior preserved)
- [x] **3.4** LaunchAgent plist at `~/Library/LaunchAgents/com.saagar.notification-hub.plist`
- [x] **3.5** LaunchAgent config: start on login, restart on crash, stdout/stderr to log files
- [x] **3.6** Install script or instructions for `launchctl load`
- [x] **3.7** Tests: hook POST payload format matches Event schema
- [x] **3.8** Integration test: full flow from hook → server → classification → delivery channel
- [x] **3.9** Manual test: trigger Claude Code stop hook, confirm event flows through hub

### Acceptance Criteria

- Existing hook behavior 100% preserved (terminal-notifier still fires, osascript still fires)
- Hub POST is additive and non-blocking (2s timeout, failure ignored)
- LaunchAgent starts server on login, restarts on crash
- Full pipeline works: hook fires → POST to hub → classify → route → deliver
- All tests pass, no regressions in existing notification behavior
