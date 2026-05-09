# Current State

Last updated: 2026-05-09

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
- A localhost-only review page is available at `/review` on the daemon. It shows runtime health,
  inbox rollups, action proposals, and trust state without applying anything.
- The review page can stage a local review package, list recent saved review packages, inspect
  package actions/evidence, queue import handoff items, mark queued items reviewed/rejected/snoozed/promoted,
  delete saved review packages, and validate the latest staged or saved package while keeping apply
  behavior disabled.
- A local logs command is available for recent event and daemon log inspection, including accepted
  versus rejected `/events` counts from the visible daemon tail.
- A local burn-in command is available for recent accepted/rejected event counts and repeated
  event signatures, with validation-error summaries scoped to the latest visible daemon start.
  Burn-in now reports health failures separately from repeated-event noise candidates and includes
  Slack-eligible volume by source/level. Recent Slack delivery failures now degrade burn-in health.
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
  and validation errors without importing or applying anything.
- Added `DELETE /review/package/{name}` so saved review packages can be cleaned up without touching
  personal-ops.
- Added `POST /review/package/{name}/queue` and `GET /review/import-queue` so the review UI can
  enqueue and display personal-ops handoff items without applying them.

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
uv run --frozen notification-hub personal-ops-actions
uv run --frozen notification-hub personal-ops-actions --save-review-package
uv run --frozen notification-hub validate-action-package path/to/actions.json
uv run --frozen notification-hub personal-ops-import path/to/actions.json
uv run --frozen notification-hub personal-ops-import path/to/actions.json --enqueue
uv run --frozen notification-hub personal-ops-queue
uv run --frozen notification-hub personal-ops-queue --queue-id QUEUE_ID --status reviewed --reason "evidence checked"
uv run --frozen notification-hub logs
curl http://127.0.0.1:9199/review
curl http://127.0.0.1:9199/review/packages
curl http://127.0.0.1:9199/review/package/personal-ops-actions-YYYYMMDD-HHMMSS.json
curl -X POST http://127.0.0.1:9199/review/package/personal-ops-actions-YYYYMMDD-HHMMSS.json/queue
curl http://127.0.0.1:9199/review/import-queue
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

- `pytest`: 237 passed
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
- `notification-hub personal-ops-actions`: proposal-only action export derived from repeated inbox
  rollups
- `notification-hub personal-ops-actions --save-review-package`: writes a local JSON review package
  without mutating personal-ops
- `notification-hub validate-action-package`: validates a saved review package without importing it
- `notification-hub personal-ops-import`: validates a package and stops before mutation; `--enqueue`
  adds valid action proposals to the local import queue while keeping `applied: false`
- `notification-hub personal-ops-queue`: lists and updates queued handoff lifecycle state without
  creating personal-ops tasks, approvals, or sends
- `/review`: localhost-only review UI for runtime state, inbox rollups, action proposals, and trust
  state
- `/review/save-package` and `/review/validate-package`: review UI controls for staging and
  validating packages without importing or applying them
- `/review/packages`: lists recent saved review packages and validation summaries without importing
  or applying them
- `/review/package/{name}`: inspects one saved review package, including action proposals, evidence
  IDs, and validation errors, without importing or applying it
- `DELETE /review/package/{name}`: deletes one saved review package without importing or applying it
- `POST /review/package/{name}/queue` and `/review/import-queue`: enqueue and display local
  personal-ops handoff items without applying them
- `PATCH /review/import-queue/{queue_id}`: marks a queued handoff reviewed, rejected, snoozed,
  superseded, or promoted without creating personal-ops work
- `notification-hub logs`: `status: ok` with recent event and daemon log tails, including Slack
  delivery failure counts
- `notification-hub burn-in`: top-level command status plus nested health counters, repeated-event
  noise candidates, Slack-eligible event volume, and Slack delivery failure counts
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
The next work here should burn in the queue lifecycle with real operator use, then tighten the
promotion UX once the personal-ops task-suggestion flow has enough evidence.

## Optional Follow-Up

- Delete `archive/local-history-pre-import` later if that old local-only history is no longer needed.
- Remove local untracked junk files like `.DS_Store` if you want a tidier working directory on disk.
- Install or customize `~/.config/notification-hub/config.toml` from the refreshed
  `config/policy.example.toml` when you want the sample repeated-event tuning in live policy.
