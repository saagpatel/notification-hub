# Current State

Last updated: 2026-06-06 (post-merge truth refresh; first-rich gate waiting)

## Freshness note (2026-06-07)

The 2026-06-06 session details below are dated history. For cross-agent
handoffs, use
`/Users/d/.codex/docs/operating-layer/machine-wide-handoff-contract.md` and
verify live repo/runtime state before carrying forward current claims.

Fresh checks on 2026-06-07 found local `main` clean and aligned with
`origin/main` at `97f5375`. `notification-hub status --json`, `logs --json`,
10-minute `verify-runtime --json`, and 60-minute `burn-in --json --minutes 60
--lines 300` all reported `status: ok`; queued personal-ops handoffs `0`;
pending promoted outcomes `0`; rejected posts `0`; validation errors `0`; and
Slack delivery failures `0`. The 10-minute burn-in had no noise candidates; the
60-minute window showed repeated thin noise candidates, including one active
thin Codex `ready_to_review` proposal for
`analyze-everything-we-re-working-on`.

Quality gates also passed for this note: `uv run --frozen pytest` (`395
passed`), `uv run --frozen pyright` (`0 errors`), and `uv run --frozen ruff
check`.

Coordination Console is healthy but no longer pure monitor mode: it reports one
active thin proposal and First Rich Proof Gate `blocked_thin_only`. Do not queue
that proposal as first-rich proof. Either wait for a rich-evidence proposal or
explicitly park the thin proposal as `needs_follow_up` after operator review.
Explicit Slack transport, LaunchAgent restart, and live write-route checks were
not rerun for this freshness note.

## Session Update (2026-06-06)

**Current verification:**

- Work is on `main`; the runtime-truth hardening branch was merged as PR #72, and local `main`
  matches `origin/main` at `255574e`.
- Runtime truth reporting is tightened: `burn-in` now returns a degraded top-level status whenever
  nested health degrades from rejected posts, validation errors, or Slack delivery failures.
- `logs` also keeps a degraded top-level status when sampled daemon evidence includes rejected
  posts, validation errors, Slack delivery failures, missing paths, or no sampled evidence.
- `/review/data` runtime status now uses the same compact truth as `notification-hub status`, so
  Slack failure count and next action match CLI runtime posture instead of being hardcoded clean.
- Local HTTP report writes moved off GET. `/review/operator-daily-state` and
  `/review/operator-review-session` are read-only, even if `save_report=true` is present; explicit
  report saves use `POST /review/operator-daily-state/report` and
  `POST /review/operator-review-session/report`.
- Explicit Slack transport verification passed with `notification-hub delivery-check --slack
  --json`; one real Slack diagnostic notification was sent.
- Live runtime checks are healthy: `doctor`, `status`, `burn-in --minutes 10 --lines 200`, and
  `verify-runtime` all returned `status: ok`; daemon reachable, watcher active, runtime wiring
  current, policy OK, import queue OK, and Slack delivery failures `0`.
- Full local gates passed: `uv run --frozen pytest` (`395 passed`), `uv run --frozen pyright`
  (`0 errors`), and `uv run --frozen ruff check`.
- The LaunchAgent was restarted with the documented `bootout` / `bootstrap` / `kickstart` sequence.
  `launchctl print` showed the daemon running from this repo with fresh uptime and PID `63498`.
- Live `/review/data` now serves the new runtime truth contract: runtime `status: ok`, Slack
  delivery failures `0`, and `next_action: No action needed.`
- Live GET report endpoints stay read-only even when `save_report=true` is present. Live POST
  report routes wrote local reports successfully:
  `/Users/d/.local/share/notification-hub/operator-state-reports/operator-daily-state-20260606-081623.json`
  and
  `/Users/d/.local/share/notification-hub/operator-review-session-reports/operator-review-session-20260606-081623.json`.
- Follow-up observation at `2026-06-06T08:20:01Z` found the current 10-minute burn-in and
  `/review/noise-candidates` clean: runtime health `ok`, no rejected posts, no validation errors, no
  Slack delivery failures, and no live noise candidates. The wider 30-minute and 2-hour inspection
  still shows older synthetic personal-ops mail workflow repeats (`Draft Ready`,
  `Approval Requested`, `Send Succeeded`), but Coordination Console is monitor mode with
  `active_action_count: 0`. No new noise rules were added because generic approval bodies would be
  too blunt and could hide real approval requests.
- First Rich Proof Gate check at `2026-06-06T08:24:13Z` found no current candidate:
  `active_action_count: 0`, `active_rich_count: 0`, `active_thin_count: 0`, no queued handoffs, and
  no pending promoted outcomes. No package was saved, no handoff was queued, and no outcome was
  recorded because the first-rich lane requires a real active rich-evidence proposal.

**Active backlog (priority order):**

1. Continue first-rich proof collection only on the next real rich-evidence handoff signal.
2. Keep using `status`, `burn-in`, and `verify-runtime` as the shared runtime truth contract; if one
   surface degrades, the review surface should degrade with it.
3. Revisit personal-ops mail workflow policy only if the same repeats become live 10-minute noise
   candidates again or create active operator work.

## Session Update (2026-05-31)

**Current verification:**

- `main` and `origin/main` were aligned at `33adb8a` before this pass; the worktree was clean.
- A new thin-only `codex:we-just-overhauled-my-global-codex:needs_attention:high:open`
  `Codex is waiting` proposal appeared. It was recorded locally as `needs_follow_up`, so it remains
  handled history and is not eligible for the first-rich proof lane.
- Coordination Console and `/review` are back in monitor posture with `active_action_count: 0`,
  `rich_follow_up_review_count: 0`, no queued handoffs, and no pending promoted outcomes.
