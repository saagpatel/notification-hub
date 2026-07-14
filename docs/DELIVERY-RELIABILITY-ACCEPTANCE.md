# Delivery Reliability Acceptance Matrix

This document is the rollout gate for the Project → BridgeDB → personal-ops producer →
notification-hub → operator pathway. Source and isolated-fixture evidence can pass before runtime
adoption; live delivery remains unknown until separately approved destination readback succeeds.

## Requirement evidence

| Requirement | Authoritative isolated evidence |
| --- | --- |
| Deterministic producer IDs | `test_hooks.py`, `test_bridge_cursor.py`; personal-ops feature commit `89d9b8b` (`notification-hub.test.ts`) |
| Identical retry / conflicting retry | `test_server.py`, `test_durable_inbox.py`, `test_producer_outbox.py` |
| HTTP timeout after possible acceptance | `test_producer_outbox.py::test_http_timeout_after_possible_acceptance_retries_idempotently` |
| Bridge downtime, cursor recovery, gaps, rewrite rejection | `test_bridge_cursor.py` |
| Restart before attempt | `test_durable_inbox.py::test_restart_before_first_attempt_preserves_queued_event` |
| Restart after channel acceptance | `test_durable_inbox.py::test_restart_after_acceptance_before_terminal_receipt_skips_accepted_channel` |
| Quiet-hour and overflow restart | `test_durable_inbox.py`, `test_pipeline.py` |
| Queue-full honesty | `test_pipeline.py::test_full_quiet_queue_fails_honestly_without_processed_log` |
| Bounded retry and poison handling | `test_durable_inbox.py`, `test_producer_outbox.py` |
| Partial downstream failure | `test_pipeline.py::test_one_channel_acceptance_and_other_channel_failure_are_distinct` |
| Transport timeout and non-zero exit | `test_channels.py` |
| Acceptance without readback | `test_delivery_readback.py` |
| Readback and explicit observation | `test_delivery_readback.py`, `test_delivery_e2e_fixture.py` |
| Semantic suppression evidence | `test_suppression.py`, `test_pipeline.py` |
| Privacy redaction | `test_channels.py` |
| Additive migration and history preservation | `test_durable_inbox.py`, `test_producer_outbox.py` |
| Producer terminal disposition without history deletion | personal-ops feature commit `89d9b8b` (`notification-hub.test.ts`) |
| No live test destinations or Keychain | `tests/conftest.py`, `test_channels.py`, `test_config.py` |
| Full isolated chain | `test_delivery_e2e_fixture.py` |

## Pre-rollout receipt

Before changing the running service, record all of the following without modifying live state:

- repository branch, commit, and dirty state for notification-hub and personal-ops;
- LaunchAgent path, executable, arguments, PID, state, and feature-flag environment;
- BridgeDB maximum activity ID and protected-row counts;
- notification durable-event counts by status and channel state;
- producer-outbox counts by state, if it exists;
- JSONL line count and file digest;
- installed hook and producer-helper digests;
- SQLite `integrity_check` results from read-only connections.

Create SQLite backups using the SQLite backup API before migration. Never copy only the main file
while a WAL database is live.

## Rollout stop conditions

Stop and roll back on any event-count mismatch, missing historical row, cursor regression, new source
gap without explanation, duplicate-attempt increase, unresolved producer rejection, failed isolated
readback, privacy regression, hook/helper mismatch, or degraded schema integrity.

## Component rollback

- Schema: retain additive nullable columns and tables. Restore the pre-rollout database only if the
  migration itself corrupts or loses rows; preserve the failed database and every post-backup event
  for reconciliation first.
- Daemon: deploy the prior commit and restart through the existing LaunchAgent. Do not delete v5
  receipt state or rewind cursors.
- LaunchAgent: restore the exact backed-up plist, bootstrap it, and verify PID, arguments, and health.
- Producer hooks: restore all backed-up hook and helper files as one unit. Keep the producer outbox;
  the prior hooks may ignore it, but rollback must not delete queued or accepted history.
- Bridge cursor: unset `NOTIFICATION_HUB_BRIDGE_CURSOR_ENABLED` and return to the Markdown watcher.
  Never move the stored cursor backward without an isolated replay and duplicate-impact review.

After rollback, reconcile database counts, JSONL digest/line count, hook digests, cursor, unresolved
dead letters, and channel receipts against the pre-rollout receipt.

## Explicit remaining unknowns

- Live Slack webhook success proves provider acceptance only; no live Slack readback adapter has been
  selected or approved.
- A terminal-notifier zero exit proves local command acceptance, not display or operator observation.
- Gate 1 is installed in the running LaunchAgent and machine hooks. Runtime wiring, additive schema
  migration, history reconciliation, local hook-producer acceptance, explicit producer identity,
  and safe per-channel acceptance/error evidence have been verified.
- The personal-ops durable producer repair is published as the single clean feature commit `89d9b8b`
  on `origin/codex/personal-ops-delivery-reliability`. It is not merged, installed, or active; doing
  so is a separate rollout gate because it changes the personal-ops daemon and creates a producer
  outbox under live state.
- Current live health is degraded by unresolved historical and recent delivery failures. Those rows
  remain retained and actionable; this rollout did not replay, acknowledge, disposition, or clear
  them merely to improve health.
- The Bridge cursor remains intentionally disabled, so the runtime still uses the Markdown watcher.
- No synthetic live notification has been sent, and no live operator-observation receipt exists.

The pathway must remain reported as Gate-1 notification-hub deployed, personal-ops producer staged,
and end-to-end delivery unproven until separately approved producer activation and live destination
readback resolve these unknowns.
