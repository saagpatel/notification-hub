# ADR 0003: Delivery Semantics and Bridge Cursor

## Status

Implemented in source and isolated tests. Runtime adoption is feature-flagged and has not been
performed.

## Context

The durable inbox established local acceptance, but a terminal channel command, HTTP 2xx response,
or JSONL line could still be mistaken for operator-observed delivery. Retries also lacked a durable
per-channel receipt, so a downstream partial failure could resend to a channel that had already
accepted the event. The Markdown Bridge export was a lossy synchronization boundary without a
durable consumer cursor.

## Decision

Producer-supplied event IDs are canonical. Reusing an ID with the same canonical payload is an
idempotent retry; reusing it with a different payload returns a conflict. Hook templates derive IDs
from stable producer input. Bridge activity uses `bridge-db:activity:<row-id>` and records the row ID
as both source revision and sequence.

Delivery is recorded per event and channel with these distinct meanings:

- `attempted`: the hub invoked a destination transport.
- `buffered`: policy deferred the event without consuming the failure budget.
- `accepted`: the destination transport returned success; this is not readback.
- `delivered`: a destination-specific readback adapter returned a non-empty receipt.
- `observed`: an operator or destination supplied an explicit observation reference.
- `failed`: the last transport attempt failed and remains retryable until exhausted.
- `dispositioned`: an operator explicitly resolved the channel outcome.

Retries skip channels already in `accepted`, `delivered`, `observed`, or `dispositioned`. Receipt
state is monotonic, and acceptance, delivery, observation, and terminal-disposition references are
stored separately so later evidence cannot overwrite earlier evidence. Exhausted events stay
actionable until an explicit dead-letter disposition with a reference; disposition does not delete
the historical row. Retention does not delete rows that own channel receipts, and it does not delete
unresolved dead letters.

Project and severity are not deduplication identities. Semantic suppression is opt-in through a
producer `semantic_dedupe_key`, scoped by producer, and records both the predecessor event ID and the
policy that suppressed it. Existing explicit noise rules may still suppress byte-equivalent bursts;
those also record predecessor and policy.

The Bridge consumer uses a read-only SQLite connection and a durable monotonic cursor. It advances
only after the corresponding deterministic event is accepted into the hub inbox. A crash between
acceptance and cursor advancement safely replays the same ID. First activation starts at the current
Bridge maximum by default, preventing an accidental historical notification flood; backfill is an
explicit isolated operation. Source ID gaps are surfaced, and a source maximum below the stored
cursor is rejected as a rewrite/regression instead of silently rewinding.

## Rollout

`NOTIFICATION_HUB_BRIDGE_CURSOR_ENABLED` defaults to disabled. Before enabling it:

1. Back up the notification-hub inbox and confirm row counts and schema version.
2. Stop the existing Markdown watcher through the normal service deployment procedure.
3. Apply the additive schema initialization and verify old event counts are unchanged.
4. Start with an isolated Bridge fixture and fake destination adapters.
5. Verify cursor, idempotency-conflict, retry, dead-letter, channel-state, and readback receipts.
6. Only with separate operator approval, enable the cursor against live BridgeDB and perform one
   controlled live smoke event with end-to-end destination readback.

No live destination is contacted by schema initialization or cursor polling alone.
Dedicated live-smoke tooling must refuse to run unless both
`NOTIFICATION_HUB_LIVE_SMOKE=1` and `NOTIFICATION_HUB_OPERATOR_APPROVED=1` are present, and test mode
is off. Test mode blocks terminal-notifier, Slack HTTP, and Keychain lookup unless an isolated test
double explicitly opts in.

## Rollback

Disable `NOTIFICATION_HUB_BRIDGE_CURSOR_ENABLED` and restore the previous LaunchAgent/runtime
configuration. The new tables and nullable columns are additive and may remain in place; do not
delete them or rewrite event history. If runtime behavior must be reverted, deploy the previous
code while retaining the pre-rollout database backup and all post-rollout rows for reconciliation.
Compare event counts, unresolved dead letters, per-channel receipts, and the Bridge cursor before
and after rollback. Never roll the cursor backward without an isolated replay plan, because that can
create duplicate attempts.

## Verification Contract

Acceptance requires isolated tests for duplicate IDs, conflicting IDs, retries, partial channel
failure, restart lease recovery, transport timeout, quiet-hour deferral, malformed events, poison
messages, Bridge unavailability, cursor-write loss, ordering, and privacy redaction. A log line or
transport success is evidence only of processing or acceptance. `delivered` requires destination
readback; `observed` requires an explicit observation reference. Final runtime health must report
queued/retrying/suppressed/dead-lettered counts plus per-channel attempted/accepted/delivered/
observed/dispositioned states.
