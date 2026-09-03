# ADR 0005: A channel outage is one decision, not one per event

## Status

Implemented in source and tests. Extends ADR 0004; nothing in 0004 is reversed.

## Context

ADR 0004 gave partial deliveries an honest status and made an unresolved one degrade health,
with the instruction "review every partially delivered event". Ten days later the live inbox held
98 unresolved partial deliveries, and every one of them was the same event: Slack accepted, push
failed with `push_notifier_nonzero_exit`, five attempts, terminal. The push channel had accepted
nothing since 2026-08-24T01:34Z, and the pile was still growing at roughly ten events a day.

Two things were wrong, and neither was the classification.

The instruction did not describe the work. Ninety-eight reviews would have produced one finding,
which the first review already produced. The unit an operator can act on is the channel, because
restoring the channel is what stops the pile; filing 98 dispositions leaves the cause untouched
and the next day's events arrive the same way.

There was also no way to file them. `disposition_dead_letter` had no caller outside the test
suite, so nothing an operator could type resolved a partial delivery at all. A health check that
degrades on a condition with no available remedy trains its reader to ignore it, which is exactly
the failure ADR 0004 was written to stop.

## Decision

**A channel that has stayed silent since it started failing is an outage, and the outage is what
health reports.** Silence is the signal, not failure: every partial delivery fails a channel by
definition, so a bare "has a failure since its last acceptance" test names the working channel
too. A channel qualifies only once it has accepted nothing for `CHANNEL_OUTAGE_AFTER_SECONDS`
(one hour) while deliveries keep failing; a channel that has never accepted anything is timed
from its first failure, having no acceptance to be silent since.

`channel_outage_summary` returns, per channel, the last acceptance, the moment failures began,
the number of failed deliveries since, and the number of unresolved partial deliveries the outage
alone explains. `collect_health` carries these as `failing_channels` and, when any exist, replaces
the per-event review instruction with the channel, its silence, and its blast radius. Per-event
review remains the instruction when no channel explains the partials.

**Resolution stays operator-initiated and evidence-bearing, but the unit is the cause.**
`disposition_partial_deliveries_for_channel` resolves, in one action, every unresolved partial
delivery whose *only* non-accepting channel is the named one, writing the same disposition,
reference, and timestamp that a single-event disposition writes. Nothing auto-resolves: an
operator still supplies the reason and the reference, and `notification-hub disposition-partials
--dry-run` lists the population before anything is written. The `--until` bound exists so
resolving a closed outage cannot silently claim events that arrived after it.

**The predicate is asymmetric, and deliberately so.** The blamed channel must have actually
`failed`. Every *other* channel must hold a positive receipt. So a `dead_lettered` event (nobody
got it), an event with a second failing channel, an event already dispositioned by hand, and an
event whose named channel is merely `buffered` or still `outcome_unknown` are all excluded. The
count that means "reached nobody" cannot be reduced by resolving an outage, and evidence awaiting
reconciliation is never resolved as though it were a known failure.

A cutoff passed to `--until` is parsed as a timestamp and refused if it is not one. SQLite
compares `dead_lettered_at <= 'yesterday'` lexically and matches every row, so an unparsed word
would not narrow the sweep, it would remove the bound entirely and resolve strictly more than the
operator asked for.

The population a health report counts and the population a bulk disposition writes to come from
one shared SQL predicate. An operator acts on the number they were shown; if the two queries could
drift, they would be acting on a different set than the one they approved.

## Consequences

- Health degrades on a named outage even before it produces its first partial delivery, so a
  channel that goes quiet is visible sooner than the pile it will create. The one-hour threshold
  is the cost: a channel down for less than that is not reported, which is the same trade the
  backlog and dead-letter thresholds already make.
- Existing rows are still not rewritten. Resolving the 98 is an operator action against a
  backed-up database, and it records who decided and why.
- Restoring a channel does not resolve its partial deliveries. That is deliberate: the events
  remain unresolved evidence until someone states what happened to them.
