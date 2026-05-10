# Current State

Last updated: 2026-05-10

## Snapshot

`notification-hub` is in a healthy operating state after the latest runtime restart and policy
tuning pass.

- Local `main` matches `origin/main`.
- GitHub Actions CI is configured and passing on `main`.
- The daemon is running locally via LaunchAgent on `127.0.0.1:9199`.
- Slack delivery is configured through macOS Keychain, and recent post-restart runtime checks show
  zero scoped Slack delivery failures.
- Policy-based runtime overrides are now supported through an optional config file.
- A local doctor command is available for operator checks.
- The repo now also includes a sample policy config, a smoke command, and a log-retention command.
- Policy config now also supports ordered routing rules, and a bootstrap command can copy the sample
  config into the live config path.
- Runtime wiring now has repo-owned LaunchAgent and hook templates under `ops/`.
- A compact local status command is available for the day-to-day runtime view.
- A compact local inbox command is available for recent coordination intent: attention,
  waiting/blocked, ready, completed, repeated rollups, and noisy producers.
- A bridge-ready coordination snapshot command now combines inbox state and runtime status into
  JSON that can be reviewed or explicitly saved into bridge-db as Codex snapshot data.
- A proposal-only personal-ops action export turns inbox rollups into reviewable action records
  without writing to personal-ops.
- Action exports can now be staged as local review packages under notification-hub runtime state,
  still without importing or applying them.
- Saved action review packages can be validated before any future personal-ops import/apply step.
- A personal-ops import stub now validates packages and refuses mutation, preserving the operator
  gate for any future apply behavior.
- Valid review packages can now be explicitly queued into a local personal-ops import queue. Queue
  items are durable handoff records under notification-hub runtime state, not personal-ops tasks or
  applied changes.
- Queued personal-ops handoffs now have explicit lifecycle states: queued, reviewed, rejected,
  snoozed, superseded, and promoted. Queue health is visible in status and runtime verification.
- The Coordination Console now treats reviewed and snoozed handoffs as handled history instead of
  active lifecycle blockers once queue health is clean.
- The first real operator-mediated promotion proof has completed, and the current live queue has no
  queued, pending, or stale promoted handoff outcomes.
- Queue maintenance now has a dedicated `personal-ops-queue-health` command that reports queued
  item age, promoted handoffs still waiting on outcome sync, stale pending outcomes, and the next
  safe operator commands without applying work.
- A dedicated `personal-ops-outcome-sync-reminder` command now reports pending or stale promoted
  handoff outcomes as a read-only reminder without syncing personal-ops itself.
- A queue burn-in command now combines queue health, the temporary queue lifecycle scenario, and
  recent runtime burn-in into one non-applying readiness report for live operator handoffs. It now
  states that outcome sync remains operator-mediated and that notification-hub reports pending or
  stale outcomes without syncing personal-ops itself.
- Queue burn-in can now save a timestamped local report under notification-hub runtime state with
  `--save-report`, giving real-use promotion checks a durable artifact without applying work.
- Saved queue burn-in reports can now be listed and inspected from `/review`, so real-use evidence
  remains visible after the command that generated it has finished.
- A compact `coordination-readiness` command and `/review/coordination-readiness` endpoint now
  combine runtime health, queue state, and saved burn-in report history into a deterministic
  `fix_noise_first`, `keep_burning_in`, or `ready_to_expand` decision.
- A compact `coordination-console` command and `/review/coordination-console` endpoint now summarize
  readiness, action proposals, queue state, promoted-outcome reminders, burn-in report history, and
  the next safe action in one read-only view. The console separates active proposal lineage from
  handled history so resolved or ignored handoffs stop reappearing as fresh work, includes the next
  real signal lane, and includes a guided operator stage with exact safe commands for the current
  handoff state. It now also includes a proposal-review summary that groups active proposals by
  source, project, intent, priority, and state so the operator can distinguish single-proposal review
  from a small batch package. Group controls can save a scoped review package, queue that group into
  the local handoff queue, or locally dismiss the group without applying personal-ops work. Each
  group action now appends local group-history JSONL so the console can show recent group lifecycle
  state after a save, queue, dismiss, or explicit local outcome decision.
