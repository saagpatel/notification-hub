# Product Boundary

notification-hub is a localhost-only coordination sidecar. It collects local workflow events, classifies them, exposes deterministic review surfaces, and preserves evidence for operator decisions. It should stay additive: Claude Code, Codex, Claude.ai, and personal-ops workflows must keep working if notification-hub is unavailable.

## notification-hub Owns

- Local event intake on `127.0.0.1`.
- Deterministic urgency and intent classification.
- Runtime health, burn-in, log, policy, and delivery diagnostics.
- Review packages derived from recent event rollups.
- A local personal-ops import queue with explicit lifecycle states.
- Promotion tracking for queue items, including the downstream personal-ops suggestion id and final accepted, rejected, ignored, or pending outcome.
- Local review UI and API endpoints that inspect, queue, and mark handoff state without applying personal-ops work.

## personal-ops Owns

- Operator inbox aggregation across systems.
- Task suggestions, task creation, approvals, reminders, calendar changes, email actions, and all external mutations.
- The operator-mediated command that promotes a notification-hub handoff into a personal-ops task suggestion.
- Outcome sync back to notification-hub after a promoted task suggestion is accepted or rejected.

## bridge-db Owns

- Durable cross-agent memory and snapshots.
- Shared context between Codex, Claude Code, and Claude Desktop.
- Long-lived recall beyond notification-hub runtime logs and review packages.

## Non-Goals

- notification-hub does not send email, create tasks, mutate calendars, or approve work.
- notification-hub does not auto-apply personal-ops actions.
- notification-hub does not become a network service beyond localhost without an explicit product decision.
- notification-hub does not replace bridge-db as the durable multi-agent memory layer.

## Current Direction

The near-term product path is to keep the trust boundary visible while making the local review loop easier to operate:

1. Burn in real queue usage.
2. Promote reviewed handoffs into personal-ops task suggestions.
3. Sync accepted or rejected suggestion outcomes back into notification-hub.
4. Use the scripted queue scenario and runtime gates before expanding the boundary.
