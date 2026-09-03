# ADR 0004: Partial delivery is not a dead letter

## Status

Implemented in source and tests. Amends the delivery semantics of ADR 0003; nothing in 0003 is
reversed.

## Context

ADR 0003 defined per-channel delivery states and said a failed channel "remains retryable until
exhausted". It did not say what the *event's* terminal status should be when the channels
disagree, and the implementation had only one answer: exhausted attempts became `dead_lettered`
regardless of what any destination had already done.

That produced a measurable lie. Between 2026-08-24 and 2026-09-03, macOS notification permission
for `terminal-notifier` was off, so every push attempt exited 3. Ninety-eight events in that
window were recorded `dead_lettered` after Slack had already accepted them. Every one of those
events reached a human. The dead-letter count is read, by health checks and by the operator, as
"notifications that reached nobody", and for three-quarters of a month it was wrong by 98.

A count that overstates failure is not a safe error. It trains its reader to discount the number,
which is exactly what happened: the dead letters were visible the whole time and nobody acted on
them, because the population was known to contain events that had actually been delivered.

## Decision

Exhausted attempts resolve to one of two terminal statuses, decided by evidence already in
`channel_deliveries`:

- **`dead_lettered`**: no channel reached `accepted`, `delivered`, `observed`, or
  `dispositioned`. Nobody got it. This is the count that must stay honest.
- **`partially_delivered`** (new): at least one channel reached one of those states and the
  attempts still ran out. Somebody got it, and something is still broken.

`buffered` is not an acceptance. It is policy deferral with no receipt, so an event that only ever
buffered is a dead letter, not a partial delivery.

Both statuses are terminal, both keep writing `dead_lettered_at` as the terminal timestamp, and
both are dispositionable by an operator without deleting history. Health reports
`partially_delivered_count` and `unresolved_partially_delivered_count` alongside the dead-letter
counts, and an unresolved partial delivery degrades health: it ranks below an outright dead letter
because someone was reached, and above silence because a destination is still failing.

The set of channel states that count as delivery evidence is now named once
(`TERMINAL_POSITIVE_CHANNEL_STATES`) and shared with the retry-skip logic, so "which channels may
a retry skip" and "did this event reach anyone" cannot drift apart.

## Consequences

- Existing rows are not rewritten by this change. The 98 historical rows keep the status they were
  given; a backfill is a separate, explicitly operator-run migration against a backed-up database.
- Any consumer counting `status = 'dead_lettered'` now sees a smaller, truer number and will not
  see partial deliveries unless it asks for them.