- Action proposal export now scans a deeper candidate set than the display limit, so dismissed or
  policy-covered rollups cannot crowd out real lower-ranked operator signals from the default view.
- Action proposal dismissals can now be listed, inspected, and undismissed through CLI and `/review`
  without deleting dismissal history.
- An `operator-daily-state` command and `/review/operator-daily-state` endpoint now build a
  resume-ready local state snapshot across runtime health, queue health, Coordination Console next
  signal, burn-in, and dismissals. The command can save timestamped JSON reports under local
  notification-hub runtime state.
- An `operator-review-session` command and `/review/operator-review-session` endpoint now summarize
  recent local review activity across grouped proposal saves, queues, dismissals, outcomes, and
  queue follow-through. The `/review` Operator State panel shows this alongside the daily state, and
  `--save-report` or `save_report=true` writes timestamped local JSON audit reports.
- An `operator-handoff-drill` command and `/review/operator-handoff-drill` endpoint now run the
  temporary handoff lifecycle plus queue burn-in as a non-applying rehearsal.
- The sample policy now includes the repeated `personal-ops` daemon-start and `notion-os`
  control-tower sync signals seen during live burn-in, keeping evidence-based noise tuning in the
  repo without changing machine-local config.
- The sample and live policy now also cover repeated personal-ops mail `Send Succeeded` events for
  `Console reply needed`, after a real-use route-aware review pass showed them as success chatter
  rather than operator work.
- A localhost-only review page is available at `/review` on the daemon. It shows runtime health,
  inbox rollups, action proposals, and trust state without applying anything.
- The review page now includes Operator Focus, Coordination Readiness, and Coordination Console
  summaries that put the current action state, expansion gate, next real signal, and next safe action
  first. A Proposal Review section shows grouped active proposals before a package is queued and can
  save, queue, mark as needing follow-up, or dismiss one proposal group at a time. It also shows
  recent group-history entries so a refresh does not hide the last grouped action.
- Proposal Review now adds advisory mail routing recommendations for personal-ops mail approval
  groups, with promote, suppress, and follow-up counts. This helps split concrete reply candidates
  from repeated phase/workflow chatter without auto-promoting or auto-suppressing anything.
- Proposal Review group controls are now route-aware for mixed mail batches: operators can save or
  queue only the `promote` route, or locally dismiss only the `suppress` route, while leaving
  follow-up candidates visible for separate inspection.
- The review page can stage a local review package, list recent saved review packages, inspect
  package actions/evidence plus queue lineage, queue import handoff items, filter
  queued/promoted/pending/stale/resolved handoffs, mark queued items reviewed/rejected/snoozed/promoted,
  show pending outcome-sync reminders, list and undismiss action proposal dismissals, show the daily
  operator state, run the temporary handoff drill, delete saved review packages, and validate the
  latest staged or saved package while keeping apply behavior disabled.
- A local logs command is available for recent event and daemon log inspection, including accepted
  versus rejected `/events` counts from the visible daemon tail.
- A local burn-in command is available for recent accepted/rejected event counts and repeated
  event signatures, with validation-error summaries scoped to the latest visible daemon start.
  Burn-in now reports health failures separately from repeated-event noise candidates and includes
  Slack-eligible volume by source/level. Repeated-event candidates now include review-only
  noise-rule suggestions, and recent Slack delivery failures now degrade burn-in health.
- Explicit delivery checks are available through `notification-hub delivery-check` and the
  `verify-runtime --verify-slack` / `--verify-push` flags, so Slack and push transport can be
  tested intentionally without making default verification noisy.
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
- Runtime notification hooks clamp outgoing payloads to the event schema before posting.
- Event validation failures are logged with sanitized field/type details, not request bodies.
- `personal-ops` is accepted as a first-class event source.
- `notion-os` is accepted as a first-class event source, and incoming `warn`/`warning` level aliases
  normalize to `normal`.
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
- Hardened Claude and Codex notification hooks against oversized title/body/project fields.
- Added sanitized `/events` validation diagnostics so future `422` investigations identify the
  failing field without exposing notification text.