- First Rich Proof Gate is still waiting for the next real rich-evidence handoff signal. Active
  rich, thin, and queued counts are all zero.
- Runtime remains healthy: daemon reachable, watcher active, runtime wiring current, policy check
  OK, queue OK, no queued handoffs, no pending promoted outcomes, and no Slack delivery failures.
- A fresh 30-minute burn-in returned `status: ok`, zero validation errors, zero rejected event
  posts, zero Slack delivery failures, and no noise candidates. The synthetic
  `personal-ops` mail approval repeats are quiet in the current burn-in window.
- `/review` dismissal rows now label dismissal state as `dismissal active` / `dismissal inactive`
  so hidden local dismissals are not visually confused with active proposal work.

**Active backlog (priority order):**

1. Keep observing the synthetic personal-ops mail workflow source (`Draft Ready`,
   `Approval Requested`, `Send Succeeded`), but do not add broad suppression while the current
   burn-in window is clean.
2. Use the First Rich Proof Gate during the next real rich active proposal: queue exactly one rich
   handoff only after package save/validation, then record the promoted outcome.
3. Continue observing `near_rollup_singles`; tune only if one-off resolved echoes or informational
   first occurrences become repeated operator noise.

## Session Update (2026-05-30)

**Current verification:**

- Post-merge dependency closeout is complete: the open Dependabot lane was cleared, `main` and
  `origin/main` were aligned at `4c2e5f0`, CI and CodeQL were green, and the daemon was restarted
  again after the dependency updates so live runtime uses the final merged dependency set.
- `/review` now includes a structured First Rich Proof Gate. The gate separates first-proof status
  from generic readiness, shows active rich/thin proposal counts, queued/pending/stale lifecycle
  counts, resolved rich outcome count, candidate action ids, and the exact safe next action.
- With no resolved rich-evidence handoff outcome yet, the gate stays operator-mediated by design.
  If a rich active proposal appears, the safe path is to save and validate the package, queue
  exactly one rich handoff, then record the promoted outcome before widening authority.
- The first-rich proof path is now guarded at both layers: `/review` hides queue controls for thin
  or mixed first-proof groups, and the queue path rejects first-proof selections unless they contain
  exactly one rich-evidence handoff.
- The thin-only `codex:bridge-db:needs_attention:high:open` `Codex is waiting` proposal was recorded
  locally as `needs_follow_up`, so it is handled history and cannot be mistaken for first-rich proof
  work.
- During follow-up, fresh mail approval evidence under the existing
  `personal-ops:mail:waiting_on_user:high:waiting` `needs_follow_up` lineage rotated repeatedly.
  The group was explicitly kept parked, then the five synthetic/workflow repeat proposal keys were
  dismissed locally. Coordination Console is back in monitor posture with zero active proposals, no
  queued or pending handoffs, and `rich_follow_up_review_count: 0`.
- Local `main` matched `origin/main` before this pass; worktree drift was the untracked local
  `.claude/` directory plus this session's changes.
- Runtime status is OK again: daemon reachable, watcher active, runtime wiring current, policy
  check OK, queue OK, no queued handoffs, no pending promoted outcomes, and no Slack delivery
  failures in the current burn-in window.
- The earlier degraded status was caused by stale daemon stderr evidence: the stderr log had not
  changed since the old Slack timeout, but burn-in still counted the old post-start failure.
  Burn-in now ignores daemon log files that have not changed inside the requested window.
- Repeated `personal-ops` `Task suggestion pending` events are now covered by an explicit
  `noise.rules` policy entry in both the repo sample config and the live local policy file. They
  remain visible as repeated signatures and policy-covered Coordination Console history, but no
  longer appear as active noise candidates.
- Repeated informational `personal-ops` mail `Draft Updated` events surfaced after restart and are
  now covered by an explicit sample/live `noise.rules` entry. They remain visible as repeated
  signatures but no longer block clean burn-in proof.
- Explicit Slack transport verification passed with `notification-hub-delivery-check --json
  --slack`; one real Slack transport-check notification was sent.
- The LaunchAgent was restarted after operator approval. The live daemon is on a fresh PID, live
  `/review` now reports `ready_to_expand`, and Coordination Console is in monitor mode with
  `active_action_count: 0`, no queued handoffs, no pending promoted outcomes, and
  `rich_follow_up_review_count: 0`; the synthetic mail approval repeats are hidden by local
  dismissal keys rather than queued or promoted.
- Fresh saved burn-in proofs were created:
  `/Users/d/.local/share/notification-hub/burn-in-reports/personal-ops-queue-burn-in-20260530-110041.json`
  and
  `/Users/d/.local/share/notification-hub/burn-in-reports/personal-ops-queue-burn-in-20260530-123125.json`.
- `/review` now separates live runtime status from saved burn-in proof in the Real Signal
  Readiness panel. Saved proof older than seven days is called out with a warning age badge and no
  longer lets that panel show the compact `ready` state by itself.
- `/review` also shows daemon uptime in the top summary so process freshness is visible from the
  browser after restarts.
- `/review` now adds a plain-language Coordination Readiness explanation. When readiness is
  blocked, the panel lists the current blocker category; when it is ready, it explicitly confirms
  that runtime, policy, queue, and saved burn-in proof are clear.
- Dependabot PR #49's failed Pyright 1.1.409 check was diagnosed. The failure was caused by the
  deprecated `AsyncIterator` return annotation on the FastAPI `@asynccontextmanager` lifespan
  function; `server.py` now uses `AsyncGenerator[None]`, and both the pinned Pyright and
  `pyright==1.1.409` pass locally.
- `.claude/` is now ignored as Claude-owned local state rather than source drift. The existing
  local directory contains a portable-skill symlink and empty agent-memory directories.

