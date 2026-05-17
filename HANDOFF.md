# Handoff — notification-hub

**Status:** Terminal-outcome lineage expansion complete; repo ready and console in monitor mode
**Branch:** main
**Last verified remote commit before this pass:** ff2aded (proposal-review cleanup)
**Tests:** 376 passing + 9 in mcp_server/; Ruff and Pyright passing after 2026-05-16 refresh; latest GitHub Actions on `main` passing

## Completed This Session

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

## In Progress

- Notification-hub itself is healthy and ready to expand.
- Coordination Console is in monitor mode with no active proposals, no queued handoffs, and no pending promoted outcomes.

## Blocked

- Gmail draft "Rich-evidence pipeline test — 2026-05-11" — manual deletion required via Gmail web UI (no `mail_draft_delete` in personal-ops MCP)

## Next Steps

1. **Manual:** Delete the Gmail test draft in Gmail web UI
2. **Resolve ADR 0001 later** — lineage rich-vs-thin supersession is still deferred until a real promoted/resolved rich handoff appears under a prior `needs_follow_up` stable key
3. **Observe `near_rollup_singles` in real use** — tune suppression policy based on actual volume

## Key Decisions

- `near_rollup_singles` reuses `InboxRollupReport` TypedDict (avoids new type)
- mcp_server tests patch `server._get`/`server._post` at module level — no live daemon required
- ADR 0001 left deferred-open; needs real-use data before closing
- Direct private-helper coverage is kept with a narrow Pyright ignore instead of widening the helper's public API
- The 2026-05-16 sync-degraded package was kept as local evidence, but the two handoffs were closed as reviewed rather than promoted because source checks recovered and no downstream personal-ops work was appropriate
- Stale recovered proposal keys are better handled with local dismissals than queueing: this clears monitor noise while allowing distinct future failures to appear under different keys
- A terminal group outcome should also clear matching future repeats without requiring a separate dismissal; this is now covered by a regression test for `superseded`

## Files Changed

- README.md
- docs/CURRENT-STATE.md
- HANDOFF.md
- src/notification_hub/operations.py
- tests/test_operations.py
