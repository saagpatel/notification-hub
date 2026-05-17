# ADR 0001 — Should an evidence-quality upgrade supersede a prior `needs_follow_up` outcome?

**Status:** Accepted — sticky follow-up with explicit rich re-review signal
**Date:** 2026-05-11
**Context:** This is the first ADR in the repository. The pattern: capture
non-obvious design decisions and trade-offs so future maintainers don't have
to re-derive them from code archaeology.

## Background

`notification-hub` classifies action proposals as `evidence_quality: rich` or
`thin` based on whether the underlying event context carries both an anchor
(`thread_id` or `message_id`) and a work-item identifier (`draft_id`,
`approval_id`, `provider_draft_id`, `review_id`, or `queue_id`). See
`_evidence_quality` in `operations.py`.

Operators can group active proposals and record a group outcome
(`accepted` / `rejected` / `snoozed` / `superseded` / `needs_follow_up`).
A `needs_follow_up` outcome is the standard way to say "evidence isn't
sufficient yet — leave this in handled-history until more arrives."

The Coordination Console then uses `_build_proposal_lineage` to classify
new active proposals against group-history. If a new proposal shares a
`stable_proposal_key` with a prior `needs_follow_up` outcome, it is
classified as `follow_up` — handled history, not fresh active work — even
when its `evidence_event_id` has rotated.

This is intentional: it implements the operator's explicit "preserve
follow-up state across evidence-event rotation" guidance (recorded as the
2026-05-10 14:56 UTC group outcome reason).

## The question

What should happen when a **rich** proposal arrives sharing a stable proposal
key with a prior `needs_follow_up` outcome that was recorded when only **thin**
evidence existed?

Two reasonable answers:

### Option A — Rich evidence supersedes prior `needs_follow_up`

The operator's `needs_follow_up` decision was effectively "I need more
evidence before I can act on this." When rich evidence arrives, that
condition has been met. The proposal should appear as `new` (fresh active
work) so the operator can re-evaluate against the better signal.

**Pros:**
- Semantically intuitive: rich evidence *is* the inspection the prior
  follow-up was waiting for.
- Avoids the situation where a real rich-evidence handoff sits invisible
  in `handled_actions` while the operator stares at "monitor mode."
- Mirrors how dismissals work for evidence-quality: if a rich signal
  appears under a dismissed proposal key, the dismissal might also need to
  be re-evaluated.

**Cons:**
- Overrides an explicit operator decision without explicit operator action.
- The operator's reason in the 2026-05-10 group outcome said "stable
  proposal keys should preserve follow-up state across evidence-event
  rotation" — they anticipated evidence rotation and chose to ignore it.
  Upgrading evidence quality is just a *kind* of rotation.
- Could create churn: an operator who has deliberately parked a category
  of proposals might be surprised to see them resurface.

### Option B — Operator must explicitly overturn (status quo)

`needs_follow_up` stays sticky until the operator records a new outcome.
Rich-evidence arrivals on the same stable key remain classified as
`follow_up` lineage. The operator can use the existing group-outcome
mechanism (`accepted`, `rejected`, `superseded`) to overturn.

**Pros:**
- Respects operator intent literally — no implicit override.
- Predictable: the lineage classifier is purely a function of recorded
  outcomes; nothing in the system reverses an operator decision without
  another operator decision.
- The `evidence_event_rotated` and `evidence_quality` fields are already
  visible in `handled_actions`, so an operator who is watching follow-up
  history can spot the rich-evidence arrival manually.

**Cons:**
- Requires operator attention to surface rich-evidence arrivals that
  would otherwise be missed in `monitor` mode.
- Future operator may not realize a deliberate sticky decision is hiding
  fresh-evidence signal.
- The console's current `next_action: "Monitor /review for the next real
  handoff signal"` is misleading when handled follow-up has new rich evidence
  underneath.

## Decision

**Accept Option B with a visibility mitigation.** Keep the current
lineage behavior: `needs_follow_up` stays sticky until the operator records
a later outcome. Do not automatically promote rich arrivals back into active
proposal work.

When rich evidence arrives under handled follow-up history, surface it as an
operator-visible re-review signal in the Coordination Console. This makes the
new evidence visible without reversing the earlier operator decision or
creating downstream personal-ops work.

Until then:

- `_build_proposal_lineage` continues to classify rich proposals as
  `follow_up` when they match a `needs_follow_up` group's stable proposal
  key.
- The Coordination Console exposes a `follow_up_review` review mode,
  `rich_follow_up_review_count`, and a `next_signal` status of `review` when
  rich handled follow-up needs an operator decision.
- Active proposal count remains zero unless the operator records a new
  outcome or queues/promotes work through the existing operator-mediated
  path.

### Recheck — 2026-05-16

The real-use burn-in pass did not change the decision. It produced useful
live evidence for queue review, `near_rollup_singles`, and repeated
personal-ops sync-degraded noise, but it did not produce an organic
promoted or resolved rich handoff under a prior `needs_follow_up` stable
key.

The console did show handled mail follow-up history with rich evidence, and
`near_rollup_singles` exposed real count=1 mail signals, but those cases do
not answer the supersession question. Keep Option B as the current behavior:
operator-recorded `needs_follow_up` stays sticky until an explicit later
operator outcome changes it.

### Recheck — 2026-05-17

The follow-up pass created real approval entries with thread, draft, provider
draft, group, mailbox, and approval IDs. Those entries confirmed the practical
gap: rich handled follow-up can sit under a sticky `needs_follow_up` stable key
while the console says monitor mode.

This evidence re-opened the ADR, but it did not justify automatic
supersession. The implemented fix is the middle path above: rich handled
follow-up becomes an explicit re-review signal, while the lineage status stays
`follow_up` and active proposals remain operator-controlled.

## Triggers to re-open

Re-open this decision when **any** of the following happens:

1. An operator reports being surprised by a rich-evidence proposal sitting
   in `follow_up` when they expected fresh work.
2. An operator records a new group outcome (e.g., `accepted` or `rejected`)
   that explicitly overturns a prior `needs_follow_up` because rich
   evidence arrived — and they note this is a recurring need.
3. The re-review lane repeatedly produces noise or fails to get acted on,
   suggesting that sticky follow-up is still too conservative.

## Related fields and surfaces

For future reference, the actual field names involved (a few aliases came
up in conversation and aren't real fields):

| Concept | Real field name | Where |
|---|---|---|
| Next operator-facing signal | `next_signal` (object) | `coordination-console` JSON output, `/review/coordination-console` |
| Rich handled follow-up re-review count | `proposal_review.rich_follow_up_review_count` | `coordination-console` JSON output, `/review/coordination-console` |
| Rich handled follow-up action ids | `proposal_review.rich_follow_up_action_ids` | `coordination-console` JSON output, `/review/coordination-console` |
| Rich-evidence readiness in synthetic drill | `scenario.rich_evidence_ready` (bool) | `operator-handoff-drill` output |
| Per-proposal evidence rotation | `evidence_event_rotated` (bool), `stable_key_matched` (bool) | inside each `handled_actions` entry |

`real_signal_readiness` is **not** a real field — the `/review` UI panel
titled "Real Signal Readiness" is rendered from `next_signal`, not from a
separate JSON field by that name.

## References

- `_build_proposal_lineage` in `src/notification_hub/operations.py`
  (around line 4980)
- `_latest_follow_up_action_markers` in `src/notification_hub/operations.py`
  (around line 4918)
- `_evidence_quality` (rich/thin scoring) in
  `src/notification_hub/operations.py` (around line 5070)
- `docs/CURRENT-STATE.md` — Truth-Gap Status section