- Added `personal-ops` to the accepted source contract after live diagnostics showed that producer
  was being rejected.
- Added `notion-os` and warning-level normalization after burn-in diagnostics showed those producer
  shapes were active.
- Added daemon access summary counts to `notification-hub logs`.
- Added narrow intake burst suppression for exact repeated `personal-ops` reminder events before
  they are written to the JSONL log.
- Added `notification-hub burn-in` as a read-only recent-runtime summary for noisy producers.
- Scoped burn-in/log validation-error summaries to the latest visible daemon start so resolved
  pre-restart `422` diagnostics do not appear as current failures.
- Added configurable noise rules so repeated accepted producer events can be suppressed by
  source/project/text/level/window instead of relying only on hard-coded producer behavior.
- Added Slack delivery failure detection to daemon log summaries so `logs`, `burn-in`,
  `verify-runtime`, and `status` no longer treat a configured webhook as proof of working delivery.
- Added opt-in Slack/push delivery verification for operator-requested transport checks.
- Added optional event `intent` and deterministic intent inference so the hub can group recent
  work by coordination state instead of only by notification level.
- Added `notification-hub inbox` as the first operator-facing coordination view.
- Added `notification-hub coordination-snapshot` as the first bridge-ready export surface for
  durable coordination memory.
- Added explicit `coordination-snapshot --save-bridge-db` support so bridge-db writes are possible
  but never happen during default read-only checks.
- Added inbox rollups so repeated approval, draft, and completion patterns are grouped into compact
  operator signals.
- Added `notification-hub personal-ops-actions` as the first personal-ops handoff surface. It emits
  action proposals with priority, state, suggested next action, and evidence IDs, but does not mutate
  personal-ops.
- Added stable action proposal dismissal keys plus `action-proposal-dismiss` and a `/review` Dismiss
  control so known repeated proposals can be hidden locally without deleting event history or applying
  downstream work.
- Burn-in now keeps repeated signatures visible while filtering active noise candidates through the
  configured `[[noise.rules]]`, which prevents already-tuned repeated signals from blocking readiness.
- Added `personal-ops-actions --save-review-package` so action proposals can be saved for an
  operator-mediated import step.
- Added `validate-action-package` so saved review packages can be checked for schema, required
  fields, duplicate action IDs, and priority/state validity.
- Added `personal-ops-import` as a non-mutating apply boundary: it validates a package and reports
  `applied: false` until an explicit personal-ops integration exists.
- Added `personal-ops-import --enqueue` and the local import queue JSONL file so valid review
  packages can create durable handoff items while still reporting `applied: false`.
- Added the first local review UI at `GET /review`, backed by read-only `GET /review/data`.
- Added review UI controls backed by `POST /review/save-package` and
  `POST /review/validate-package`; both preserve `applied: false`.
- Added `GET /review/packages` and recent package display so saved review packages remain visible
  across daemon restarts.
- Added `GET /review/package/{name}` and package detail display for action proposals, evidence IDs,
  validation errors, and any existing queue lineage without importing or applying anything.
- Added `DELETE /review/package/{name}` so saved review packages can be cleaned up without touching
  personal-ops.
- Added `POST /review/package/{name}/queue` and `GET /review/import-queue` so the review UI can
  enqueue and display personal-ops handoff items without applying them.
- Added promotion outcome tracking so promoted handoffs can retain the personal-ops suggestion id
  and final `pending`, `accepted`, `rejected`, or `ignored` outcome.
- Added `personal-ops-queue-health` so routine maintenance can detect queued age, pending promotion
  outcome sync, stale promoted-pending handoffs, and the next safe non-mutating commands.
- Added `personal-ops-outcome-sync-reminder` so pending or stale promoted handoff outcomes can be
  surfaced directly without creating, accepting, rejecting, or syncing personal-ops work.
- Added `personal-ops-queue-burn-in` so queue lifecycle readiness, live queue attention, and recent
  runtime noise can be checked together before or after real operator promotion.
- Added explicit queue burn-in outcome-sync posture so reports make clear that notification-hub
  tracks pending/stale promoted outcomes but does not create or sync personal-ops work.
