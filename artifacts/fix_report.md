# Notification Irreversible-Action Fix Report

## Outcome

`fixed`

This report covers the source-tree repair for `NOTIF-AUTH-001`,
`NOTIF-DELIVERY-001`, and `NOTIF-DELIVERY-002`. No daemon was started, no live
provider was contacted, and no notification or provider retry was performed.

## Controls

- `NOTIF-AUTH-001`: authenticated producer source/destination allowlists are
  enforced at intake. The authenticated principal and exact authorized
  destination ceiling are persisted and bound into the durable idempotency
  digest. Delivery-time policy reloads can narrow but cannot widen that ceiling.
- `NOTIF-DELIVERY-001`: provider results distinguish `accepted`, `failed`, and
  `outcome_unknown`. Ambiguous Slack responses and post-attempt stale leases for
  push or Slack are quarantined without automatic retry. Stale local-log
  attempts remain retryable.
- `NOTIF-DELIVERY-002`: external copies redact complete Authorization bearer
  values, credential-shaped standalone bearer strings, Slack and Discord
  webhook URLs, generic `webhook=` assignments, local paths, and secret-class
  caller metadata before formatter or subprocess use.

## Verification

- Focused notification controls: `195 passed`.
- Full repository suite: `563 passed`.
- Ruff: `All checks passed`.
- Pyright: `0 errors, 0 warnings, 0 informations`.
- Codex ops smoke, isolated disposable `CODEX_HOME`: `3 passed`, `0 failed`,
  `0 errors`.
- Hook health, isolated report path: `PASS`.
- Hook tests with read-only control inputs linked into an isolated home:
  `313 tests`, `OK`.
- Codex MCP registry readback: readable; configured read-only launch posture
  remained visible for engraph and GitHub.
- Engraph MCP health/readback: responsive. The vault index was stale and the
  `notification-hub` project bundle was empty; those are operating-state
  warnings, not evidence of this source repair.
- MCP process posture: `attention` because the report-only sweep observed 532
  live-parent-owned MCP-like processes. It found zero orphan suspects and zero
  stale apply candidates. No cleanup was attempted.

All test runs used fake transports or patched providers and redirected writable
home/cache/database/report state to disposable locations under `/private/tmp`.

## Residual Unknowns

- Live daemon activation and external provider acceptance/readback remain
  untested by design.
- The worktree base predates two current-main health-reporting commits. Those
  unrelated changes were not merged into this repair.
- Slack incoming webhooks do not expose a provider-side idempotency mechanism or
  unique provider receipt in this integration. A successful HTTP response is
  retained as provider-specific acceptance evidence, not proof of observation.
