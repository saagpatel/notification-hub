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
uv sync --frozen --group dev
uv run --frozen uvicorn notification_hub.server:app --host 127.0.0.1 --port 9199 --reload
```

## Operator Commands

```bash
uv run notification-hub doctor
uv run notification-hub-doctor
uv run notification-hub-doctor --json
uv run notification-hub smoke
uv run notification-hub status
uv run notification-hub-status --json
uv run notification-hub inbox
uv run notification-hub-inbox --json
uv run notification-hub coordination-snapshot
uv run notification-hub-coordination-snapshot --json
uv run notification-hub coordination-snapshot --save-bridge-db
uv run notification-hub coordination-readiness
uv run notification-hub-coordination-readiness --json
uv run notification-hub coordination-console
uv run notification-hub-coordination-console --json
uv run notification-hub personal-ops-actions
uv run notification-hub-personal-ops-actions --json
uv run notification-hub action-proposal-dismissals
uv run notification-hub action-proposal-undismiss DISMISSAL_KEY --reason "signal is useful again"
uv run notification-hub action-proposal-group-outcome GROUP_KEY --outcome needs_follow_up --reason "operator follow-up needed"
uv run notification-hub operator-daily-state
uv run notification-hub operator-review-session
uv run notification-hub operator-review-session --save-report
uv run notification-hub operator-review-session-retention --keep 20
uv run notification-hub operator-review-session-retention --keep 20 --apply
uv run notification-hub operator-handoff-drill
uv run notification-hub personal-ops-actions --save-review-package
uv run notification-hub validate-action-package path/to/actions.json
uv run notification-hub personal-ops-import path/to/actions.json
uv run notification-hub personal-ops-import path/to/actions.json --enqueue
uv run notification-hub personal-ops-queue
uv run notification-hub personal-ops-queue --queue-id QUEUE_ID --status reviewed --reason "evidence checked"
uv run notification-hub personal-ops-queue --queue-id QUEUE_ID --status promoted --promotion-target-id SUGGESTION_ID --promotion-outcome accepted
uv run notification-hub personal-ops-queue-health
uv run notification-hub personal-ops-queue-review
uv run notification-hub-personal-ops-queue-health --json
uv run notification-hub personal-ops-outcome-sync-reminder
uv run notification-hub-personal-ops-outcome-sync-reminder --json
uv run notification-hub personal-ops-queue-burn-in
uv run notification-hub-personal-ops-queue-burn-in --json
uv run notification-hub personal-ops-queue-burn-in --save-report
uv run notification-hub personal-ops-queue-scenario
uv run notification-hub logs
uv run notification-hub-logs --json
uv run notification-hub burn-in --minutes 10
uv run notification-hub-burn-in --json
uv run notification-hub verify-runtime
uv run notification-hub verify-runtime --verify-slack
uv run notification-hub delivery-check --slack
uv run notification-hub-delivery-check --json --slack
uv run notification-hub-verify-runtime --json
uv run notification-hub policy-check
uv run notification-hub explain --source codex --level info --title "Test" --body "Approval needed"
uv run notification-hub bootstrap-config
uv run notification-hub retention --max-events 2000
```

The doctor command checks the local API, LaunchAgent presence, bridge file path, push notifier,
Slack Keychain setup, and policy-config load status.
The smoke command posts a harmless `info` event and verifies it lands in the live JSONL log.
The status command shows the compact day-to-day runtime view and suggests the next repair action
when something is degraded, including recent Slack delivery failures found in daemon logs.
The inbox command groups recent events by coordination intent so attention, blocked/waiting work,
ready work, completions, repeated rollups, and noisy producers are easy to scan.
The coordination-snapshot command combines inbox state and runtime status into bridge-ready JSON.
By default it only prints the snapshot; pass `--output path/to/snapshot.json` when you want a
durable file for a bridge-db import step.
Pass `--save-bridge-db` when you intentionally want to insert the snapshot into bridge-db as a Codex
system snapshot. Use `--bridge-db-path` to target a non-default database during testing.
The coordination-readiness command combines runtime status, queue state, and saved queue burn-in
report history into one compact expansion gate. It returns `fix_noise_first`, `keep_burning_in`, or
`ready_to_expand` without applying work.
The coordination-console command is the first compact expansion after that gate. It brings
readiness, action proposals, queue state, promoted-outcome reminders, burn-in report history, next
real signal state, and the next safe action into one read-only summary. It also classifies proposal
lineage as new, queued, promoted, follow-up, resolved, or ignored so already-handled proposals stay
visible as history without being treated as fresh work. Its operator guide names the current stage and exact
safe commands for saving, validating, queueing, promoting, or outcome-syncing handoffs while keeping
apply behavior outside notification-hub. If a handoff is already queued or waiting on a promoted
outcome, the console keeps that queue lifecycle as the next action before returning to readiness
cleanup or new package review.
The console also includes a proposal-review summary that groups active proposals by source, project,
intent, priority, and state, so the operator can tell when to review one proposal alone versus
staging a small batch package for inspection. The `/review` surface can save, queue, or locally
dismiss one proposal group, and it can record a local group outcome such as `needs_follow_up`,
`accepted`, `rejected`, `snoozed`, or `superseded`. Queueing still only creates notification-hub
handoff records and does not create personal-ops tasks.
When a group's latest recorded outcome is terminal handled history, matching action IDs and stable
proposal keys are treated as handled rather than fresh active proposals. `needs_follow_up` stays in
follow-up history, `snoozed` stays snoozed, `accepted` is resolved history, and `rejected` or
`superseded` are closed history. A later save-only package inspection does not reopen that group,
and repeated rollups can keep that handled state even when their newest evidence event rotates.
Queueing, promotion, dismissal, or a different proposal key can still create a new actionable state.
For personal-ops mail approval groups, Proposal Review adds a local route recommendation that
separates concrete reply candidates from repeated phase or workflow chatter. The recommendation is
advisory only; it never promotes, suppresses, or sends by itself. The review controls can also save
or queue just the `promote` route, or locally dismiss just the `suppress` route, so mixed mail batches
do not have to be handled as one all-or-nothing group.
The same recommendation now exposes a separate Operator Decision Required lane for real outbound
mail approvals, so approval requests stay visible as operator work instead of being mixed into
noise-review follow-up. The approval lane packages every approval-titled mail item except known
phase/workflow chatter, while the narrower promote route remains available for concrete reply
candidates.
The personal-ops-actions command turns inbox rollups into action proposals for review. It does not
write to personal-ops; pass `--output path/to/actions.json` when you want a handoff file.
It scans a deeper candidate set than the display limit, so dismissed or policy-covered rollups do not
hide a real operator signal that appears just below them.
Each proposal now includes a stable dismissal key, and `action-proposal-dismiss` can hide a known
repeated proposal from future console/action exports without deleting the underlying event log.
`action-proposal-dismissals` lists active or inactive dismissal records, while
`action-proposal-undismiss` reactivates a proposal by appending a tombstone rather than rewriting
history.
The operator-daily-state command builds a resume-ready local snapshot across runtime health, queue
health, Coordination Console next signal, burn-in, dismissals, and the current rich/thin outcome
quality summary. Pass `--save-report` when you want a timestamped JSON report under
`~/.local/share/notification-hub/operator-state-reports/`.
The operator-review-session command summarizes recent local review activity, including grouped
proposal saves, queues, dismissals, outcomes, and queue follow-through. It is read-only and mirrors
the review-session summary shown in `/review`; pass `--save-report` when you want a timestamped JSON
audit report under `~/.local/share/notification-hub/operator-review-session-reports/`. Saved
review-session reports can be listed and inspected from `/review` for a compact session timeline,
and the review page surfaces the latest saved session as its own at-a-glance panel.
The operator-review-session-retention command prunes old saved review-session reports; it defaults to
a dry run and only deletes files when `--apply` is passed. The `/review` page also shows the same
retention pressure as a read-only summary, so cleanup stays explicit.
The operator-handoff-drill command runs the temporary queue lifecycle plus queue burn-in as a
non-applying rehearsal before using the same review flow for a real handoff. The `/review` drill
button saves the burn-in proof by default and shows rich-evidence readiness, live-promotion
readiness, and the saved report status.
The `/review` page also includes a Real Signal Readiness lane that combines active proposals,
handled follow-ups, queue state, latest saved proof, the next safe command, and a rich-outcome
guardrail so expansion stays operator-mediated until a real rich-evidence handoff resolves.
It also shows a first-rich-handoff checklist and compares the latest saved burn-in proof against the
previous proof for readiness and noise drift.
Pass `--save-review-package` when you want notification-hub to stage a local review package under
`~/.local/share/notification-hub/action-exports/`; this still does not import or apply actions.
The validate-action-package command checks a saved review package before any future import/apply
step consumes it.
Burn-in keeps repeated signatures visible for inspection, but filters active noise candidates through
the configured `[[noise.rules]]` so policy-covered repeats do not block coordination readiness.
The personal-ops-import command validates the package and stops before mutation by default. Pass
`--enqueue` to add valid action proposals to a local personal-ops import queue under
notification-hub runtime state; queued items are handoff records only and are not personal-ops tasks,
approvals, sends, or applied changes.
The personal-ops-queue command lists and updates queued handoffs through explicit lifecycle states:
`queued`, `reviewed`, `rejected`, `snoozed`, `superseded`, and `promoted`. Marking an item
`reviewed` is now treated as a reviewed-only closeout lane: evidence was checked and no downstream
personal-ops promotion is required. Marking an item `promoted` records that an operator-mediated
personal-ops task suggestion was created; it does not create that suggestion by itself. Promotion
records can also store the personal-ops suggestion id and final `pending`, `accepted`, `rejected`, or
`ignored` outcome.
The Coordination Console treats reviewed, follow-up, and snoozed handoffs as handled history, so
they do not block readiness once queue health is clean. Proposal Review also breaks handled history
into reviewed-only, follow-up, resolved, closed, and snoozed counts so reviewed-but-not-promoted work
is visible. Handled mail follow-ups are summarized separately with rich/thin evidence counts, so
repeated handled mail echoes remain reviewable history without looking like fresh operator work.
Handled proposals also include a lineage reason plus stable-key and evidence-rotation flags, so the
console can explain when a newer event is still covered by an earlier `needs_follow_up` outcome.
The console also reports promoted handoff outcome quality by rich versus thin evidence and narrows
the monitor posture to notify only on active proposals, queued handoffs, pending promoted outcomes,
runtime degradation, or repeated diagnostic echoes.
The personal-ops-queue-health command is the normal maintenance check for this queue. It reports
queued item age, promoted handoffs still waiting on downstream outcome sync, stale pending
promotions, and the next safe operator commands without applying work.
The personal-ops-queue-review command groups queued handoffs into review batches, highlights
operator-decision approval counts, and shows the next local review command without approving,
sending, or changing downstream systems.
The personal-ops-outcome-sync-reminder command is a narrower read-only reminder for promoted
handoffs that still need downstream personal-ops outcome sync. It returns `status: warn` when a
reminder should be shown, but still leaves syncing to the operator.
The personal-ops-queue-burn-in command combines queue health, the temporary queue lifecycle
scenario, and recent runtime burn-in into one non-applying readiness report. Use it before promoting
real handoffs or after syncing a downstream personal-ops outcome. The report now states the
outcome-sync posture explicitly: notification-hub can show pending or stale promoted outcomes, but
the operator still owns creating and recording the downstream personal-ops work. Pass
`--save-report` when you want a timestamped local report under
`~/.local/share/notification-hub/burn-in-reports/`.
The personal-ops-queue-scenario command runs a temporary end-to-end queue lifecycle, including a
promoted handoff with an accepted outcome, without touching the real operator queue.
See `docs/PRODUCT-BOUNDARY.md` for the current ownership split between notification-hub,
personal-ops, and bridge-db.
The logs command shows recent stored events, daemon stdout/stderr tails, and a summary of accepted
versus rejected `/events` posts plus Slack delivery failures without changing local runtime state.
The burn-in command summarizes recent accepted/rejected event posts and repeated event signatures
so noisy producers are easy to spot. Validation-error counts are scoped to the latest visible daemon
start so fixed pre-restart errors do not keep appearing as current burn-in failures. Recent Slack
delivery failures now degrade burn-in health so configured-but-broken delivery does not look clean.
Repeated-event candidates now include review-only noise-rule suggestions so policy changes can be
copied deliberately instead of inferred from raw event rows.
The verify-runtime command combines doctor, policy-check, `/health/details`, runtime wiring checks,
and recent burn-in health into one read-only report by default. Pass `--include-smoke` when you
intentionally want it to post a harmless smoke event too. Pass `--verify-slack` or `--verify-push`
when you intentionally want to send one real delivery-check notification through that channel.
The delivery-check command runs the same explicit transport checks directly without the rest of
the runtime report.
The policy-check command inspects the current policy config for overlapping keywords, shadowed
routing rules, no-op rules, and drift between the live noise rules and repo sample before they cause
confusing behavior. It also suggests likely fixes for each warning it reports.
The explain command shows how a sample event would classify, route, and deliver without posting it
to the live daemon or sending any notifications.
The bootstrap command copies the repo sample policy file into `~/.config/notification-hub/config.toml`
without overwriting an existing config unless you pass `--force`.
The retention command archives older log entries into `~/.local/share/notification-hub/archive/`.
The daemon now also performs the same retention check automatically on a schedule, while the manual
command remains available when you want to force a run immediately.

## Event Contract

Accepted event sources are `codex`, `cc`, `claude_ai`, `bridge_watcher`, `personal-ops`,
and `notion-os`.
Accepted levels are `urgent`, `normal`, and `info`; incoming `warn` and `warning` aliases are
normalized to `normal`.
Events may optionally include an `intent` value for coordination semantics. Supported intents are
`needs_attention`, `blocked`, `waiting_on_user`, `ready_to_review`, `ready_to_merge`,
`handoff_created`, `automation_failed`, `completed`, and `informational`. When omitted, the inbox
uses deterministic title/body/source-level rules to infer intent.
Events may also include optional scalar `context` values for operator evidence, such as mail
`thread_id`, `draft_id`, `message_id`, or `approval_id`. The hub stores and displays this context in
rollups and review packages, but does not use it to send, approve, or mutate external systems.
Action proposals also include an `evidence_quality` value. `rich` means the latest event has both a
mail/thread anchor and a concrete work-item ID; `thin` means the proposal still needs more operator
inspection before promotion.
For mail proposal routing, promotion-looking signals only enter the promote lane when evidence is
rich. Thin promotion-looking signals stay in follow-up until the source emits enough context.
Proposal Review also reports promotion readiness for each active group, including which action IDs
are ready to queue and which are blocked by thin evidence or workflow chatter.
The inbox report also includes `rollups` for repeated source/project/title/body patterns, so repeated
approval drafts and completion pings can be reviewed as one grouped signal.
Personal-ops action exports are proposal-only: they include priority, state, suggested next action,
evidence IDs, and optional evidence context, but they do not create tasks, send messages, approve
drafts, or mutate external systems.
Review packages are local JSON files for an operator-mediated import step. They are intentionally
separate from any future personal-ops apply command.
Validation checks the schema version, required action fields, duplicate action IDs, priority/state
values, action counts, and optional scalar evidence context without mutating personal-ops.
The import stub reports `applied: false` even when validation passes, so no personal-ops task,
approval, or send path is touched.
The local review surface is available at `http://127.0.0.1:9199/review` while the daemon is running.
It shows runtime state, inbox rollups, action proposals, and the current trust boundary without
mutating local state.
The review page can also stage a review package, show recent saved review packages, inspect package
actions/evidence, show queue lineage for already queued packages, queue import handoff items, filter
queued/promoted/pending/stale/resolved handoffs, mark queued items reviewed/rejected/snoozed/promoted,
show pending outcome-sync reminders, list and inspect saved burn-in reports, list/undismiss action
proposal dismissals, show the Coordination Console next signal, run the temporary operator handoff
drill, delete saved review packages, validate the latest staged or saved package, and show the
Coordination Console operator guide plus proposal-review grouping. It also surfaces sample-vs-live
policy drift and the latest saved review-session summary. The Proposal Review controls can save a
group package, queue a group package for operator review, or dismiss a group locally, and each group
action is recorded in local group-history JSONL so later console refreshes still show what happened.
A group outcome can also be recorded locally after review. These controls still do not apply,
approve, send, or mutate personal-ops.
Mail proposal groups include a route recommendation with promote, suppress, and follow-up counts so
the operator can split mixed batches before queueing or dismissing them. Route-aware group controls
still only stage local packages, queue local handoff records, or append local dismissals; they do not
send email, create personal-ops tasks, or approve work.
The review page also includes a Noise Candidate Review panel backed by
`/review/noise-candidates`; it highlights repeated burn-in signatures with decision hints, while
keeping real mail approvals in Operator Decision Required instead of suggesting automatic policy
suppression.
Coordination snapshots target bridge-db's `codex` snapshot shape: the emitted
`bridge_snapshot` object can be passed as snapshot data after operator review, or saved directly
with the explicit `--save-bridge-db` flag.
Exact repeated producer bursts that match configured noise rules are accepted by the API but
suppressed before JSONL storage and notification delivery when they repeat inside the configured
noise window. Without a live policy config, the built-in default keeps suppressing repeated
`personal-ops` reminder bursts.

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

