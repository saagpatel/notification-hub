# Delivery Reliability Acceptance Matrix

This document is the rollout gate for the Project → BridgeDB → personal-ops producer →
notification-hub → operator pathway. Source and isolated-fixture evidence can pass before runtime
adoption. Gate 2 now has one approved local-push acceptance and destination readback receipt;
operator observation remains a separate explicit transition.

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
- A terminal-notifier zero exit proves local command acceptance only. Gate 2 supplements that receipt
  with `terminal-notifier -list` destination readback; explicit operator observation is still pending.
- Gate 1 is installed in the running LaunchAgent and machine hooks. Runtime wiring, additive schema
  migration, history reconciliation, local hook-producer acceptance, explicit producer identity,
  and safe per-channel acceptance/error evidence have been verified.
- The personal-ops durable producer, correlated acceptance receipt, isolated-smoke guard, and
  immutable-path health fixes are integrated at commit
  `282102bb971b3ef77d1b9b66448dfe8603734d6a` and landed through personal-ops PR #259 at merge
  commit `5a220a1c04f811a101690a70a11840f3048aeb2c`; the active commit is an ancestor of `main`, and their
  source trees match. Immutable release
  `579c2a313d39f07ed8eb11890762ebf13fd4253fb90cb9a86c34135ce1c35973` is current, source authority
  points to that clean commit, final activation receipt `7f666658-8ef7-4ad1-94ef-00cfd2411d86` reports
  `readback_verified=true`, and CLI, daemon, Codex MCP, Claude MCP, LaunchAgent, and desktop all read
  back the same release with no stale helpers.
- Pre-activation recovery snapshot `2026-07-14T09-21-28Z` captured schema v36 and passed SQLite
  integrity checks. Post-activation integrity remains `ok`; 56 application tables remain present;
  sampled history counts were preserved or increased; and the producer outbox contains 13 accepted
  events with no queued, rejected, or dead-lettered row.
- Test mode blocks port 9199, the generation LaunchAgent preserves only explicitly isolated test
  transport variables, and the verification harness uses an ephemeral loopback hub. The full
  personal-ops isolated suite passed 1188 tests; targeted delivery/generation/runtime tests passed;
  normal-checkout and metadata-free end-to-end smoke both passed; and GitHub CI passed on the exact
  activated commit. No test used a live destination.
- `personal-ops install check --json` is ready at 66 pass / 0 warn / 0 fail, and deep health is ready
  at 6 pass / 0 warn / 0 fail. The prior immutable-path false degradation is repaired.
- Activation produced deterministic startup event
  `personal-ops:daemon.started:17c93b9fc4f34b62118b45db`. The producer stored one matching
  `http:201:<event-id>` receipt, notification-hub processed it once, and no channel row exists because
  its explicit destination contract is log-only. A repeated historical attention event still reuses
  deterministic ID `personal-ops:operator.attention_item:2e9a32212f723163addcf78a`.
- Current live health is degraded by unresolved historical and recent delivery failures. Those rows
  remain retained and actionable; this rollout did not replay, acknowledge, disposition, or clear
  them merely to improve health.
- Runtime verification on 2026-07-14 exposed a destination-contract bypass: the deterministic
  `personal-ops:daemon.stopping:ecb9f7a87ebaee058744808f` event declared
  `required_destinations=["log"]`, but severity routing still attempted Slack and received provider
  acceptance. This is acceptance evidence only: no delivered or observed receipt exists. The source
  repair makes non-empty producer destination lists authoritative for external-channel eligibility;
  history is preserved and the accepted Slack row is not rewritten or dispositioned.
- The destination-contract repair was merged through PR #114 at notification-hub commit
  `4e44d1159ac1aca08514fd87ae11a10847ef1f12` after 511 isolated tests, Ruff, Pyright, CI, and CodeQL
  passed. The LaunchAgent was restarted from that clean `main`; runtime wiring is current, and the
  running pure explanation path reports `{log: true, push: false, slack: false}` for the exact
  normal+log-only daemon-stop shape. That pure explanation proof did not post an event.
