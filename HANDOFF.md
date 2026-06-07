# Handoff — notification-hub

## Current freshness note — 2026-06-07

Machine-wide handoffs now follow
`/Users/d/.codex/docs/operating-layer/machine-wide-handoff-contract.md`.

Fresh checks on 2026-06-07 found local `main` clean and aligned with
`origin/main` at `97f5375`. Runtime status, logs, 10-minute burn-in,
60-minute burn-in, and read-only runtime verification all reported `status:
ok`; queued personal-ops handoffs `0`; pending promoted outcomes `0`;
rejected posts `0`; validation errors `0`; and Slack delivery failures `0`.

Quality gates also passed for this note: `uv run --frozen pytest`, `uv run
--frozen pyright`, and `uv run --frozen ruff check`.

Current nuance: Coordination Console now has one active thin Codex proposal for
`analyze-everything-we-re-working-on`, so First Rich Proof Gate is
`blocked_thin_only`. Do not queue it as first-rich proof. Either wait for a
rich-evidence proposal or explicitly park this thin proposal as
`needs_follow_up` after operator review. Explicit Slack transport, LaunchAgent
restart, and live write-route checks were not rerun in this freshness note; use
the repo verifier before claiming a new green closeout.

**Status:** Runtime truth hardening merged; first-rich gate waiting for candidate
**Branch:** main
**Last verified remote commit before this pass:** main matches origin/main at `255574e`
**Tests:** `uv run --frozen pytest`, `uv run --frozen pyright`, `uv run --frozen ruff check`, Slack
transport check, runtime doctor/status/burn-in/verify-runtime, live `/review/data`, and live POST
report-save routes passed

## Completed This Session

- **2026-06-06 post-merge truth refresh** — local `main` matches `origin/main` at `255574e`; PR
  #72's runtime-truth/report-write hardening is merged, and the resume docs now point at `main`
  rather than the completed feature branch.
- **2026-06-06 runtime truth hardening** — `burn-in` now degrades at the top level whenever nested
  health degrades, and `logs` preserves degraded status for bad or empty sampled evidence.
  `/review/data` runtime state now uses `run_status`, so Slack failure count and next action match
  the CLI runtime contract.
- **2026-06-06 HTTP write cleanup** — `/review/operator-daily-state` and
  `/review/operator-review-session` are GET/read-only even if `save_report=true` is present. Local
  report saves now use explicit POST routes:
  `/review/operator-daily-state/report` and `/review/operator-review-session/report`.
- **2026-06-06 Slack/runtime verification** — `delivery-check --slack --json` passed and sent one
  real Slack diagnostic notification. `doctor`, `status`, 10-minute `burn-in`, and `verify-runtime`
  returned `status: ok` with Slack delivery failures `0`.
- **2026-06-06 quality gates** — full local gates passed: `395` pytest tests, Pyright `0 errors`,
  and Ruff clean.
- **2026-06-06 live daemon activation** — restarted the LaunchAgent with the documented `bootout`,
  `bootstrap`, and `kickstart` sequence. `launchctl print` showed the service running from this repo
  with fresh uptime and PID `63498`.
- **2026-06-06 live route verification** — live `/review/data` now reports runtime `ok`, Slack
  delivery failures `0`, and `next_action: No action needed.` GET report endpoints ignore
  `save_report=true`; POST report routes saved both an operator daily-state report and an
  operator-review-session report without applying downstream work.
- **2026-06-06 mail workflow observation** — current 10-minute burn-in and
  `/review/noise-candidates` are clean, while wider 30-minute/2-hour inspection still shows older
  synthetic personal-ops mail repeats. Coordination Console remains monitor mode with active
  proposals `0`; no new noise rules were added because title/body-only suppression would be too
  blunt for real approval requests.
- **2026-06-06 first-rich gate check** — attempted the next strong lane, but the live gate is still
  waiting: `active_action_count: 0`, `active_rich_count: 0`, `active_thin_count: 0`, no queued
  handoffs, and no pending promoted outcomes. No package was saved, no handoff was queued, and no
  outcome was recorded because there is no real active rich-evidence proposal.
- **2026-05-31 thin waiting echo parking** — a new thin-only
  `codex:we-just-overhauled-my-global-codex:needs_attention:high:open` `Codex is waiting` proposal
  was recorded locally as `needs_follow_up`. No handoff was queued, no notification was sent, and
  First Rich Proof Gate remains waiting for a real rich-evidence candidate.
- **2026-05-31 monitor verification** — Coordination Console and `/review` report monitor posture
  with active proposals `0`, rich follow-up `0`, queued handoffs `0`, pending promoted outcomes `0`,
  runtime `ok`, queue `ok`, Slack delivery failures `0`, and no burn-in noise candidates in the
  current 30-minute window.
- **2026-05-31 dismissal wording cleanup** — `/review` dismissal rows now say `dismissal active` or
  `dismissal inactive` so hidden local dismissals are not mistaken for active proposal work.
