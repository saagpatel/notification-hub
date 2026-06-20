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

Delivery is at-least-once. The background worker claims due rows, runs the existing pipeline, writes
JSONL only for processed non-burst events, and then marks terminal state. Transient failures retry up
to 5 attempts with exponential backoff capped around 10 minutes. Exhausted events move to the
dead-letter state.

On startup, expired `processing` leases are reclaimed to `retry_scheduled`, so a restart during
delivery becomes retryable backlog instead of silent loss.

Processed and suppressed rows are retained for 30 days while preserving at least the newest 10,000
terminal rows. Dead-letter rows are retained for 90 days. Manual redrive is intentionally deferred.

## Health Contract

`/health/details`, `notification-hub status`, `logs`, `burn-in`, `verify-runtime`, and `/review`
surface durable inbox status. Dead letters, stale processing leases, and old queued backlog degrade
operator health. The JSONL event log remains processed-event audit history and existing JSONL readers
continue to work.

## Consequences

Producers can stay fire-and-forget for v1 because a 201 now confirms durable local acceptance. The
tradeoff is that delivery can happen shortly after the response, so live smoke verification waits
briefly for the worker to write the JSONL audit record.