## Session Update (2026-05-17)

**Current verification:**

- `main` matched `origin/main` at `65c093a` before this pass; latest CI on `main` was passing.
- Local worktree status was clean except for the existing untracked `.claude/` folder.
- Runtime status remained OK: daemon reachable, runtime wiring current, queue OK, no Slack delivery
  failures, no queued handoffs, and no pending promoted outcomes.
- A real rich handled follow-up appeared under the `personal-ops:mail:waiting_on_user:high:waiting`
  group after the re-review lane shipped. Recording a fresh local group outcome moved Coordination
  Console back to monitor mode with `active_action_count: 0` and `rich_follow_up_review_count: 0`.
- The local read-only burn-in remained healthy: queue loop ready, queue health OK, scenario OK,
  runtime OK, and no validation or Slack delivery failures.
- Review-window drift cleanup is complete locally: the review UI now inherits
  `ACTION_PROPOSAL_REVIEW_WINDOW_HOURS` from the server instead of hardcoding `24`, and regression
  coverage now includes multiple rich handled follow-ups clearing after fresh outcomes.
- CI maintenance is complete locally: `actions/checkout` moved from `v4` to `v5`, the official
  Node 24 migration release, to clear the GitHub Actions Node 20 deprecation warning.
- Coordination Console maintainability cleanup is complete locally: proposal review summary logic,
  proposal group assembly, and guide-stage branches were split into smaller helpers inside
  `operations.py` without changing the public report shape.
- Queue lifecycle maintainability cleanup is complete locally: queue item update validation,
  lifecycle field mutation, degraded response construction, and next-action text were split into
  smaller helpers without changing the queue update report shape.
- Queue health summary cleanup is complete locally: queue status counting, pending promotion
  detection, stale pending calculation, timestamp selection, and next-action text were split into
  smaller helpers without changing the health report shape.
- Personal-ops queue test cleanup is complete locally: queue import, lifecycle, health, review,
  outcome reminder, scenario, and queue burn-in tests moved out of the large operations test file
  into a dedicated queue test module without changing runtime behavior.
- README command inventory coverage is complete locally: documented notification-hub command
  examples in bash blocks are checked against the real `pyproject.toml` script inventory and CLI
  subcommand inventory so operator-facing command docs are less likely to drift.
- Action review package test cleanup is complete locally: saved review package, validation, package
  listing, detail loading, and safe deletion tests moved out of the large operations test file into
  a dedicated package-focused test module without changing runtime behavior.
- Runtime diagnostics test cleanup is complete locally: status, logs, verify-runtime, and
  delivery-check tests moved out of the large diagnostics test file into a dedicated runtime
  diagnostics module without changing runtime behavior.
- Operator review-session test cleanup is complete locally: review-session summary, saved report,
  report listing/detail loading, and retention tests moved out of the large operations test file
  into a dedicated review-session test module without changing runtime behavior.
- Coordination-console test cleanup is complete locally: readiness, proposal review, handled
  history, rich follow-up review, outcome quality, and queued handoff lifecycle tests moved out of
  the large operations test file into a dedicated coordination-console test module without changing
  runtime behavior.
- Coordination-console test cleanup continued locally: the broad coordination-console test module
  is now split into core console/proposal coverage, lineage history coverage, follow-up re-review
  coverage, queued-handoff guidance coverage, and shared coordination-console fixtures without
  changing runtime behavior.
- Inbox/action-export test cleanup is complete locally: inbox rollups, near-rollup singles,
  coordination snapshot wrapping, personal-ops action export filtering, dismissal lifecycle, and
  repeated-title uniqueness tests moved out of the large operations test file into a dedicated
  inbox/action-export test module without changing runtime behavior.
- Action proposal group test cleanup is complete locally: group package save routes, enqueue
  history, action-export file pruning, group dismissal, and group outcome tests moved out of
  the large operations test file into a dedicated action proposal group test module without
  changing runtime behavior.
- Operator state/report test cleanup is complete locally: operator daily-state snapshots,
  handoff drill lifecycle, saved queue burn-in report listing/detail, noise-candidate review,
  and nearby queue/import guard tests moved out of the large operations test file into a
  dedicated operator state report test module without changing runtime behavior.
- Logs/burn-in diagnostics test cleanup is complete locally: log tailing, daemon validation
  and Slack failure counting, burn-in repeated signature reporting, policy-covered noise
  filtering, and Slack failure health tests moved out of the large operations test file into
  a dedicated logs/burn-in diagnostics test module without changing runtime behavior.
- Retention/policy test cleanup is complete locally: retention rotation, policy config
  bootstrap, policy warning/degraded handling, sample noise rule drift, and routing fix
  suggestion tests moved out of the large operations test file into a dedicated retention
  and policy operations test module without changing runtime behavior.
- Review endpoint test cleanup is complete locally: `/review` page, data, package, queue,
  burn-in report, policy check, proposal group, dismissal, operator state, review-session,
  drill, and queue lifecycle endpoint tests moved out of the large server test file into a
  dedicated review endpoint test module without changing runtime behavior.
- Review endpoint error hardening is complete locally: `/review` JSON responses now sanitize
  unexpected `error`, `load_error`, and validation `errors` text while preserving known safe
  operator messages, so local paths or traceback-like details stay out of the browser-facing API.
- CLI command test cleanup is complete locally: doctor, smoke, inbox, coordination, personal-ops
  queue, policy, explain, retention, bootstrap, burn-in, runtime verification, delivery check, and
  wrapper command tests moved out of the large diagnostics test file into a dedicated CLI command
  test module without changing runtime behavior.