- Added `personal-ops-queue-burn-in --save-report` so operator burn-in checks can be kept as
  timestamped JSON evidence under local notification-hub runtime state.
- Added saved burn-in report history to `/review`, including readiness, queue, runtime, and noise
  summaries for each local report.
- Added a sample `personal-ops` daemon-start noise rule after live burn-in surfaced it as a repeated
  informational producer.
- Added a narrow `personal-ops` mail success noise rule for repeated `Console reply needed`
  `Send Succeeded` events after route-aware review confirmed they should not block readiness.
- Added review UI Operator Focus so the top of `/review` names the current next action before the
  operator scans packages, rollups, or queue detail.
- Added review UI queue-health summary and filters for pending outcome, stale outcome, queued,
  promoted, resolved, and open handoffs.
- Added lineage-aware Coordination Console action counts so active proposals and handled proposal
  history are visible separately in CLI, JSON, and `/review`.
- Added a read-only Coordination Console operator guide so package review, queue review, promotion,
  outcome sync, and monitor states expose the current stage and safe next commands.
- Added Coordination Console proposal-review grouping in CLI, JSON, and `/review` so multiple active
  proposals can be reviewed as one operator batch without applying personal-ops work.
- Added Proposal Review group controls in `/review` so an operator can save a scoped group package,
  queue it into the local handoff queue, or dismiss the group locally while keeping personal-ops
  mutations outside notification-hub.
- Added durable Proposal Review group history so save, queue, and dismiss actions append local JSONL
  evidence and appear in CLI, JSON, and `/review` lifecycle summaries.
- Added local Proposal Review group outcomes so grouped work can be marked `accepted`, `rejected`,
  `snoozed`, `superseded`, or `needs_follow_up` without applying downstream work.
- Added advisory mail route recommendations to Proposal Review so mixed mail approval batches show
  whether they contain promote candidates, suppression candidates, or follow-up-only items.
- Added route-aware Proposal Review actions so a mixed mail batch can be split into local promote,
  suppress, and follow-up routes without sending mail or creating downstream personal-ops work.
- Added action proposal dismissal listing/undismiss commands and `/review` controls so temporarily
  hidden proposals can be audited or reactivated without deleting dismissal history.
- Added operator daily-state and handoff-drill commands plus `/review` endpoints so local operators
  can see the next real signal and run the temporary handoff lifecycle from the review surface.
- Added `personal-ops-queue-scenario` as a temporary end-to-end lifecycle proof that does not touch
  the real operator queue.
- Added `docs/PRODUCT-BOUNDARY.md` to keep notification-hub, personal-ops, and bridge-db ownership
  explicit before expanding the product surface.

## Verified Baseline

The following checks were re-run after cleanup and merge:

```bash
uv lock --check
uv run --frozen pytest
uv run --frozen ruff check
uv run --frozen pyright
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
uv run --frozen notification-hub action-proposal-group-outcome GROUP_KEY --outcome needs_follow_up --reason "operator follow-up needed"
uv run --frozen notification-hub personal-ops-actions --save-review-package
uv run --frozen notification-hub validate-action-package path/to/actions.json
uv run --frozen notification-hub personal-ops-import path/to/actions.json
uv run --frozen notification-hub personal-ops-import path/to/actions.json --enqueue
uv run --frozen notification-hub personal-ops-queue
uv run --frozen notification-hub personal-ops-queue --queue-id QUEUE_ID --status reviewed --reason "evidence checked"
uv run --frozen notification-hub personal-ops-queue-health
uv run --frozen notification-hub-personal-ops-queue-health --json
uv run --frozen notification-hub personal-ops-outcome-sync-reminder
uv run --frozen notification-hub-personal-ops-outcome-sync-reminder --json
uv run --frozen notification-hub personal-ops-queue-burn-in
uv run --frozen notification-hub-personal-ops-queue-burn-in --json
uv run --frozen notification-hub personal-ops-queue-burn-in --save-report
uv run --frozen notification-hub personal-ops-queue-scenario
uv run --frozen notification-hub logs
curl http://127.0.0.1:9199/review
curl http://127.0.0.1:9199/review/packages
curl http://127.0.0.1:9199/review/package/personal-ops-actions-YYYYMMDD-HHMMSS.json
curl -X POST http://127.0.0.1:9199/review/package/personal-ops-actions-YYYYMMDD-HHMMSS.json/queue
curl http://127.0.0.1:9199/review/import-queue
curl http://127.0.0.1:9199/review/outcome-sync-reminder
curl -X POST http://127.0.0.1:9199/review/action-proposal/DISMISSAL_KEY/dismiss -H 'Content-Type: application/json' -d '{"reason":"known repeated test signal"}'
curl -X PATCH http://127.0.0.1:9199/review/import-queue/QUEUE_ID -H 'Content-Type: application/json' -d '{"status":"reviewed","reason":"evidence checked"}'
curl -X DELETE http://127.0.0.1:9199/review/package/personal-ops-actions-YYYYMMDD-HHMMSS.json
uv run --frozen notification-hub burn-in --minutes 10
uv run --frozen notification-hub verify-runtime
uv run --frozen notification-hub delivery-check --slack
uv run --frozen notification-hub-policy-check
uv run --frozen notification-hub-explain --source codex --level info --title "Test" --body "Session complete"
uv run --frozen notification-hub smoke
uv run --frozen notification-hub retention --max-events 2000
```

