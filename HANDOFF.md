# Handoff — notification-hub

**Status:** Next pass complete; implementation backlog closed, CI coverage follow-up wired locally
**Branch:** main
**Last commit:** 6d1864c (PR #40 merged)
**Tests:** 376 passing + 9 in mcp_server/; Ruff and Pyright passing after 2026-05-16 refresh

## Completed This Session

- **PR #37** — `action-export-retention` CLI: prune action-export files, dry-run by default, `--apply` to delete, `--keep N`; first run pruned 20 files
- **PR #38** — `near_rollup_singles`: surfaces count=1 inbox events invisible to rollup pipeline; new field on `InboxReport`, wired into `run_inbox` and CLI display
- **PR #39** — `docs/CURRENT-STATE.md` updated to reflect session 2 state
- **PR #40** — `mcp_server` smoke tests: 9 in-process tests covering all 7 tool wrappers via FastMCP Client + AsyncMock; replaced dead echo-tool stub
- **2026-05-16 next pass** — fixed Pyright's private-helper test import warning, refreshed `docs/CURRENT-STATE.md`, and intentionally kept `.claude/` plus this handoff artifact
- **2026-05-16 CI follow-up** — added `uv run --directory mcp_server --frozen pytest` to GitHub Actions and documented the separate MCP test command

## In Progress

None.

## Blocked

- Gmail draft "Rich-evidence pipeline test — 2026-05-11" — manual deletion required via Gmail web UI (no `mail_draft_delete` in personal-ops MCP)

## Next Steps

1. **Manual:** Delete the Gmail test draft in Gmail web UI
2. **Resolve ADR 0001** — lineage rich-vs-thin supersession; run a real burn-in session first to get signal
3. **Observe `near_rollup_singles` in real use** — tune suppression policy based on actual volume

## Key Decisions

- `near_rollup_singles` reuses `InboxRollupReport` TypedDict (avoids new type)
- mcp_server tests patch `server._get`/`server._post` at module level — no live daemon required
- ADR 0001 left deferred-open; needs real-use data before closing
- Direct private-helper coverage is kept with a narrow Pyright ignore instead of widening the helper's public API

## Files Changed

- tests/test_operations.py
- docs/CURRENT-STATE.md
- HANDOFF.md (local artifact kept intentionally)