- CLI wrapper test cleanup is complete locally: script-wrapper entrypoint forwarding tests moved
  out of the broad CLI command test file into a dedicated wrapper test module, with shared CLI
  report fixtures extracted for command and wrapper tests without changing runtime behavior.
- CLI source cleanup is complete locally: terminal report rendering and JSON output-file helpers
  moved out of `cli.py` into `cli_reports.py`, leaving command parsing and dispatch in `cli.py`
  without changing command behavior.
- CLI parser cleanup is complete locally: command-line parser construction moved out of `cli.py`
  into `cli_parser.py`, leaving command dispatch and script-wrapper entrypoints in `cli.py`
  without changing command behavior.
- CLI wrapper cleanup is complete locally: script-wrapper entrypoints now share one forwarding
  helper in `cli.py`, keeping each public wrapper command mapped to the same subcommand without
  repeating `sys.argv` handling.
- CLI dispatch cleanup is complete locally: repeated JSON/human report emission and status-based
  exit-code handling now flows through one helper in `cli.py`, while command-specific argument
  wiring remains explicit.
- Operation report type cleanup is complete locally: the large `TypedDict` report-shape block moved
  out of `operations.py` into `operations_types.py`, while `operations.py` still re-exports those
  names for existing imports.
- Runtime log helper cleanup is complete locally: daemon log tailing, daemon summary parsing, and
  stored-event report shaping moved out of `operations.py` into `operations_logs.py`, while public
  `run_logs` and `run_burn_in` behavior stays in `operations.py`.
- Proposal persistence cleanup is complete locally: action proposal dismissals, undismissals,
  dismissal listing, and proposal group-history JSONL handling moved out of `operations.py` into
  `operations_proposals.py`, while existing CLI/server imports continue through `operations.py`.
- Action package validation cleanup is complete locally: saved action package schema validation,
  action-record validation, and payload action extraction moved out of `operations.py` into
  `operations_packages.py`, while existing CLI/server imports continue through `operations.py`.
- Action package storage cleanup is complete locally: review package writing, listing, safe-name
  path resolution, deletion, and action-export retention moved into `operations_packages.py`,
  while existing CLI/server imports continue through `operations.py`.
- Inbox rollup helper cleanup is complete locally: event-to-inbox item shaping, repeated rollup
  construction, near-rollup single construction, and intent bucket mapping moved into
  `operations_inbox.py`, while `run_inbox` behavior remains in `operations.py`.
- Action proposal shaping cleanup is complete locally: rollup-to-action mapping, stable proposal
  dismissal key generation, action candidate limit selection, and evidence-quality helpers moved
  into `operations_actions.py`, while proposal export and Coordination Console behavior stays in
  `operations.py`.
- Generic error hardening is complete locally: policy config load failures, doctor local API
  failures, smoke/log/burn-in failures, queue/report file IO failures, and package/report parsing
  failures now return stable operator-facing error messages instead of raw local exception text.
- Report error hardening is complete locally: operation, diagnostic, and policy-loading reports now
  use generic browser/operator-facing error messages for unexpected exceptions while retaining
  detailed exception text in local logs where it is needed for debugging.
- Review package endpoint test cleanup is complete locally: review package save, validation,
  listing/detail/delete, package queueing, import queue, import queue review, and burn-in report
  endpoint tests moved out of the broad review endpoint test file into a dedicated review package
  endpoint test module without changing runtime behavior.
- Review endpoint test cleanup is complete locally: proposal-group/dismissal endpoints and
  operator/session/queue endpoints moved out of the broad review endpoint test file into dedicated
  review endpoint modules, with the shared async review client fixture moved into test isolation
  setup without changing runtime behavior.
- Compact expansion shipped locally: proposal lineage now treats terminal local group outcomes as
  handled history. `needs_follow_up` remains follow-up, `snoozed` remains snoozed, `accepted` is
  resolved history, and `rejected` / `superseded` are closed history. Matching action IDs or stable
  proposal keys no longer resurface as fresh active work just because evidence rotated.
- Personal-ops recovery repair was refreshed before mutation: `personal-ops backup create --json`
  created snapshot `2026-05-17T03-44-01Z`; follow-up health check returned ready with 6 pass /
  0 warn / 0 fail.
- The stale rich-evidence test draft cleanup is complete in local personal-ops state. Artifact
  `aa8fd718-51cb-4285-9833-edee718f8706` / provider draft `r695541613668480159` was routed through
  the supported approval workflow and rejected as stale verification-only work; the draft record is
  now `status=rejected`, `review_state=resolved`.
- Post-cleanup checks stayed clean: personal-ops health ready, notification-hub status OK,
  personal-ops queue health OK, and Coordination Console in monitor mode with `active_action_count:
  0`, no queued handoffs, and no pending promoted outcomes.
- Follow-up observation stayed quiet: Coordination Console still has no active proposals, queued
  handoffs, or pending promoted outcomes. `outcome_quality.rich` remains 0/0 resolved, so ADR 0001
  still has no real promoted/resolved rich handoff trigger.
- `near_rollup_singles` currently surfaces one cleanup echo from the resolved rich-evidence draft
  approval request plus low-volume informational first occurrences. This is useful visibility but
  not enough volume to justify suppression or a code change yet.
- ADR 0001 follow-up shipped the middle path: rich evidence under handled `needs_follow_up` history
  remains lineage status `follow_up`, but Coordination Console now exposes it as
  `follow_up_review` with `rich_follow_up_review_count`, a `next_signal` status of `review`, and a
  guide stage of `rich_follow_up_review`. This makes the evidence visible without automatic
  promotion or downstream personal-ops mutation.

**Active backlog (priority order):**