Expected current outcome:

- `pytest`: 307 passed
- `ruff`: clean
- `pyright`: 0 errors
- `/health/details`: `status: ok`, watcher active, push available, Slack configured
- `notification-hub-doctor`: `status: ok`
- `notification-hub status`: compact read-only runtime summary; degrades when recent Slack delivery
  failures are present
- `notification-hub inbox`: compact recent coordination view grouped by intent, with repeated
  event rollups
- `notification-hub coordination-snapshot`: bridge-ready JSON combining inbox state, runtime
  status, and follow-up guidance; writes to bridge-db only with `--save-bridge-db`
- `notification-hub coordination-readiness`: read-only expansion gate combining runtime status,
  queue state, and saved queue burn-in report history; current live decision is `ready_to_expand`
- `notification-hub coordination-console`: read-only compact console for readiness, action proposals,
  queue state, outcome reminders, saved burn-in evidence, and next safe action; proposal lineage is
  split into active actions and handled history so resolved or ignored work does not drive the next
  operator step, and the guide stage exposes exact safe next commands for the current handoff state
- `notification-hub personal-ops-actions`: proposal-only action export derived from repeated inbox
  rollups; known repeated proposals with local dismissal keys are filtered from active exports
- `notification-hub action-proposal-dismiss`: records a local dismissal for one repeated proposal key
  without deleting stored events or applying work in personal-ops
- `notification-hub personal-ops-actions --save-review-package`: writes a local JSON review package
  without mutating personal-ops
- `notification-hub validate-action-package`: validates a saved review package without importing it
- `notification-hub personal-ops-import`: validates a package and stops before mutation; `--enqueue`
  adds valid action proposals to the local import queue while keeping `applied: false`
- `notification-hub personal-ops-queue`: lists and updates queued handoff lifecycle state without
  creating personal-ops tasks, approvals, or sends
- `notification-hub personal-ops-queue-health`: reports routine import queue maintenance state,
  stale pending promoted outcomes, and next safe commands without applying work
- `notification-hub-personal-ops-queue-health`: script shortcut for the same queue-health report
- `notification-hub personal-ops-outcome-sync-reminder`: reports pending and stale promoted handoff
  outcomes as read-only reminders without applying personal-ops work
- `notification-hub-personal-ops-outcome-sync-reminder`: script shortcut for the same reminder report
- `notification-hub personal-ops-queue-burn-in`: checks queue health, temporary lifecycle scenario,
  runtime burn-in, outcome-sync posture, and live operator steps without applying personal-ops work;
  `--save-report` writes a timestamped local JSON report when durable burn-in evidence is useful, and
  policy-covered repeated signatures no longer count as active noise candidates
- `notification-hub-personal-ops-queue-burn-in`: script shortcut for the same burn-in report
- `notification-hub action-proposal-dismissals`: lists active or historical local proposal
  dismissals without changing proposal state
