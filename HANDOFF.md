# Handoff — notification-hub

**Status:** Live runtime refreshed; dashboard freshness and Pyright readiness pass complete
**Branch:** main
**Last verified remote commit before this pass:** 217ab7c (action proposal shaping split)
**Tests:** 386 repo tests passing, 9 MCP tests passing, Ruff and Pyright passing

## Completed This Session

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
- **2026-05-30 LaunchAgent refresh** — restarted the live daemon after operator approval; live
  `/review` now reports `ready_to_expand` and monitor mode.
- **2026-05-30 fresh proof** — saved
  `/Users/d/.local/share/notification-hub/burn-in-reports/personal-ops-queue-burn-in-20260530-110041.json`
  with runtime OK, queue OK, and zero noise candidates.
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

- Source-tree and live browser/API verification report notification-hub healthy and ready to expand.
- Coordination Console is in monitor mode with no active proposals, no queued handoffs, and no
  pending promoted outcomes.
- `/review` now makes the main Real Signal Readiness panel less ambiguous about live health versus
  saved proof, and the top summary shows daemon uptime for process-freshness checks.
- The only red Dependabot signal inspected in this pass, PR #49, has its code blocker fixed locally;
  dependency pin changes and PR merges are still pending.

## Blocked

- None.

## Next Steps

1. **Resolve ADR 0001 later** — lineage rich-vs-thin supersession is still deferred until a real promoted/resolved rich handoff appears under a prior `needs_follow_up` stable key
2. **Observe `near_rollup_singles` in real use** — tune only if one-off resolved echoes or informational first occurrences become repeated operator noise
3. **Triage remaining Dependabot PRs** — PR #49's code blocker is fixed locally; dependency pin
   changes and merges still need a clean dependency lane

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
- tests/test_logs_burn_in_diagnostics.py
- tests/test_review_endpoints.py