- **2026-05-30 dependency closeout** — cleared the open Dependabot lane, verified `main` /
  `origin/main` at `4c2e5f0`, confirmed GitHub CI and CodeQL success, and restarted the daemon
  after dependency updates so live runtime uses the final merged dependency set.
- **2026-05-30 first-rich proof gate** — Coordination Console now emits a structured
  `first_rich_handoff_gate`, and `/review` renders it as a First Rich Proof Gate with
  active rich/thin counts, queue lifecycle counts, resolved rich outcome count, candidate action
  ids, and the exact safe next action.
- **2026-05-30 first-rich queue guard** — thin-only `Codex is waiting` was recorded locally as
  `needs_follow_up`, making it handled history. `/review` now hides queue controls for thin or
  mixed first-proof groups, and the queue path rejects unsafe first-proof selections unless they
  contain exactly one rich-evidence handoff.
- **2026-05-30 rich follow-up parking and dismissal** — fresh mail approval evidence under
  `personal-ops:mail:waiting_on_user:high:waiting` rotated repeatedly, was reviewed, and was
  recorded locally as `needs_follow_up`; the five synthetic/workflow repeat proposal keys were then
  dismissed locally. No mail was sent, no handoff was queued, and no downstream personal-ops work
  was promoted.
- **2026-05-30 runtime readiness cleanup** — burn-in now ignores daemon log files whose mtime is
  outside the requested burn-in window, so stale Slack/validation evidence does not block fresh
  runtime readiness.
- **2026-05-30 personal-ops noise cleanup** — added a `Task suggestion pending` noise rule to the
  repo sample policy and the live local policy config; repeated task suggestions now remain
  policy-covered history instead of active noise candidates.
- **2026-05-30 mail draft noise cleanup** — added a `Draft Updated` mail noise rule to the repo
  sample policy and the live local policy config after fresh burn-in surfaced repeated
  informational draft-update events.
- **2026-05-30 Slack delivery verification** — explicit Slack transport check passed and sent one
  real transport-check notification.
- **2026-05-30 dashboard freshness improvement** — `/review` now separates live runtime status
  from saved burn-in proof, warns when the latest saved proof is older than seven days, and shows
  daemon uptime in the top summary.
- **2026-05-30 dashboard readiness explanation** — `/review` now names readiness blockers when
  degraded and confirms runtime, policy, queue, and saved burn-in proof when ready.
- **2026-05-30 LaunchAgent refresh** — restarted the live daemon after operator approval; live
  `/review` now reports `ready_to_expand` and monitor mode.
- **2026-05-30 fresh proof** — saved
  `/Users/d/.local/share/notification-hub/burn-in-reports/personal-ops-queue-burn-in-20260530-110041.json`
  with runtime OK, queue OK, and zero noise candidates.
- **2026-05-30 fresh follow-up proof** — saved
  `/Users/d/.local/share/notification-hub/burn-in-reports/personal-ops-queue-burn-in-20260530-123125.json`
  after parking the rich follow-up group; runtime OK, queue OK, and zero noise candidates.
- **2026-05-30 local state cleanup** — `.claude/` is now ignored as Claude-owned local state.
- **2026-05-30 Dependabot/Pyright readiness** — diagnosed PR #49's failed Pyright 1.1.409 check
  and updated the FastAPI lifespan annotation from `AsyncIterator` to `AsyncGenerator`; pinned
  Pyright and `pyright==1.1.409` now pass locally.
- **PR #37** — `action-export-retention` CLI: prune action-export files, dry-run by default, `--apply` to delete, `--keep N`; first run pruned 20 files
- **PR #38** — `near_rollup_singles`: surfaces count=1 inbox events invisible to rollup pipeline; new field on `InboxReport`, wired into `run_inbox` and CLI display
- **PR #39** — `docs/CURRENT-STATE.md` updated to reflect session 2 state
- **PR #40** — `mcp_server` smoke tests: 9 in-process tests covering all 7 tool wrappers via FastMCP Client + AsyncMock; replaced dead echo-tool stub
- **2026-05-16 next pass** — fixed Pyright's private-helper test import warning, refreshed `docs/CURRENT-STATE.md`, and intentionally kept `.claude/` plus this handoff artifact
- **2026-05-16 CI follow-up** — added `uv run --directory mcp_server --frozen pytest` to GitHub Actions, switched CI install to `uv sync --frozen --group dev`, and documented the separate MCP test command
- **2026-05-16 real-use burn-in** — exercised `/review`, Coordination Console, inbox `near_rollup_singles`, queue health, runtime status, and 60-minute burn-in against live signals
- **2026-05-16 local handoff closeout** — saved/validated/queued a focused sync-degraded review package, then marked the two queued mailbox/calendar handoff items reviewed after source checks recovered enough that no downstream promotion was appropriate
- **2026-05-16 proposal cleanup** — saved/validated `personal-ops-actions-20260516-193841-085944.json`, dismissed stale recovered personal-ops mailbox-sync and Hermes watchdog proposal keys, and verified Coordination Console returned to monitor mode
- **2026-05-17 calendar proposal cleanup** — saved/validated `personal-ops-actions-20260517-033110-245366.json`, dismissed a stale recovered calendar-sync proposal, and verified Coordination Console returned to monitor mode
- **2026-05-17 compact expansion** — terminal local group outcomes now count as handled lineage: `accepted` resolved, `rejected`/`superseded` closed, `snoozed` snoozed, and `needs_follow_up` follow-up
- **2026-05-17 stale draft cleanup** — created fresh personal-ops recovery snapshot `2026-05-17T03-44-01Z`, routed draft artifact `aa8fd718-51cb-4285-9833-edee718f8706` through approval request `6991ef3e-9ff6-47a9-8304-cf0d259729b2`, rejected it as verification-only stale work, and verified the local draft record is `rejected` / `resolved`
- **2026-05-17 post-cleanup observation** — rechecked status, logs, burn-in, inbox, personal-ops health, and Coordination Console; no ADR 0001 trigger appeared, and the only new `near_rollup_singles` item was the cleanup approval echo