[[noise.rules]]
source = "personal-ops"
project = "personal-ops"
title_contains = "approval expires soon"
body_contains = "approval expires soon: review or cancel"
level = "info"
window_minutes = 10

[[noise.rules]]
source = "personal-ops"
project = "personal-ops"
title_contains = "daemon started"
level = "info"
window_minutes = 10

[[noise.rules]]
source = "personal-ops"
project = "personal-ops"
title_contains = "system needs attention"
body_contains = "run personal-ops doctor"
window_minutes = 30

[[noise.rules]]
source = "personal-ops"
project = "mail"
title_contains = "approval requested"
body_contains = "phase 34 secondary approval"
level = "urgent"
window_minutes = 30

[[noise.rules]]
source = "personal-ops"
project = "mail"
title_contains = "draft ready"
body_contains = "phase 34 secondary approval"
level = "info"
window_minutes = 30

[[noise.rules]]
source = "notion-os"
title_contains = "external-signal-sync complete"
level = "info"
window_minutes = 10

[[noise.rules]]
source = "notion-os"
title_contains = "control-tower-sync complete"
level = "info"
window_minutes = 10

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
uv run --directory mcp_server --frozen pytest
uv run --frozen ruff check
uv run --frozen pyright
```

The root test suite uses temporary runtime paths, so local verification does not write into the live
machine event log or watch the real bridge file. The MCP server smoke tests live in a separate uv
project under `mcp_server/`, so they are run with `uv run --directory mcp_server --frozen pytest`
locally and in CI.
The committed `uv.lock` file keeps local installs and CI in sync.

Runtime diagnostics:

```bash
curl http://127.0.0.1:9199/health
curl http://127.0.0.1:9199/health/details
uv run --frozen notification-hub-doctor
uv run --frozen notification-hub status
uv run --frozen notification-hub inbox
uv run --frozen notification-hub coordination-snapshot
uv run --frozen notification-hub coordination-snapshot --save-bridge-db
uv run --frozen notification-hub coordination-readiness
uv run --frozen notification-hub coordination-console
uv run --frozen notification-hub personal-ops-actions
uv run --frozen notification-hub action-proposal-dismiss DISMISSAL_KEY --reason "known repeated test signal"
uv run --frozen notification-hub action-proposal-dismissals
uv run --frozen notification-hub action-proposal-undismiss DISMISSAL_KEY --reason "signal is useful again"
uv run --frozen notification-hub personal-ops-actions --save-review-package
uv run --frozen notification-hub validate-action-package path/to/actions.json
uv run --frozen notification-hub personal-ops-import path/to/actions.json
uv run --frozen notification-hub personal-ops-import path/to/actions.json --enqueue
uv run --frozen notification-hub personal-ops-queue
uv run --frozen notification-hub personal-ops-queue --queue-id QUEUE_ID --status rejected --reason "duplicate"
uv run --frozen notification-hub personal-ops-queue-health
uv run --frozen notification-hub-personal-ops-queue-health --json
uv run --frozen notification-hub personal-ops-outcome-sync-reminder
uv run --frozen notification-hub-personal-ops-outcome-sync-reminder --json
uv run --frozen notification-hub personal-ops-queue-burn-in
uv run --frozen notification-hub-personal-ops-queue-burn-in --json
uv run --frozen notification-hub personal-ops-queue-burn-in --save-report
uv run --frozen notification-hub personal-ops-queue-scenario
uv run --frozen notification-hub operator-daily-state
uv run --frozen notification-hub operator-review-session
uv run --frozen notification-hub operator-review-session --save-report
uv run --frozen notification-hub operator-review-session-retention --keep 20
uv run --frozen notification-hub operator-review-session-retention --keep 20 --apply
uv run --frozen notification-hub operator-handoff-drill
uv run --frozen notification-hub logs
curl http://127.0.0.1:9199/review
curl http://127.0.0.1:9199/review/packages
curl http://127.0.0.1:9199/review/package/personal-ops-actions-YYYYMMDD-HHMMSS.json
curl http://127.0.0.1:9199/review/operator-review-session-retention
curl -X POST http://127.0.0.1:9199/review/package/personal-ops-actions-YYYYMMDD-HHMMSS.json/queue
curl http://127.0.0.1:9199/review/import-queue
curl http://127.0.0.1:9199/review/import-queue-review
curl http://127.0.0.1:9199/review/coordination-readiness
curl http://127.0.0.1:9199/review/coordination-console
curl http://127.0.0.1:9199/review/noise-candidates
curl http://127.0.0.1:9199/review/policy-check
curl http://127.0.0.1:9199/review/outcome-sync-reminder
curl http://127.0.0.1:9199/review/action-proposal-dismissals
curl -X POST http://127.0.0.1:9199/review/action-proposal/DISMISSAL_KEY/dismiss \
  -H 'Content-Type: application/json' \
  -d '{"reason":"known repeated test signal"}'