- `notification-hub action-proposal-undismiss`: reactivates one dismissed proposal while preserving
  dismissal history
- `notification-hub operator-daily-state`: builds a read-only, resume-ready operator state payload;
  `--save-report` writes a local JSON report when durable evidence is useful
- `notification-hub operator-review-session`: summarizes recent local review-session activity
  without applying work; `--save-report` writes a local JSON audit report when durable evidence is
  useful
- `notification-hub operator-handoff-drill`: runs the temporary queue lifecycle and queue burn-in
  together without touching the live operator queue
- `/review/burn-in-reports` and `/review/burn-in-report/{name}`: list and inspect saved queue
  burn-in reports without applying work
- `/review/coordination-readiness`: reports whether to fix noise, keep burning in, or start a
  small coordination expansion without applying work
- `/review/coordination-console`: reports the compact coordination console payload, including active
  and handled proposal counts plus dismissal counts and guide steps, without applying work
- `notification-hub personal-ops-queue-scenario`: runs a temporary queue lifecycle and records a
  final accepted promotion outcome without touching runtime queue state
- `/review`: localhost-only review UI for runtime state, Operator Focus, Coordination Readiness,
  Coordination Console next signal and operator guide, inbox rollups, action proposals, import queue
  health, saved burn-in report history, proposal dismissal/undismissal, daily operator state,
  handoff drill, and trust state
- `/review/save-package` and `/review/validate-package`: review UI controls for staging and
  validating packages without importing or applying them
- `/review/packages`: lists recent saved review packages and validation summaries without importing
  or applying them
- `/review/package/{name}`: inspects one saved review package, including action proposals, evidence
  IDs, queue lineage, and validation errors, without importing or applying it
- `DELETE /review/package/{name}`: deletes one saved review package without importing or applying it
- `POST /review/package/{name}/queue` and `/review/import-queue`: enqueue and display local
  personal-ops handoff items without applying them
- `/review/outcome-sync-reminder`: reports promoted handoffs that still need downstream outcome sync
  without applying them
- `/review/action-proposal-dismissals`: lists active or historical local proposal dismissals without
  applying downstream work
- `POST /review/action-proposal/{dismissal_key}/undismiss`: reactivates one dismissed proposal while
  preserving dismissal history
- `POST /review/action-proposal-group/outcome`: records a local grouped-review outcome without
  applying downstream work
- `/review/operator-daily-state`: returns a read-only operator state payload for the review surface
- `/review/operator-review-session`: returns a read-only summary of recent grouped-review and queue
  follow-through activity; `save_report=true` writes the same summary to local runtime state
- `POST /review/operator-handoff-drill`: runs the temporary handoff lifecycle from the review surface
  without touching the live queue
- `PATCH /review/import-queue/{queue_id}`: marks a queued handoff reviewed, rejected, snoozed,
  superseded, or promoted without creating personal-ops work
- `notification-hub logs`: `status: ok` with recent event and daemon log tails, including Slack
  delivery failure counts
- `notification-hub burn-in`: top-level command status plus nested health counters, repeated-event
  noise candidates, review-only noise-rule suggestions, Slack-eligible event volume, and Slack
  delivery failure counts
- `notification-hub verify-runtime`: read-only by default; degrades when doctor, policy, runtime
  wiring, recent burn-in health, or an explicitly requested delivery check is degraded
- `notification-hub delivery-check --slack` / `--push`: sends one explicit transport-check
  notification only when requested
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
The next work here should keep `coordination-console` as the first expansion surface, use proposal
groups during real handoff review, and use the new group history to confirm saved, queued, and
dismissed groups do not reappear as confusing fresh work. Keep apply behavior operator-mediated until
the compact console proves it should own a broader workflow.

## Optional Follow-Up

- Delete `archive/local-history-pre-import` later if that old local-only history is no longer needed.
- Remove local untracked junk files like `.DS_Store` if you want a tidier working directory on disk.
- Keep the live policy's narrow `personal-ops` mail approval noise rules aligned with
  `config/policy.example.toml` when new repeated test signals appear.
