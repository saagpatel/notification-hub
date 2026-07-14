# Delivery Reliability Acceptance Matrix

This document is the rollout gate for the Project → BridgeDB → personal-ops producer →
notification-hub → operator pathway. Source and isolated-fixture evidence can pass before runtime
adoption; live delivery remains unknown until separately approved destination readback succeeds.

## Requirement evidence

| Requirement | Authoritative isolated evidence |
| --- | --- |
| Deterministic producer IDs | `test_hooks.py`, `test_bridge_cursor.py`; personal-ops integration commit `776c3c4` (`notification-hub.test.ts`) |
| Correlated producer acceptance receipt | personal-ops integration commit `776c3c4` (`notification-hub.test.ts`): a 2xx response is accepted only when its nonempty `event_id` matches the submitted deterministic ID |
| Identical retry / conflicting retry | `test_server.py`, `test_durable_inbox.py`, `test_producer_outbox.py` |
| HTTP timeout after possible acceptance | `test_producer_outbox.py::test_http_timeout_after_possible_acceptance_retries_idempotently` |
| Bridge downtime, cursor recovery, gaps, rewrite rejection | `test_bridge_cursor.py` |
| Restart before attempt | `test_durable_inbox.py::test_restart_before_first_attempt_preserves_queued_event` |
| Restart after channel acceptance | `test_durable_inbox.py::test_restart_after_acceptance_before_terminal_receipt_skips_accepted_channel` |
| Restart preserves hourly channel rate limits | `test_durable_inbox.py::test_recent_channel_acceptance_times_reconstructs_restart_rate_history`, `test_suppression.py::test_rate_history_restores_across_restart` |
| Quiet-hour and overflow restart | `test_durable_inbox.py`, `test_pipeline.py` |
| Queue-full honesty | `test_pipeline.py::test_full_quiet_queue_fails_honestly_without_processed_log` |
| Bounded retry and poison handling | `test_durable_inbox.py`, `test_producer_outbox.py` |
| Partial downstream failure | `test_pipeline.py::test_one_channel_acceptance_and_other_channel_failure_are_distinct` |
| Producer destination contract enforcement | `test_pipeline.py::test_required_log_destination_blocks_normal_slack`, `test_pipeline.py::test_required_log_destination_blocks_urgent_external_channels`, `test_server.py::test_durable_worker_honors_log_only_destination_contract` |
| Secret-safe transport failure categories and durable persistence | `test_channels.py::test_detailed_result_*`, `test_server.py::test_durable_worker_persists_secret_safe_transport_failure_category` |
| Acceptance without readback | `test_delivery_readback.py` |
| Readback and explicit observation | `test_delivery_readback.py`, `test_delivery_e2e_fixture.py` |
| Semantic suppression evidence | `test_suppression.py`, `test_pipeline.py` |
| Privacy redaction | `test_channels.py` |
| Additive migration and history preservation | `test_durable_inbox.py`, `test_producer_outbox.py` |
| Producer terminal disposition without history deletion | personal-ops integration commit `776c3c4` (`notification-hub.test.ts`) |
| Producer timeout, network, HTTP, and receipt failures are bounded and secret-safe | personal-ops integration commit `776c3c4` (`notification-hub.test.ts`) |
| CI and smoke isolation from the machine's live hub | personal-ops integration commit `776c3c4` (`verify-harness.ts`, `generation-reconcile.test.ts`, `notification-hub.test.ts`) |
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
- The personal-ops durable producer repair and immutable-path health fix are published at integration
  commit `543da29` on `origin/codex/personal-ops-delivery-activation`. Delivery commit `776c3c4`, based
  on runtime commit `3f2cf5b`, was transiently activated as immutable release
  `56a0611abe01687f7d2915ffd238beb3fa2fbcbc00d5bbfb791d70a6a295b14b` with readback verified across
  CLI, daemon, Codex MCP, Claude MCP, LaunchAgent, and desktop.
- That activation is historical evidence, not current runtime truth. A concurrent serialized installer
  has replaced it repeatedly with commits that do not contain the integration branch. The latest
  observed receipt was release `87f1b54d67afca422239ccce5ff09d98b2de0de444ee83325da822310783393e`
  from commit `d9dbda8`; operators must consult `personal-ops install generation-status --json` for the
  moving current value. The durable personal-ops producer must therefore be reported as published and
  validated but **not currently deployed**.
- Before activation, SQLite-backup snapshot `2026-07-14T07-10-53Z` captured schema v36 and the live
  database passed `integrity_check`. After activation, schema v36, 55 application tables, and all
  sampled append-only history counts were preserved or increased; the new producer outbox also
  passed `integrity_check`.
- Test mode blocks port 9199, the generation LaunchAgent preserves only explicitly isolated test
  transport variables, and the verification harness uses an ephemeral loopback hub. The isolated
  generation smoke and the directly affected 49-test delivery/generation/runtime matrix passed on
  the final baseline without a live test destination; the broader matrix passed 105 tests on the
  immediately preceding baseline.
- Activation produced deterministic startup event
  `personal-ops:daemon.started:06b0217c3ce9597961865175`. The producer stored one matching
  `http:201:<event-id>` receipt; notification-hub classified it as log-only, recorded no channel rows,
  and therefore made no push or Slack attempt. A repeated historical attention event reused
  deterministic ID `personal-ops:operator.attention_item:2e9a32212f723163addcf78a` and was
  suppressed without a channel row.
- Current generation readback is current for the competing installer's own commit, but `personal-ops
  install check --json`
  reports a contradictory degraded result: immutable release-resolved wrapper targets are classified
  as a different install layout, and the LaunchAgent's stable `install/current/app` working directory
  is compared against the resolved release directory. Commit `543da29` repairs and regression-tests
  this comparison, but it is not deployed. Treat install-check health as untrusted until the competing
  runtime-authority lane incorporates and activates the integration branch.
- Current live health is degraded by unresolved historical and recent delivery failures. Those rows
  remain retained and actionable; this rollout did not replay, acknowledge, disposition, or clear
  them merely to improve health.
- Runtime verification on 2026-07-14 exposed a destination-contract bypass: the deterministic
  `personal-ops:daemon.stopping:ecb9f7a87ebaee058744808f` event declared
  `required_destinations=["log"]`, but severity routing still attempted Slack and received provider
  acceptance. This is acceptance evidence only: no delivered or observed receipt exists. The source
  repair makes non-empty producer destination lists authoritative for external-channel eligibility;
  history is preserved and the accepted Slack row is not rewritten or dispositioned.
- Historical channel rows keep their original generic `push_transport_failed` or
  `slack_transport_failed` evidence. Gate 1 does not rewrite history; future attempts persist bounded,
  secret-safe causes such as notifier timeout, HTTP class, network failure, or rate limiting.
- The Bridge cursor remains intentionally disabled, so the runtime still uses the Markdown watcher.
- No synthetic live notification has been sent, and no live operator-observation receipt exists.

The pathway must remain reported as Gate-1 notification-hub deployed, with the personal-ops durable
producer validated but displaced by a competing installer. End-to-end delivery and operator observation
remain unproven until runtime authority is serialized, the integration branch is activated, and separately
approved live destination readback resolves these unknowns.