- Pre-restart backups under
  `~/.local/share/notification-hub/backups/2026-07-14T09-34-23Z-required-destinations/` contain
  SQLite-backup-API copies of the inbox and producer outbox plus the exact LaunchAgent plist; both
  database copies passed integrity checks. Restart-time retention rotated 51 JSONL rows into
  `archive/events-20260714-093512-514698.jsonl`; current plus archive line counts reconcile to 2052,
  so the rotation preserved rather than discarded history.
- The restart created no channel acceptance. One legacy Codex row was Slack-rate-limited, and one
  legacy personal-ops row was initially quiet-hours buffered for push and Slack-rate-limited; neither
  attempt produced acceptance, delivery, or observation evidence.
- GitHub CLI briefly detached the authoritative personal-ops source worktree at the old default-branch
  base after PR #259 merged. The serialized installer reacted by activating immutable release
  `251646120f51900ed3b172654030530be00da984e53eabc38e8a708423690d0b` from stale commit `1648b91`.
  The worktree was restored to `282102b`, the already-verified `579c2a...` release was reactivated with
  six-surface readback, and no data restore or history rewrite was required.
- That final reactivation supplied live local proof of the repaired contract without a destination
  smoke: deterministic stop event `personal-ops:daemon.stopping:80b127e586c24e1c0d96c895` was
  accepted once by the hub and processed with no channel row; deterministic start event
  `personal-ops:daemon.started:285b8cac695bf70af72f1922` was accepted once and suppressed with no
  channel row. The producer outbox has matching `http:201:<event-id>` receipts for both.
- Historical channel rows keep their original generic `push_transport_failed` or
  `slack_transport_failed` evidence. Gate 1 does not rewrite history; future attempts persist bounded,
  secret-safe causes such as notifier timeout, HTTP class, network failure, or rate limiting.
- Gate 2 was explicitly approved on 2026-07-14. Before cutover, SQLite-backup-API copies of the inbox
  and BridgeDB plus the exact LaunchAgent plist were saved under
  `~/.local/share/notification-hub/backups/2026-07-14T10-45-20Z-gate2-cursor/`; both database copies
  pass `quick_check` when opened immutable. The cursor initialized at live BridgeDB maximum ID 5778
  with `consumed=0`, so no historical row was replayed and no channel count changed during bootstrap.
- The LaunchAgent now sets `NOTIFICATION_HUB_BRIDGE_CURSOR_ENABLED=1`. Runtime health reports
  `watcher_active=false`, `bridge_cursor_enabled=true`, and `bridge_cursor_active=true`; launchd
  confirms the flag and the cursor remains at the source maximum. The approved per-machine flag is
  recognized without weakening detection of any other LaunchAgent drift. That observability repair
  landed through PR #117 at merge commit `305e877` after 513 tests, Ruff, Pyright, CI, and CodeQL
  passed.
- Exactly one controlled local-push smoke was attempted. Deterministic event
  `notification-hub:live-smoke:gate2-20260714T104520Z` was persisted before transport with
  `max_attempts=1`, then terminally marked `processed` so it cannot retry. Its push row progressed
  once through `attempted` and `accepted` with receipt `terminal-notifier:exit:0`, then to `delivered`
  only after `terminal-notifier -list notification-hub` returned the matching event ID and delivery
  timestamp `2026-07-14 10:55:08 +0000`. The durable delivery receipt is
  `terminal-notifier:list:notification-hub:2026-07-14 10:55:08 +0000`; the matching JSONL audit row
  is retained. No Slack smoke was sent, and no second push attempt was made after the reporting-only
  script error.
- The smoke's channel state is `delivered`, not `observed`. Its observation receipt remains null
  until the operator explicitly confirms the visible notification; no inferred or log-only
  observation will be written.

The pathway is Gate 2 active: the durable Bridge cursor and one live local-push destination readback
are proven. Overall notification-hub health remains degraded by retained historical failures and retry
backlog. End-to-end operator observation remains unproven until the operator explicitly confirms the
visible smoke and the matching observation receipt is recorded.