## In Progress

- Source-tree verification and live runtime checks report notification-hub healthy.
- Current live 10-minute burn-in and `/review/noise-candidates` are clean. Older personal-ops mail
  workflow repeats are visible only in wider windows and remain an observation item, not active work.
- First-rich proof collection remains operator-mediated until one rich promoted handoff resolves.

## Blocked

- None.

## Next Steps

1. **Use the First Rich Proof Gate on the next real rich proposal** — save and validate the package,
   queue exactly one rich handoff, and record the promoted outcome before widening authority.
2. **Revisit mail workflow policy only if live noise returns** — tune only if the same repeats
   become current 10-minute candidates or active operator work; avoid broad `Approval Requested`
   suppression.
3. **Resolve ADR 0001 later** — lineage rich-vs-thin supersession is still deferred until a real
   promoted/resolved rich handoff appears under a prior `needs_follow_up` stable key.
4. **Observe `near_rollup_singles` in real use** — tune only if one-off resolved echoes or
   informational first occurrences become repeated operator noise.

## Key Decisions

- `near_rollup_singles` reuses `InboxRollupReport` TypedDict (avoids new type)
- mcp_server tests patch `server._get`/`server._post` at module level — no live daemon required
- ADR 0001 left deferred-open; needs real-use data before closing
- Direct private-helper coverage is kept with a narrow Pyright ignore instead of widening the helper's public API
- The 2026-05-16 sync-degraded package was kept as local evidence, but the two handoffs were closed as reviewed rather than promoted because source checks recovered and no downstream personal-ops work was appropriate
- Stale recovered proposal keys are better handled with local dismissals than queueing: this clears monitor noise while allowing distinct future failures to appear under different keys
- A terminal group outcome should also clear matching future repeats without requiring a separate dismissal; this is now covered by a regression test for `superseded`
- The stale rich-evidence pipeline draft does not need to be sent or promoted; the supported cleanup path is local approval request plus rejection, leaving the personal-ops record resolved without direct database mutation
- A one-off `near_rollup_singles` cleanup echo is an observation, not a suppression trigger; wait for repeated operator noise before adding policy or code
- Stale daemon log files should not drive current burn-in health; the logs surface can still show
  tails for inspection, while burn-in readiness follows the requested time window.
- The repeated personal-ops task-suggestion lane is intentionally policy-covered local noise, not a
  proposal source, unless a future distinct task-suggestion signal needs operator action.
- Repeated informational mail `Draft Updated` events are policy-covered local noise; they are still
  visible as repeated signatures but should not block burn-in readiness.
- The Real Signal Readiness panel should treat saved burn-in proof freshness separately from live
  runtime health; proof older than seven days is useful history, not fresh readiness evidence.
- Live daemon UI/API posture can lag source-tree fixes until the LaunchAgent is restarted; use the
  `/review` uptime metric plus `launchctl print` when checking process freshness.
- HTTP GET routes in `/review` should stay read-only; local report writes should use explicit POST
  routes or CLI `--save-report`.
- FastAPI lifespan functions decorated with `@asynccontextmanager` should return
  `AsyncGenerator[...]`, not `AsyncIterator[...]`, for Pyright 1.1.409+ compatibility.

## Files Changed

- README.md
- docs/CURRENT-STATE.md
- HANDOFF.md
- .gitignore
- config/policy.example.toml
- src/notification_hub/operations.py
- src/notification_hub/server.py
- tests/test_cli_commands.py
- tests/test_logs_burn_in_diagnostics.py
- tests/test_review_endpoints.py
- tests/test_review_operator_endpoints.py