1. Continue observing `near_rollup_singles`; tune only if one-off resolved echoes or informational
   first occurrences become repeated operator noise.
2. Keep calendar OAuth recovery work outside this repo unless the operator explicitly asks for
   cross-project work.

## Session Update (2026-05-16)

**Current verification:**

- Root test suite: 376 passed.
- MCP server smoke tests: 9 passed.
- Ruff: passed.
- Pyright: current pass refreshed after the near-rollup private-helper test import was marked as
  an intentional test-only exception.
- CI workflow: dependency-group install, root tests, MCP server smoke tests, Ruff, and Pyright are
  all wired into GitHub Actions.
- Runtime verification: `notification-hub verify-runtime --json` reports `status: ok`; daemon,
  doctor, policy check, runtime wiring, queue health, and recent runtime health are OK.
- Coordination readiness: `ready_to_expand`; runtime, queue, and saved burn-in evidence are ready
  for the next compact coordination-console slice.
- Real-use burn-in closeout: `notification-hub status --json`, `personal-ops-queue-health --json`,
  `coordination-readiness --json`, and a 60-minute `burn-in --json` all report notification-hub
  health OK. The local handoff queue is clean (`queued_count: 0`, `promoted_pending_count: 0`).
- The saved review packages from the live personal-ops sync-degraded pass are intentionally kept
  under `/Users/d/.local/share/notification-hub/action-exports/`:
  `personal-ops-actions-20260516-091230-479634.json`,
  `personal-ops-actions-20260516-091259-180792.json`, and
  `personal-ops-actions-20260516-091315-947184.json`.
- That live pass queued two local notification-hub handoff items for mailbox/calendar sync
  degradation, then marked both reviewed after source checks recovered enough that no downstream
  personal-ops promotion was appropriate.
- The latest 60-minute burn-in is delivery-clean (`rejected_event_posts: 0`,
  `validation_error_count: 0`, `slack_delivery_failure_count: 0`) but still shows repeated
  personal-ops mailbox/task-suggestion signatures as noise/watch items.
- `near_rollup_singles` proved useful in real use: it surfaced count=1 Notion and real-mail
  signals that would not enter the repeat-rollup proposal pipeline.
- Follow-up proposal review: saved and validated
  `personal-ops-actions-20260516-193841-085944.json`, then locally dismissed three stale recovered
  thin-evidence proposals: two personal-ops mailbox-sync signatures and one Hermes watchdog
  connectivity signature. The Coordination Console is back in monitor mode with `active_action_count:
  0`; the queue remains clean.

**Current repo posture:**

- `main` was at `b870b44` before the 2026-05-16 documentation closeout, then advanced through
  `d8cfa13` and `ff2aded` as the live burn-in/proposal cleanup notes landed.
- PR #40 closed the prior MCP server smoke-test backlog with 9 in-process FastMCP wrapper tests.
- The follow-up CI coverage gap is now closed locally by adding the MCP server smoke test command to
  `.github/workflows/ci.yml`; the workflow install step now uses the repo's dependency-group syntax.
- The earlier `action-export-retention` and `near_rollup_singles` work remains shipped.
- `HANDOFF.md` is now a tracked restart artifact. Local `.claude/` remains untracked and contains
  only ignored machine-local state.

**Active backlog (priority order):**

1. `aa8fd718` Gmail draft cleanup — test draft "Rich-evidence pipeline test — 2026-05-11" still in
   Drafts. Manual action via Gmail web UI or rejection workaround via `approval_request_create`.
2. Resolve ADR 0001 — lineage rich-vs-thin supersession remains deferred until a real promoted or
   resolved rich handoff appears under a prior `needs_follow_up` stable key.
3. Continue observing `near_rollup_singles` and tune suppression policy based on actual volume.

**`outcome_quality.rich` remains 0/0 by design.** No organic rich handoff has been promoted.

## Session Update (2026-05-12, session 2)