curl -X POST http://127.0.0.1:9199/review/action-proposal/DISMISSAL_KEY/undismiss \
  -H 'Content-Type: application/json' \
  -d '{"reason":"signal is useful again"}'
curl http://127.0.0.1:9199/review/operator-daily-state
curl http://127.0.0.1:9199/review/operator-review-session
curl 'http://127.0.0.1:9199/review/operator-review-session?save_report=true'
curl http://127.0.0.1:9199/review/operator-review-session-reports
curl http://127.0.0.1:9199/review/operator-review-session-report/operator-review-session-YYYYMMDD-HHMMSS.json
curl -X POST http://127.0.0.1:9199/review/operator-handoff-drill
curl -X PATCH http://127.0.0.1:9199/review/import-queue/QUEUE_ID \
  -H 'Content-Type: application/json' \
  -d '{"status":"reviewed","reason":"evidence checked"}'
curl -X DELETE http://127.0.0.1:9199/review/package/personal-ops-actions-YYYYMMDD-HHMMSS.json
uv run --frozen notification-hub verify-runtime
uv run --frozen notification-hub delivery-check --slack
uv run --frozen notification-hub policy-check
uv run --frozen notification-hub explain --source codex --level info --title "Test" --body "Approval needed"
uv run --frozen notification-hub smoke
uv run --frozen notification-hub retention --max-events 2000
```

Runtime change checklist:

- Run the static gates before shipping code changes: lock check, tests, Ruff, and Pyright.
- Run `notification-hub verify-runtime` before changing live launcher, hook, policy, or delivery
  behavior.
- Use `notification-hub verify-runtime --include-smoke` only when you intentionally want a real
  POST-to-log smoke event.
- Use `notification-hub verify-runtime --verify-slack`, `--verify-push`, or
  `notification-hub delivery-check` only when you intentionally want a real delivery notification.
- Confirm GitHub Actions passes after pushing to `main`.

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
