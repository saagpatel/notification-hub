# ADR 0002: Durable Inbox and Dead-Letter Box

## Status

Accepted, implemented locally.

## Context

Before this change, `POST /events` validated an event and then ran the delivery pipeline inline. A
SIGTERM, restart, or process crash during that inline path could lose the event after the producer
believed the hub had accepted it. JSONL remained useful as processed-event audit history, but it was
not a durability boundary.

## Decision

`POST /events` now means: the event is validated and durably committed to SQLite before the 201
response is returned. It does not mean push, Slack, suppression, or JSONL audit logging already
completed.

The durable inbox is SQLite at `~/.local/share/notification-hub/inbox.sqlite3`. Accepted events move
through:

- `queued`
- `processing`
- `retry_scheduled`
- `processed`
- `suppressed`
- `dead_lettered`
- `partially_delivered`
- `reconciliation_required`
- `reconciled_succeeded`
- `reconciled_absent`

Delivery tracks per-channel evidence. The background worker claims due rows, runs the existing
pipeline, writes JSONL only for processed non-burst events, and then marks terminal state.
Failures known to have no provider effect retry up
to 5 attempts with exponential backoff capped around 10 minutes. Exhausted events become
`partially_delivered` when a channel has positive evidence, otherwise `dead_lettered`.
Ambiguous external outcomes are quarantined as `reconciliation_required`.

Local channel throttling is a deferral, not a delivery failure. Rate-limited rows return to
`retry_scheduled` at the next available channel slot without consuming the event failure budget or
recording a transport attempt. Per-channel acceptance remains monotonic, so a retry skips any
channel that already supplied an acceptance receipt. If another channel has a real transport
failure in the same pass, that failure still consumes one attempt and retains its own backoff.

On startup, expired `processing` leases are reclaimed to `retry_scheduled` unless an external
channel is `attempted` or `outcome_unknown`; those events become `reconciliation_required`
instead of replaying an ambiguous delivery.

Processed and suppressed rows without channel evidence are pruned after 30 days while preserving
the newest 10,000 processed/suppressed rows. Processed and suppressed rows with channel evidence
are pruned after 180 days, except immutable unknown-outcome and reconciliation evidence.
Dead-letter pruning after 90 days requires a disposition and no channel evidence. Automatic
pruning is disabled in preserve-history mode. Manual redrive is intentionally deferred.

## Health Contract

`/health/details`, `notification-hub status`, `logs`, `burn-in`, `verify-runtime`, and `/review`
surface durable inbox status. Unresolved dead letters, stale processing leases, and old queued backlog degrade
operator health. A `retry_scheduled` row with a future `next_attempt_at` is a healthy deferral, even
when the event itself is old; it degrades health only after its scheduled retry has been overdue
beyond the backlog threshold. The JSONL event log remains processed-event audit history and
existing JSONL readers continue to work.

## Consequences

Producers can stay fire-and-forget for v1 because a 201 now confirms durable local acceptance. The
tradeoff is that delivery can happen shortly after the response, so live smoke verification waits
briefly for the worker to write the JSONL audit record.