**What shipped (PRs #37 and #38):**

- **Action-export retention** (`notification-hub action-export-retention`) — new CLI + script entry
  mirrors `operator-review-session-retention`. Dry-run by default, `--apply` to delete, `--keep N`
  (default 20), `--json`. 20 of 40 accumulated files pruned on first run. 3 new tests.
- **Near-rollup singles visibility** — `near_rollup_singles: list[InboxRollupReport]` added to
  `InboxReport`. New `_build_near_rollup_singles` helper returns count=1 groups (same key as rollup,
  but filtered to exactly one occurrence). Surfaced in CLI `inbox` print and JSON output. 3 new tests.
- **376 tests passing** (up from 370).
- `main` is at `ea80ac2`, clean and aligned with `origin/main`.

**Active backlog (priority order):**

1. `mcp_server/` smoke test — thin scaffold at `mcp_server/`; one minimal `test_server.py`.
   Add a connection/health smoke test covering the FastMCP stdio transport and the 7 tool wrappers.
2. `aa8fd718` Gmail draft cleanup — test draft "Rich-evidence pipeline test — 2026-05-11" still in
   Drafts. Manual action via Gmail web UI or rejection workaround via `approval_request_create`.

**`outcome_quality.rich` remains 0/0 by design.** No organic rich handoff has been promoted.

---

## Truth-Gap Status (2026-05-11)

The original "rich 0/0 resolved" outcome-quality gap was investigated and split into two layers:

- **Visibility (fixed)**: `coordination-console` and `/review/coordination-console` previously
  defaulted to a 2-hour proposal window while `personal-ops-actions` used 24 hours, so proposals
  whose latest evidence aged past 2 hours silently disappeared from the operator surface. The
  default is now 24 hours across CLI, function, and review endpoint.
- **Real producer signal (validated 2026-05-11)**: two operator-mediated approval requests on
  pre-existing assistant-generated reply drafts produced the first ever
  `mailbox: jayday1104@gmail.com` events in notification-hub, each carrying a real
  `thread_id` (`19d2305f402a9cb3`, `19d0e84571202070`) plus `draft_id`, `provider_draft_id`,
  `approval_id`, `group_id`. They formed a `count=2` "Approval Requested / Security alert"
  rollup that produced the first real `evidence_quality: rich` proposal
  (`action_id ...:approval-requested:497f27949e37`). The proposal was saved (review package
  `personal-ops-actions-20260511-043245-053387.json`), enqueued (queue id `bc3ad1589f83d5dd`),
  and closed via the `reviewed` lane — **deliberately not promoted**, because no operator-mediated
  personal-ops task suggestion was created downstream. `outcome_quality.rich` remains 0/0 by
  design; promotion is reserved for a real handoff that will actually be acted on downstream.
  The wiring is now empirically validated end-to-end: real producer → real event → rich score →
  rollup → proposal → save → queue → reviewed closeout.
- **Rollup-of-2 constraint**: `_build_inbox_rollups` (operations.py:1484) requires at least 2
  events of the same `(source, project, intent, level, title, body)` signature before a rollup
  is emitted. Single events never reach the proposal pipeline. This is intentional repeat-noise
  detection, but it does mean that the first signal of a kind is invisible until a second one
  matches. Worth keeping in mind when designing future evidence-quality tests.
- **Lineage subsumption (resolved as sticky plus visible re-review)**: the prior `needs_follow_up` group outcome on
  `personal-ops:mail:waiting_on_user:high:waiting` (recorded 2026-05-10 14:56Z) covers stable
  proposal keys that current synthetic rich proposals share. The lineage logic correctly classifies
  them as continuations of that follow-up, honoring the operator's explicit "preserve follow-up
  state across evidence-event rotation" intent. Rich handled follow-up now gets a separate
  operator-visible re-review signal instead of automatic supersession, and that prompt clears after
  an explicit group outcome is recorded later than the rich evidence timestamp. The console, `/review`
  controls, and `action-proposal-group-outcome` now share the same 24-hour review window so an
  explicit group outcome records against the evidence the operator just reviewed.

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
- The doctor command reports localhost health failures cleanly, including local SSL/certificate
  setup errors from the HTTP client, instead of crashing during verification.
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
- The Coordination Console now treats reviewed, follow-up, and snoozed handoffs as handled history
  instead of active lifecycle blockers once queue health is clean.
- Proposal Review now splits handled history into reviewed-only, follow-up, resolved, closed, and
  snoozed counts, so intentionally reviewed-but-not-promoted or follow-up-only handoffs are visible
  without looking like unfinished promotion work.
- Handled mail follow-ups now get their own calm history summary with rich/thin evidence counts, so
  repeated handled mail echoes do not read as new active operator work.
- The Coordination Console now reports promoted handoff outcome quality by rich versus thin
  evidence and narrows the monitor posture to notify only on active proposals, queued handoffs,
  pending promoted outcomes, runtime degradation, or repeated diagnostic echoes.
- The first real operator-mediated promotion proof has completed, and the current live queue has no
  queued, pending, or stale promoted handoff outcomes.
- Queue maintenance now has dedicated `personal-ops-queue-health` and `personal-ops-queue-review`
  commands. Health reports queued age, pending/stale outcomes, and next safe commands; review groups
  queued handoffs into operator batches without approving, sending, or changing downstream systems.
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
  state after a save, queue, dismiss, or explicit local outcome decision. Queued or promoted-pending
  handoffs now remain the console's next action until their local queue lifecycle is resolved, even
  when the readiness gate is also warning.
- Proposal groups with a latest terminal outcome now remain visible as handled history instead of
  resurfacing as fresh active proposals. `needs_follow_up` remains follow-up, `snoozed` remains
  snoozed, `accepted` is resolved history, and `rejected` / `superseded` are closed history.
  Save-only package inspection does not reopen those groups; stable proposal keys keep repeated
  rollups in their handled state even when newest event IDs rotate. Queueing, promotion, dismissal,
  or a different proposal key can still move the lifecycle.
- Action proposal export now scans a deeper candidate set than the display limit, so dismissed or
  policy-covered rollups cannot crowd out real lower-ranked operator signals from the default view.
- Action proposal dismissals can now be listed, inspected, and undismissed through CLI and `/review`
  without deleting dismissal history.
- An `operator-daily-state` command and `/review/operator-daily-state` endpoint now build a
  resume-ready local state snapshot across runtime health, queue health, Coordination Console next
  signal, burn-in, dismissals, and the rich/thin outcome-quality summary. The command can save
  timestamped JSON reports under local notification-hub runtime state; HTTP report saves use
  `POST /review/operator-daily-state/report` so the GET endpoint stays read-only.
- An `operator-review-session` command and `/review/operator-review-session` endpoint now summarize
  recent local review activity across grouped proposal saves, queues, dismissals, outcomes, and
  queue follow-through. The `/review` Operator State panel shows this alongside the daily state, and
  `--save-report` or `POST /review/operator-review-session/report` writes timestamped local JSON
  audit reports. The GET endpoint stays read-only even when `save_report=true` is present. Saved
  review-session reports can now be listed and inspected from `/review` as a compact session
  timeline, while `operator-review-session-retention` prunes old saved reports after an explicit
  `--apply`. `/review/operator-review-session-retention` exposes the same cleanup pressure as a
  read-only dry-run summary.
- An `operator-handoff-drill` command and `/review/operator-handoff-drill` endpoint now run the
  temporary handoff lifecycle plus queue burn-in as a non-applying rehearsal. The `/review` drill
  control saves burn-in proof by default and displays rich-evidence readiness, live-promotion
  readiness, and saved report status.
- The sample policy now includes the repeated `personal-ops` daemon-start and `notion-os`
  control-tower sync signals seen during live burn-in, keeping evidence-based noise tuning in the
  repo without changing machine-local config.
- The sample and live policy now also cover repeated personal-ops mail `Send Succeeded` events for
  `Console reply needed`, after a real-use route-aware review pass showed them as success chatter
  rather than operator work.
- The sample and live policy now also cover repeated personal-ops mail `Draft Ready` and
  `Approval Requested` echoes for `Phase 34 secondary approval`.
- The sample policy now also covers the repeated personal-ops `System needs attention: run
  personal-ops doctor` diagnostic echo, keeping already-satisfied doctor prompts from resurfacing as
  fresh proposal work when the active policy includes the sample rule.
- A localhost-only review page is available at `/review` on the daemon. It shows runtime health,
  inbox rollups, action proposals, and trust state without applying anything.
- The review page now includes Operator Focus, Coordination Readiness, and Coordination Console
  summaries that put the current action state, expansion gate, next real signal, and next safe action
  first. A Proposal Review section shows grouped active proposals before a package is queued and can
  save, queue, mark as needing follow-up, or dismiss one proposal group at a time. It also shows
  recent group-history entries so a refresh does not hide the last grouped action. Policy drift and
  the latest saved review-session report are also visible from the review surface.
- The review page now also includes a Real Signal Readiness lane that combines active proposals,
  handled follow-ups, queue state, latest saved proof, the next safe command, and a rich-outcome
  guardrail so coordination expansion waits for a real resolved rich-evidence handoff.
  It also shows a structured First Rich Proof Gate for rich/thin candidate counts, lifecycle state,
  proof status, and the next safe action. Saved burn-in proof is compared against the previous proof
  for readiness and noise drift.
- Codex now has an active `notification-hub-signal-watch` heartbeat that should stay report-only and
  use the read-only Coordination Console, queue health, and runtime verification surfaces to decide
  whether there is an active operator handoff or only the narrowed monitor posture.
- Proposal Review now adds advisory mail routing recommendations for personal-ops mail approval
  groups, with promote, suppress, and follow-up counts. This helps split concrete reply candidates
  from repeated phase/workflow chatter without auto-promoting or auto-suppressing anything.
- Proposal Review group controls are now route-aware for mixed mail batches: operators can save or
  queue only the `promote` route, or locally dismiss only the `suppress` route, while leaving
  follow-up candidates visible for separate inspection.
- The review surface now splits real personal-ops mail approvals into an Operator Decision Required
  lane and repeated burn-in signatures into Noise Candidate Review, preserving the outbound-approval
  operator gate while still surfacing narrow policy candidates. The approval lane now has its own
  route so approval-titled mail items that are not known phase/workflow chatter can be packaged
  together even when they are not concrete reply-promotion candidates.
- Events now accept optional scalar `context` values, and repeated-event rollups carry the latest
  context into personal-ops action proposals as `evidence_context`. Mail producers can use this for
  source-side identifiers such as thread, draft, message, or approval IDs without giving the hub any
  send or approval authority.
- Action proposals now include deterministic `evidence_quality` so review surfaces can distinguish
  context-rich mail handoffs from thin repeated signals before queueing or promotion.
- Mail proposal routing now uses evidence quality: rich promotion-looking signals can be routed to
  the promote lane, while thin promotion-looking signals remain follow-up work until more context is
  present.
- Proposal Review now reports per-group promotion readiness, including ready action IDs and blocked
  action IDs, so a mixed mail group can be split before any local handoff is queued.
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
  and now suggests likely fixes for each warning. It also compares live noise rules with the repo
  sample policy so missing sample coverage is visible before repeated producers return.
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
- Added narrow `personal-ops` mail noise rules for repeated `Phase 34 secondary approval` approval
  and draft-ready echoes so those repeated test signals stay out of active operator proposals.
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
- Added first-class follow-up lineage in Coordination Console so `needs_follow_up` outcomes are
  counted as handled follow-up history rather than new active proposal work, including repeated
  rollups whose newest event IDs rotate under the same stable proposal key.
- Added handled-proposal lineage reasons plus stable-key and evidence-rotation counts, so monitor
  mode can explain why a repeated proposal remains history instead of active work.
- Added advisory mail route recommendations to Proposal Review so mixed mail approval batches show
  whether they contain promote candidates, suppression candidates, or follow-up-only items.
- Added route-aware Proposal Review actions so a mixed mail batch can be split into local promote,
  suppress, and follow-up routes without sending mail or creating downstream personal-ops work.
- Added optional event `context` propagation into inbox rollups and personal-ops review packages so
  source-side mail identifiers can travel with evidence-backed proposals.
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
uv run --directory mcp_server --frozen pytest
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
uv run --frozen notification-hub personal-ops-queue-review
uv run --frozen notification-hub-personal-ops-queue-health --json
uv run --frozen notification-hub personal-ops-outcome-sync-reminder
uv run --frozen notification-hub-personal-ops-outcome-sync-reminder --json
uv run --frozen notification-hub personal-ops-queue-burn-in
uv run --frozen notification-hub-personal-ops-queue-burn-in --json
uv run --frozen notification-hub personal-ops-queue-burn-in --save-report
uv run --frozen notification-hub personal-ops-queue-scenario
uv run --frozen notification-hub operator-review-session
uv run --frozen notification-hub operator-review-session --save-report
uv run --frozen notification-hub operator-review-session-retention --keep 20
uv run --frozen notification-hub operator-review-session-retention --keep 20 --apply
uv run --frozen notification-hub logs
curl http://127.0.0.1:9199/review
curl http://127.0.0.1:9199/review/packages
curl http://127.0.0.1:9199/review/operator-review-session-retention
curl http://127.0.0.1:9199/review/package/personal-ops-actions-YYYYMMDD-HHMMSS.json
curl -X POST http://127.0.0.1:9199/review/package/personal-ops-actions-YYYYMMDD-HHMMSS.json/queue
curl http://127.0.0.1:9199/review/import-queue
curl http://127.0.0.1:9199/review/outcome-sync-reminder
curl -X POST http://127.0.0.1:9199/review/operator-daily-state/report
curl http://127.0.0.1:9199/review/operator-review-session
curl -X POST http://127.0.0.1:9199/review/operator-review-session/report
curl http://127.0.0.1:9199/review/operator-review-session-reports
curl http://127.0.0.1:9199/review/operator-review-session-report/operator-review-session-YYYYMMDD-HHMMSS.json
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

- `pytest`: ≥376 passed (376 confirmed as of 2026-05-12 session; current suite may be higher)
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
  creating personal-ops tasks, approvals, or sends; `reviewed` is the reviewed-only lane for
  evidence-checked items that do not need downstream personal-ops promotion
- `notification-hub personal-ops-queue-health`: reports routine import queue maintenance state,
  stale pending promoted outcomes, and next safe commands without applying work
- `notification-hub personal-ops-queue-review`: groups queued handoff items into operator review
  batches, including approval decision counts and the first safe local review command
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
- `notification-hub operator-review-session-retention`: shows or applies pruning for older saved
  review-session reports; default mode is dry-run, and deletion requires `--apply`
- `/review/operator-review-session-retention`: shows saved review-session cleanup pressure without
  deleting files
- `notification-hub operator-handoff-drill`: runs the temporary queue lifecycle and queue burn-in
  together without touching the live operator queue
- `/review/burn-in-reports` and `/review/burn-in-report/{name}`: list and inspect saved queue
  burn-in reports without applying work
- `/review/coordination-readiness`: reports whether to fix noise, keep burning in, or start a
  small coordination expansion without applying work
- `/review/coordination-console`: reports the compact coordination console payload, including active
  and handled proposal counts plus dismissal counts and guide steps, without applying work
- `/review/noise-candidates`: reports the latest burn-in repeated-signature candidates with
  decision hints, suggested narrow policy text, and an explicit non-applying status
- `/review/policy-check`: reports live policy warnings and sample-vs-live noise-rule drift without
  applying work
- `notification-hub personal-ops-queue-scenario`: runs a temporary queue lifecycle and records a
  final accepted promotion outcome without touching runtime queue state
- `/review`: localhost-only review UI for runtime state, Operator Focus, Coordination Readiness,
  Coordination Console next signal and operator guide, inbox rollups, action proposals, import queue
  health, Operator Decision Required, Noise Candidate Review, policy drift, saved burn-in report
  history, latest saved review-session state, saved review-session history, proposal
  dismissal/undismissal, daily operator state, handoff drill, and trust state
- `/review/save-package` and `/review/validate-package`: review UI controls for staging and
  validating packages without importing or applying them
- `/review/packages`: lists recent saved review packages and validation summaries without importing
  or applying them
- `/review/package/{name}`: inspects one saved review package, including action proposals, evidence
  IDs, queue lineage, and validation errors, without importing or applying it
- `DELETE /review/package/{name}`: deletes one saved review package without importing or applying it
- `POST /review/package/{name}/queue` and `/review/import-queue`: enqueue and display local
  personal-ops handoff items without applying them
- `/review/import-queue-review`: summarizes queued handoffs as read-only review batches for the
  review surface
- `/review/outcome-sync-reminder`: reports promoted handoffs that still need downstream outcome sync
  without applying them
- `/review/action-proposal-dismissals`: lists active or historical local proposal dismissals without
  applying downstream work
- `POST /review/action-proposal/{dismissal_key}/undismiss`: reactivates one dismissed proposal while
  preserving dismissal history
- `POST /review/action-proposal-group/outcome`: records a local grouped-review outcome without
  applying downstream work
- `/review/operator-daily-state`: returns a read-only operator state payload for the review surface
- `POST /review/operator-daily-state/report`: saves the operator state payload to local runtime
  state
- `/review/operator-review-session`: returns a read-only summary of recent grouped-review and queue
  follow-through activity
- `POST /review/operator-review-session/report`: saves the review-session summary to local runtime
  state
- `/review/operator-review-session-reports` and `/review/operator-review-session-report/{name}`:
  list and inspect saved review-session reports without applying work
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
- `notification-hub-policy-check`: `status: ok`, `warn`, or `degraded`, depending on the active
  policy file and sample-policy drift, plus warning-specific fix suggestions when issues are found
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
  ties that still depend on file order. It also reports whether the live policy is missing sample
  noise rules or has extra live-only noise rules.
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

Use the active backlog in the most recent Session Update (2026-05-31) as the starting point.
All items listed in the 2026-05-16 entry (action-export retention, near-rollup singles visibility,
mcp_server smoke test, aa8fd718 draft cleanup) are complete per the 2026-05-17 and 2026-05-12
session updates above.
Keep apply behavior operator-mediated until the compact console proves it should own a broader workflow.

## Optional Follow-Up

- Delete `archive/local-history-pre-import` later if that old local-only history is no longer needed.
- Remove local untracked junk files like `.DS_Store` if you want a tidier working directory on disk.
- Keep the live policy's narrow `personal-ops` mail approval noise rules aligned with
  `config/policy.example.toml` when new repeated test signals appear.
