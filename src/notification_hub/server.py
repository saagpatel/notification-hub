"""FastAPI server — event intake, health check, bridge file watcher lifecycle."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, TypedDict, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse

from notification_hub.config import BRIDGE_FILE, get_policy_config
from notification_hub.diagnostics import collect_runtime_readiness
from notification_hub.models import Event, EventResponse
from notification_hub.operations import (
    ACTION_PROPOSAL_REVIEW_WINDOW_HOURS,
    action_review_package_path_for_name,
    delete_action_review_package,
    dismiss_action_proposal,
    dismiss_action_proposal_group,
    list_action_review_packages,
    list_operator_review_session_reports,
    list_personal_ops_import_queue,
    list_personal_ops_queue_burn_in_reports,
    load_action_review_package_detail,
    load_operator_review_session_report_detail,
    load_personal_ops_queue_burn_in_report_detail,
    prune_operator_review_session_reports,
    review_latest_noise_candidates,
    run_action_proposal_dismissal_list,
    run_coordination_console,
    run_coordination_readiness,
    run_inbox,
    run_operator_daily_state,
    run_operator_handoff_drill,
    run_operator_review_session,
    run_policy_check,
    run_personal_ops_action_export,
    run_personal_ops_import_queue_health_check,
    run_personal_ops_import_stub,
    run_personal_ops_outcome_sync_reminder,
    run_personal_ops_queue_review,
    run_retention,
    save_action_proposal_group_package,
    record_action_proposal_group_outcome,
    undismiss_action_proposal,
    update_personal_ops_import_queue_item,
    validate_action_package,
)
from notification_hub.pipeline import get_suppression_engine, process_event
from notification_hub.watcher import ObserverHandle, start_watcher

logger = logging.getLogger(__name__)

_REVIEW_GENERIC_ERROR = "operation failed; inspect local logs for details"
_REVIEW_SAFE_ERROR_MESSAGES = {
    "group_key is required",
    "invalid review package name",
    "missing status",
    "no review package has been saved in this server session",
    "package validation failed",
    "review package path missing",
}

_start_time: float = 0.0
_event_count: int = 0
_observer: ObserverHandle | None = None
_retention_task: asyncio.Task[None] | None = None
_latest_review_package_path: str | None = None
_latest_review_package_validation_status: str | None = None


class RetentionRuntimeStatus(TypedDict):
    enabled: bool
    interval_minutes: int
    max_events: int
    keep_archives: int
    last_checked_at: str | None
    last_status: str | None
    last_rotated: bool
    last_archive_path: str | None


_retention_status: RetentionRuntimeStatus = {
    "enabled": False,
    "interval_minutes": 0,
    "max_events": 0,
    "keep_archives": 0,
    "last_checked_at": None,
    "last_status": None,
    "last_rotated": False,
    "last_archive_path": None,
}


def reset_retention_runtime_state() -> None:
    global _retention_status
    _retention_status = {
        "enabled": False,
        "interval_minutes": 0,
        "max_events": 0,
        "keep_archives": 0,
        "last_checked_at": None,
        "last_status": None,
        "last_rotated": False,
        "last_archive_path": None,
    }


def get_retention_runtime_status() -> RetentionRuntimeStatus:
    return cast(RetentionRuntimeStatus, dict(_retention_status))


def reset_review_package_state() -> None:
    global _latest_review_package_path, _latest_review_package_validation_status
    _latest_review_package_path = None
    _latest_review_package_validation_status = None


def get_latest_review_package_path() -> str | None:
    return _latest_review_package_path


def _configure_retention_status() -> None:
    global _retention_status
    policy = get_policy_config().retention
    _retention_status = {
        "enabled": policy.enabled,
        "interval_minutes": policy.interval_minutes,
        "max_events": policy.max_events,
        "keep_archives": policy.keep_archives,
        "last_checked_at": None,
        "last_status": None,
        "last_rotated": False,
        "last_archive_path": None,
    }


def run_retention_check_once() -> None:
    global _retention_status
    policy = get_policy_config().retention
    _retention_status["enabled"] = policy.enabled
    _retention_status["interval_minutes"] = policy.interval_minutes
    _retention_status["max_events"] = policy.max_events
    _retention_status["keep_archives"] = policy.keep_archives

    report = run_retention(
        max_events=policy.max_events,
        keep_archives=policy.keep_archives,
    )
    checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _retention_status["last_checked_at"] = checked_at
    _retention_status["last_status"] = report["status"]
    _retention_status["last_rotated"] = report["rotated"]
    _retention_status["last_archive_path"] = report["archive_path"]


async def _retention_loop() -> None:
    while True:
        policy = get_policy_config().retention
        _retention_status["enabled"] = policy.enabled
        _retention_status["interval_minutes"] = policy.interval_minutes
        _retention_status["max_events"] = policy.max_events
        _retention_status["keep_archives"] = policy.keep_archives
        if policy.enabled:
            run_retention_check_once()
        await asyncio.sleep(policy.interval_minutes * 60)


def _handle_bridge_event(event: Event) -> None:
    """Callback for bridge file watcher — processes events through the full pipeline."""
    global _event_count
    process_event(event)
    _event_count += 1


def _review_package_state(validation_status: str | None = None) -> dict[str, object]:
    return {
        "path": _latest_review_package_path,
        "validation_status": validation_status or _latest_review_package_validation_status,
    }


def _review_operator_focus(
    *,
    runtime: dict[str, object],
    action_count: int,
    queue_health: dict[str, object],
    outcome_sync_reminder: dict[str, object],
) -> dict[str, object]:
    if runtime.get("status") != "ok":
        return {
            "status": "warn",
            "title": "Runtime needs attention",
            "next_action": runtime.get("next_action", "Run notification-hub verify-runtime."),
        }
    if queue_health.get("needs_review"):
        return {
            "status": "warn",
            "title": "Review queued handoffs",
            "next_action": queue_health.get(
                "next_action", "Review queued personal-ops handoff items."
            ),
        }
    if outcome_sync_reminder.get("should_remind"):
        return {
            "status": "warn",
            "title": "Resolve promoted outcomes",
            "next_action": outcome_sync_reminder.get(
                "next_action", "Resolve promoted personal-ops handoff outcomes."
            ),
        }
    if action_count > 0:
        return {
            "status": "ready",
            "title": "Action proposals available",
            "next_action": "Save and validate a review package before queueing handoffs.",
        }
    return {
        "status": "ok",
        "title": "No action needed",
        "next_action": "Queue loop is ready; wait for the next real operator signal.",
    }


async def _review_runtime_status() -> dict[str, object]:
    details = await _collect_health_details()
    config = cast(dict[str, object], details.get("config", {}))
    retention = cast(dict[str, object], details.get("retention", {}))
    delivery = cast(dict[str, object], details.get("delivery", {}))
    runtime_wiring = cast(dict[str, object], details.get("runtime_wiring", {}))
    slack_failures = 0
    return {
        "status": details.get("status", "degraded"),
        "health_url": "http://127.0.0.1:9199/health/details",
        "daemon_reachable": True,
        "watcher_active": details.get("watcher_active"),
        "events_processed": details.get("events_processed"),
        "uptime_seconds": details.get("uptime_seconds"),
        "policy_config_found": config.get("exists"),
        "policy_warning_count": config.get("warning_count", 0),
        "retention_enabled": retention.get("enabled"),
        "retention_last_status": retention.get("last_status"),
        "runtime_wiring_current": all(bool(value) for value in runtime_wiring.values()),
        "push_notifier_available": delivery.get("push_notifier_available"),
        "slack_configured": delivery.get("slack_webhook_configured"),
        "slack_delivery_failures": slack_failures,
        "next_action": "No action needed."
        if details.get("status") == "ok"
        else "Review health details.",
    }


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Start bridge watcher on startup, stop on shutdown."""
    global _start_time, _observer, _retention_task
    _start_time = time.monotonic()
    _configure_retention_status()

    if BRIDGE_FILE.parent.exists():
        _observer = start_watcher(_handle_bridge_event)
        logger.info("Bridge file watcher active")
    else:
        logger.warning("Bridge file directory not found, watcher disabled")

    _retention_task = asyncio.create_task(_retention_loop())

    yield

    retention_task = _retention_task
    assert retention_task is not None
    retention_task.cancel()
    try:
        await retention_task
    except asyncio.CancelledError:
        pass
    logger.info("Retention loop stopped")
    _retention_task = None

    if _observer is not None:
        _observer.stop()
        _observer.join(timeout=5)
        logger.info("Bridge file watcher stopped")
        _observer = None

    reset_retention_runtime_state()


app = FastAPI(
    title="Notification Hub",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


REVIEW_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>notification-hub review</title>
  <link rel="icon" href="data:,">
  <style>
    :root {
      color-scheme: light;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f7f8fa;
      color: #20242a;
    }
    body { margin: 0; }
    main { max-width: 1180px; margin: 0 auto; padding: 28px 20px 40px; }
    header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 24px; }
    h1 { font-size: 28px; line-height: 1.15; margin: 0 0 6px; font-weight: 700; }
    h2 { font-size: 16px; margin: 0 0 12px; color: #39414d; }
    p { margin: 0; color: #596272; }
    button {
      border: 1px solid #cfd6df;
      border-radius: 6px;
      background: #ffffff;
      color: #20242a;
      min-height: 36px;
      padding: 0 12px;
      cursor: pointer;
      font-weight: 600;
    }
    button:hover { border-color: #98a4b3; }
    .button-row { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
    .summary { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px; }
    .metric, section {
      background: #ffffff;
      border: 1px solid #dfe4ea;
      border-radius: 8px;
      box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04);
    }
    .metric { padding: 14px; min-height: 72px; }
    .metric span { display: block; color: #667085; font-size: 12px; text-transform: uppercase; letter-spacing: 0; }
    .metric strong { display: block; margin-top: 8px; font-size: 22px; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
    section { padding: 16px; min-width: 0; }
    ul { margin: 0; padding: 0; list-style: none; display: grid; gap: 8px; }
    li { border-top: 1px solid #eef1f4; padding-top: 8px; }
    li:first-child { border-top: 0; padding-top: 0; }
    .line { display: flex; justify-content: space-between; gap: 12px; align-items: baseline; }
    .title { font-weight: 650; overflow-wrap: anywhere; }
    .meta { color: #667085; font-size: 12px; white-space: nowrap; }
    .next { color: #475467; font-size: 13px; margin-top: 4px; overflow-wrap: anywhere; }
    .badge-row { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 6px; }
    .badge { border: 1px solid #d0d5dd; border-radius: 999px; color: #344054; font-size: 12px; padding: 2px 8px; }
    .badge.warn { border-color: #d89b25; color: #7a4b00; }
    .toolbar { align-items: center; display: flex; justify-content: space-between; gap: 8px; margin-bottom: 10px; }
    select { border: 1px solid #d0d5dd; border-radius: 6px; padding: 5px 8px; }
    .focus { border-color: #b9d6ef; margin-bottom: 14px; }
    .focus .title { font-size: 18px; }
    .focus .next { font-size: 14px; }
    .trust { background: #eef6f1; border-color: #badfc8; }
    .warn { background: #fff8eb; border-color: #efd49a; }
    .empty { color: #667085; font-style: italic; }
    @media (max-width: 860px) {
      header { display: block; }
      header button { margin-top: 14px; }
      .summary, .grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>notification-hub review</h1>
        <p>Local review surface for inbox rollups, personal-ops action proposals, and trust state.</p>
      </div>
      <div class="actions">
        <button id="savePackage" type="button">Save package</button>
        <button id="saveSession" type="button">Save session</button>
        <button id="validatePackage" type="button">Validate package</button>
        <button id="runDrill" type="button">Run drill + save proof</button>
        <button id="refresh" type="button">Refresh</button>
      </div>
    </header>
    <div class="summary" id="summary"></div>
    <section class="focus">
      <h2>Operator Focus</h2>
      <ul id="operatorFocus"></ul>
    </section>
    <section class="focus">
      <h2>Coordination Readiness</h2>
      <ul id="coordinationReadiness"></ul>
    </section>
    <section class="focus">
      <h2>Coordination Console</h2>
      <ul id="coordinationConsole"></ul>
    </section>
    <section class="focus">
      <h2>Real Signal Readiness</h2>
      <ul id="realSignalReadiness"></ul>
    </section>
    <section class="focus">
      <h2>Proposal Review</h2>
      <ul id="proposalReview"></ul>
    </section>
    <section class="focus">
      <h2>Operator Decision Required</h2>
      <ul id="operatorDecisionRequired"></ul>
    </section>
    <section class="focus">
      <h2>Noise Candidate Review</h2>
      <ul id="noiseCandidateReview"></ul>
    </section>
    <section class="focus">
      <h2>Next Signal</h2>
      <ul id="nextSignal"></ul>
    </section>
    <section class="focus">
      <h2>Policy Drift</h2>
      <ul id="policyDrift"></ul>
    </section>
    <div class="grid">
      <section>
        <h2>Action Proposals</h2>
        <ul id="actions"></ul>
      </section>
      <section>
        <h2>Inbox Rollups</h2>
        <ul id="rollups"></ul>
      </section>
      <section>
        <h2>Needs Attention</h2>
        <ul id="attention"></ul>
      </section>
      <section class="trust">
        <h2>Trust Boundary</h2>
        <ul id="trust"></ul>
      </section>
      <section>
        <h2>Dismissals</h2>
        <ul id="dismissals"></ul>
      </section>
      <section>
        <h2>Operator State</h2>
        <ul id="operatorState"></ul>
      </section>
      <section>
        <h2>Latest Review Session</h2>
        <ul id="latestReviewSession"></ul>
      </section>
      <section>
        <h2>Review Sessions</h2>
        <ul id="reviewSessions"></ul>
      </section>
      <section>
        <h2>Review Session Retention</h2>
        <ul id="reviewSessionRetention"></ul>
      </section>
      <section>
        <h2>Review Session Detail</h2>
        <ul id="reviewSessionDetail"></ul>
      </section>
      <section>
        <h2>Review Package</h2>
        <ul id="package"></ul>
      </section>
      <section>
        <h2>Recent Packages</h2>
        <ul id="packages"></ul>
      </section>
      <section>
        <h2>Package Detail</h2>
        <ul id="packageDetail"></ul>
      </section>
      <section>
        <h2>Burn-In Reports</h2>
        <ul id="burnInReports"></ul>
      </section>
      <section>
        <h2>Burn-In Detail</h2>
        <ul id="burnInDetail"></ul>
      </section>
      <section>
        <h2>Queue Review</h2>
        <ul id="queueReview"></ul>
      </section>
      <section>
        <div class="toolbar">
          <h2>Import Queue</h2>
          <select id="importQueueFilter" aria-label="Import queue filter">
            <option value="open">Open</option>
            <option value="pending">Pending outcome</option>
            <option value="stale">Stale outcome</option>
            <option value="queued">Queued</option>
            <option value="reviewed">Reviewed only</option>
            <option value="all">All</option>
            <option value="promoted">Promoted</option>
            <option value="resolved">Resolved</option>
          </select>
        </div>
        <ul id="importQueueHealth"></ul>
        <ul id="importQueue"></ul>
      </section>
    </div>
  </main>
  <script>
    const summary = document.getElementById("summary");
    const operatorFocus = document.getElementById("operatorFocus");
    const coordinationReadiness = document.getElementById("coordinationReadiness");
    const coordinationConsole = document.getElementById("coordinationConsole");
    const realSignalReadiness = document.getElementById("realSignalReadiness");
    const proposalReview = document.getElementById("proposalReview");
    const operatorDecisionRequired = document.getElementById("operatorDecisionRequired");
    const noiseCandidateReview = document.getElementById("noiseCandidateReview");
    const nextSignal = document.getElementById("nextSignal");
    const actions = document.getElementById("actions");
    const rollups = document.getElementById("rollups");
    const attention = document.getElementById("attention");
    const trust = document.getElementById("trust");
    const dismissals = document.getElementById("dismissals");
    const operatorState = document.getElementById("operatorState");
    const reviewSessions = document.getElementById("reviewSessions");
    const reviewSessionRetention = document.getElementById("reviewSessionRetention");
    const reviewSessionDetail = document.getElementById("reviewSessionDetail");
    const packageState = document.getElementById("package");
    const packages = document.getElementById("packages");
    const packageDetail = document.getElementById("packageDetail");
    const burnInReports = document.getElementById("burnInReports");
    const burnInDetail = document.getElementById("burnInDetail");
    const importQueueHealth = document.getElementById("importQueueHealth");
    const queueReview = document.getElementById("queueReview");
    const importQueue = document.getElementById("importQueue");
    const importQueueFilter = document.getElementById("importQueueFilter");
    const actionProposalReviewWindowHours = __ACTION_PROPOSAL_REVIEW_WINDOW_HOURS__;

    function item(html) {
      const li = document.createElement("li");
      li.innerHTML = html;
      return li;
    }
    function metric(label, value) {
      const div = document.createElement("div");
      div.className = "metric";
      div.innerHTML = `<span>${esc(label)}</span><strong>${esc(value)}</strong>`;
      return div;
    }
    function esc(value) {
      return String(value ?? "").replace(/[&<>"']/g, ch => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      }[ch]));
    }
    function empty(target, text) {
      target.replaceChildren(item(`<span class="empty">${text}</span>`));
    }
    function badge(value) {
      return value ? `<span class="badge">${esc(value)}</span>` : "";
    }
    function warnBadge(value, warning) {
      return value ? `<span class="badge${warning ? " warn" : ""}">${esc(value)}</span>` : "";
    }
    function ageLabel(timestamp) {
      const parsed = Date.parse(timestamp || "");
      if (Number.isNaN(parsed)) {
        return "unknown age";
      }
      const minutes = Math.max(0, Math.round((Date.now() - parsed) / 60000));
      if (minutes < 60) {
        return `${minutes}m`;
      }
      const hours = Math.round(minutes / 60);
      if (hours < 48) {
        return `${hours}h`;
      }
      return `${Math.round(hours / 24)}d`;
    }
    function durationLabel(seconds) {
      const value = Number(seconds);
      if (!Number.isFinite(value)) {
        return "unknown";
      }
      const minutes = Math.max(0, Math.round(value / 60));
      if (minutes < 60) {
        return `${minutes}m`;
      }
      const hours = Math.round(minutes / 60);
      if (hours < 48) {
        return `${hours}h`;
      }
      return `${Math.round(hours / 24)}d`;
    }
    function olderThanDays(timestamp, days) {
      const parsed = Date.parse(timestamp || "");
      if (Number.isNaN(parsed)) {
        return true;
      }
      return Date.now() - parsed > days * 24 * 60 * 60 * 1000;
    }
    function renderList(target, rows, render, emptyText) {
      if (!rows || rows.length === 0) {
        empty(target, emptyText);
        return;
      }
      target.replaceChildren(...rows.map(render));
    }
    function outcomeGuardrail(outcomeQuality) {
      const rich = outcomeQuality.rich || {};
      if ((rich.resolved ?? 0) === 0) {
        return "No resolved rich-evidence handoff outcomes yet; keep expansion operator-mediated until the first real rich handoff is promoted and resolved.";
      }
      return outcomeQuality.next_action || "Rich-evidence outcomes are now represented in the trend.";
    }
    function firstRichHandoffChecklist(outcomeQuality) {
      const rich = outcomeQuality.rich || {};
      if ((rich.resolved ?? 0) > 0) {
        return [
          "Continue comparing rich and thin outcomes before widening automation.",
          "Keep recording promotion outcome ids and final operator decisions."
        ];
      }
      return [
        "Save one rich-evidence review package from a real active proposal.",
        "Queue exactly one handoff and inspect its evidence context.",
        "Promote externally through personal-ops, then record the suggestion id.",
        "Record the final accepted, rejected, or ignored outcome.",
        "Rerun queue health and burn-in before expanding authority."
      ];
    }
    function proofTrendLabel(reports) {
      if (!reports || reports.length === 0) {
        return "No saved proof yet.";
      }
      const latest = reports[0] || {};
      const previous = reports[1] || {};
      if (!previous.name) {
        return `Latest proof is ${latest.ready_for_live_promotion ? "ready" : "not ready"} with ${latest.noise_candidate_count ?? 0} noise candidate(s).`;
      }
      const noiseDelta = (latest.noise_candidate_count ?? 0) - (previous.noise_candidate_count ?? 0);
      const readyTrend = latest.ready_for_live_promotion === previous.ready_for_live_promotion
        ? "readiness unchanged"
        : latest.ready_for_live_promotion
          ? "readiness improved"
          : "readiness regressed";
      const noiseTrend = noiseDelta === 0
        ? "noise unchanged"
        : noiseDelta < 0
          ? `noise down ${Math.abs(noiseDelta)}`
          : `noise up ${noiseDelta}`;
      return `${readyTrend}; ${noiseTrend} versus previous proof.`;
    }
    function readinessExplanation(readiness) {
      const blockers = [];
      if ((readiness.runtime_status || "unknown") !== "ok") {
        blockers.push(`runtime is ${readiness.runtime_status || "unknown"}`);
      }
      if ((readiness.policy_warning_count ?? 0) > 0) {
        blockers.push(`${readiness.policy_warning_count} policy warning(s)`);
      }
      if ((readiness.queued_count ?? 0) > 0) {
        blockers.push(`${readiness.queued_count} queued handoff(s)`);
      }
      if ((readiness.pending_count ?? 0) > 0) {
        blockers.push(`${readiness.pending_count} pending promoted outcome(s)`);
      }
      if ((readiness.stale_count ?? 0) > 0) {
        blockers.push(`${readiness.stale_count} stale promoted outcome(s)`);
      }
      if ((readiness.latest_burn_in_noise_candidates ?? 0) > 0) {
        blockers.push(`${readiness.latest_burn_in_noise_candidates} burn-in noise candidate(s)`);
      }
      if ((readiness.saved_burn_in_reports ?? 0) === 0) {
        blockers.push("no saved burn-in proof");
      }
      if (readiness.latest_burn_in_ready === false) {
        blockers.push("latest burn-in proof is not ready");
      }
      if (blockers.length > 0) {
        return `Blocked by ${blockers.join(", ")}.`;
      }
      if (readiness.decision === "ready_to_expand") {
        return "Ready because runtime, policy, queue, and saved burn-in proof are clear.";
      }
      return readiness.summary || "Readiness is still being evaluated.";
    }
    function renderRealSignalReadiness(data) {
      const readiness = data.readiness || {};
      const queue = data.queue_health || {};
      const signal = data.next_signal || {};
      const review = data.proposal_review || {};
      const outcomeQuality = data.outcome_quality || {};
      const latestProof = (data.burn_in_reports || [])[0] || {};
      const active = data.active_action_count ?? 0;
      const richFollowUp = review.rich_follow_up_review_count ?? 0;
      const queued = queue.queued_count ?? 0;
      const pending = queue.promoted_pending_count ?? 0;
      const stale = queue.promoted_pending_stale_count ?? 0;
      const readyForLive = Boolean(latestProof.ready_for_live_promotion);
      const latestProofTimestamp = latestProof.generated_at || latestProof.modified_at;
      const latestProofAge = latestProof.name ? ageLabel(latestProofTimestamp) : "none";
      const latestProofStale = latestProof.name ? olderThanDays(latestProofTimestamp, 7) : true;
      const liveRuntimeStatus = readiness.runtime_status || data.runtime_status || "unknown";
      const status = active > 0 || richFollowUp > 0 || queued > 0 || pending > 0 || stale > 0
        ? "action"
        : readiness.decision === "ready_to_expand" && readyForLive && !latestProofStale
          ? "ready"
          : "watch";
      const nextCommand = (data.next_commands || [])[0]
        || (signal.next_commands || [])[0]
        || "uv run notification-hub coordination-console";
      realSignalReadiness.replaceChildren(item(`
        <div class="line"><span class="title">${esc(signal.title || "Waiting for next real signal")}</span><span class="meta">${esc(status)}</span></div>
        <div class="badge-row">
          ${warnBadge(`active ${active}`, active > 0)}
          ${warnBadge(`rich follow-up ${richFollowUp}`, richFollowUp > 0)}
          ${badge(`handled ${data.handled_action_count ?? 0}`)}
          ${warnBadge(`queued ${queued}`, queued > 0)}
          ${warnBadge(`pending ${pending}`, pending > 0)}
          ${warnBadge(`stale ${stale}`, stale > 0)}
          ${badge(`live runtime ${liveRuntimeStatus}`)}
          ${badge(`saved proof ${latestProof.status || "none"}`)}
          ${warnBadge(`proof age ${latestProofAge}`, latestProofStale)}
          ${warnBadge(`rich resolved ${(outcomeQuality.rich || {}).resolved ?? 0}`, ((outcomeQuality.rich || {}).resolved ?? 0) === 0)}
        </div>
        <div class="next"><strong>Live runtime</strong>: ${esc(liveRuntimeStatus)}; readiness decision ${esc(readiness.decision || "unknown")}.</div>
        <div class="next"><strong>Saved proof</strong>: ${esc(latestProof.name || "No saved proof")} (${esc(latestProof.ready_for_live_promotion ? "ready" : "not ready")}, age ${esc(latestProofAge)}, noise ${esc(latestProof.noise_candidate_count ?? 0)})</div>
        <div class="next"><strong>Handled follow-ups</strong>: ${esc(review.handled_history_summary || "No handled follow-up history.")}</div>
        <div class="next"><strong>Guardrail</strong>: ${esc(outcomeGuardrail(outcomeQuality))}</div>
        <div class="next"><strong>First rich handoff checklist</strong>: ${esc(firstRichHandoffChecklist(outcomeQuality).join(" -> "))}</div>
        <div class="next"><strong>Next command</strong>: ${esc(nextCommand)}</div>
        <div class="next">${esc(data.next_action || signal.next_action || "")}</div>
      `));
    }
    async function load() {
      const res = await fetch("/review/data?hours=2&limit=6");
      const data = await res.json();
      summary.replaceChildren(
        metric("Runtime", data.runtime.status),
        metric("Uptime", durationLabel(data.runtime.uptime_seconds)),
        metric("Events", data.inbox.events_seen),
        metric("Actions", data.actions.actions.length),
        metric("Rollups", data.inbox.rollups.length),
        metric("Applied", data.trust.applied ? "yes" : "no")
      );
      const focus = data.operator_focus || {};
      operatorFocus.replaceChildren(item(`
        <div class="line"><span class="title">${esc(focus.title || "Operator focus")}</span><span class="meta">${esc(focus.status || "unknown")}</span></div>
        <div class="badge-row">
          ${warnBadge(`queued ${focus.queued_count ?? 0}`, (focus.queued_count ?? 0) > 0)}
          ${warnBadge(`pending ${focus.pending_count ?? 0}`, (focus.pending_count ?? 0) > 0)}
          ${warnBadge(`stale ${focus.stale_count ?? 0}`, (focus.stale_count ?? 0) > 0)}
          ${badge(`actions ${focus.action_count ?? 0}`)}
        </div>
        <div class="next">${esc(focus.next_action || "")}</div>
      `));
      const readiness = data.coordination_readiness || {};
      coordinationReadiness.replaceChildren(item(`
        <div class="line"><span class="title">${esc(readiness.decision || "unknown")}</span><span class="meta">${esc(readiness.status || "unknown")}</span></div>
        <div class="badge-row">
          ${badge(`runtime ${readiness.runtime_status || "unknown"}`)}
          ${warnBadge(`policy ${readiness.policy_warning_count ?? 0}`, (readiness.policy_warning_count ?? 0) > 0)}
          ${warnBadge(`queued ${readiness.queued_count ?? 0}`, (readiness.queued_count ?? 0) > 0)}
          ${warnBadge(`pending ${readiness.pending_count ?? 0}`, (readiness.pending_count ?? 0) > 0)}
          ${warnBadge(`stale ${readiness.stale_count ?? 0}`, (readiness.stale_count ?? 0) > 0)}
          ${warnBadge(`noise ${readiness.latest_burn_in_noise_candidates ?? 0}`, (readiness.latest_burn_in_noise_candidates ?? 0) > 0)}
          ${badge(`reports ${readiness.saved_burn_in_reports ?? 0}`)}
        </div>
        <div class="next"><strong>Readiness explanation</strong>: ${esc(readinessExplanation(readiness))}</div>
        <div class="next">${esc(readiness.summary || "")}</div>
        <div class="next">${esc(readiness.next_action || "")}</div>
      `));
      renderList(actions, data.actions.actions, a => item(`
        <div class="line"><span class="title">${esc(a.title)}</span><span class="meta">${esc(a.priority)}/${esc(a.state)} x${esc(a.count)}</span></div>
        <div class="next">${esc(a.suggested_next_action)}</div>
        <div class="button-row">
          <button type="button" data-dismissal-key="${esc(a.dismissal_key)}">Dismiss</button>
        </div>
      `), "No action proposals.");
      actions.querySelectorAll("button[data-dismissal-key]").forEach(button => {
        button.addEventListener("click", () => dismissActionProposal(button.dataset.dismissalKey));
      });
      renderList(rollups, data.inbox.rollups, r => item(`
        <div class="line"><span class="title">${esc(r.title)}</span><span class="meta">${esc(r.intent)} x${esc(r.count)}</span></div>
        <div class="next">${esc(r.source)}${r.project ? " / " + esc(r.project) : ""}</div>
      `), "No repeated rollups.");
      renderList(attention, data.inbox.needs_attention, a => item(`
        <div class="line"><span class="title">${esc(a.title)}</span><span class="meta">${esc(a.intent)}</span></div>
        <div class="next">${esc(a.source)}${a.project ? " / " + esc(a.project) : ""}</div>
      `), "No attention items.");
      trust.replaceChildren(
        item(`<div class="line"><span class="title">Validated</span><span class="meta">${data.trust.validated ? "yes" : "not yet"}</span></div>`),
        item(`<div class="line"><span class="title">Imported</span><span class="meta">${data.trust.imported ? "yes" : "no"}</span></div>`),
        item(`<div class="line"><span class="title">Applied</span><span class="meta">${data.trust.applied ? "yes" : "no"}</span></div>`),
        item(`<div class="next">${esc(data.trust.next_action)}</div>`)
      );
      renderPackage(data.review_package);
      await loadPackages();
      await loadBurnInReports();
      await loadQueueReview();
      await loadImportQueue();
      await loadCoordinationConsole();
      await loadNoiseCandidateReview();
      await loadPolicyDrift();
      await loadDismissals();
      await loadOperatorDailyState();
      await loadReviewSessionReports();
      await loadReviewSessionRetention();
    }
    async function loadCoordinationConsole() {
      const res = await fetch(`/review/coordination-console?hours=${actionProposalReviewWindowHours}&limit=25`);
      const data = await res.json();
      const readiness = data.readiness || {};
      const queue = data.queue_health || {};
      const reminder = data.outcome_sync_reminder || {};
      const signal = data.next_signal || {};
      const outcomeQuality = data.outcome_quality || {};
      const review = data.proposal_review || {};
      const richFollowUp = review.rich_follow_up_review_count ?? 0;
      const guideSteps = data.guide_steps || [];
      const guideRows = guideSteps.slice(0, 4).map(step => item(`
        <div class="line"><span class="title">${esc(step.step)}. ${esc(step.title)}</span><span class="meta">${esc(step.status)}</span></div>
        <div class="next">${esc(step.summary || "")}</div>
        ${(step.commands || []).slice(0, 2).map(command => `<div class="next"><strong>Command</strong>: ${esc(command)}</div>`).join("")}
      `));
      coordinationConsole.replaceChildren(item(`
        <div class="line"><span class="title">${esc(readiness.decision || "unknown")}</span><span class="meta">${esc(data.status || "unknown")}</span></div>
        <div class="badge-row">
          ${badge(`actions ${data.action_count ?? 0}`)}
          ${warnBadge(`active ${data.active_action_count ?? 0}`, (data.active_action_count ?? 0) > 0)}
          ${warnBadge(`rich follow-up ${richFollowUp}`, richFollowUp > 0)}
          ${badge(`handled ${data.handled_action_count ?? 0}`)}
          ${badge(`dismissed ${data.dismissal_count ?? 0}`)}
          ${warnBadge(`queued ${queue.queued_count ?? 0}`, (queue.queued_count ?? 0) > 0)}
          ${warnBadge(`pending ${queue.promoted_pending_count ?? 0}`, (queue.promoted_pending_count ?? 0) > 0)}
          ${warnBadge(`stale ${queue.promoted_pending_stale_count ?? 0}`, (queue.promoted_pending_stale_count ?? 0) > 0)}
          ${warnBadge(`reminders ${reminder.pending_count ?? 0}`, (reminder.pending_count ?? 0) > 0)}
          ${badge(`reports ${(data.burn_in_reports || []).length}`)}
          ${badge(signal.watch_posture || "monitor")}
        </div>
        <div class="next"><strong>Guide</strong>: ${esc(data.guide_stage || "unknown")}</div>
        <div class="next"><strong>Outcome quality</strong>: ${esc(outcomeQuality.summary || "No promoted handoff outcomes are recorded yet.")}</div>
        ${signal.quiet_reason ? `<div class="next"><strong>Quiet reason</strong>: ${esc(signal.quiet_reason)}</div>` : ""}
        <div class="next">${esc(data.next_action || "")}</div>
      `), ...(guideRows.length ? guideRows : [item(`<div class="next">No guide steps.</div>`)]));
      renderRealSignalReadiness(data);
      const reviewGroups = review.groups || [];
      const groupRows = reviewGroups.slice(0, 5).map(group => item(`
        <div class="line"><span class="title">${esc(group.source)}${group.project ? " / " + esc(group.project) : ""}</span><span class="meta">${esc(group.intent)} x${esc(group.action_count)}</span></div>
        <div class="badge-row">
          ${badge(group.priority)}
          ${badge(group.state)}
          ${badge(`events ${group.total_event_count ?? 0}`)}
          ${warnBadge(`rich ${group.rich_evidence_count ?? 0}`, (group.rich_evidence_count ?? 0) > 0)}
          ${warnBadge(`thin ${group.thin_evidence_count ?? 0}`, (group.thin_evidence_count ?? 0) > 0)}
          ${badge(`titles ${(group.titles || []).length}`)}
          ${badge(`history ${group.history_count ?? 0}`)}
          ${badge(group.promotion_readiness || "review_required")}
          ${group.routing_recommendation ? badge(group.routing_recommendation.decision) : ""}
        </div>
        <div class="next">${esc((group.titles || []).join(", "))}</div>
        <div class="next"><strong>Promotion readiness</strong>: ${esc(group.promotion_readiness_summary || "")}</div>
        ${group.latest_history ? `<div class="next"><strong>Last group action</strong>: ${esc(group.latest_history.event_type)} (${esc(group.latest_history.status)})</div>` : ""}
        ${group.routing_recommendation ? `<div class="next"><strong>Route</strong>: ${esc(group.routing_recommendation.reason)}</div>` : ""}
        ${group.routing_recommendation ? `<div class="next">Promote ${esc(group.routing_recommendation.promote_candidate_count)} / suppress ${esc(group.routing_recommendation.suppress_candidate_count)} / follow up ${esc(group.routing_recommendation.follow_up_candidate_count)}</div>` : ""}
        <div class="next">${esc(group.next_action || "")}</div>
        <div class="button-row">
          <button type="button" data-save-group="${esc(group.group_key)}">Save group</button>
          <button type="button" data-queue-group="${esc(group.group_key)}">Queue group</button>
          ${group.routing_recommendation && group.routing_recommendation.promote_candidate_count ? `<button type="button" data-save-route="promote" data-route-group="${esc(group.group_key)}">Save promote</button>` : ""}
          ${group.routing_recommendation && group.routing_recommendation.promote_candidate_count ? `<button type="button" data-queue-route="promote" data-route-group="${esc(group.group_key)}">Queue promote</button>` : ""}
          ${group.routing_recommendation && group.routing_recommendation.suppress_candidate_count ? `<button type="button" data-dismiss-route="suppress" data-route-group="${esc(group.group_key)}">Dismiss suppress</button>` : ""}
          <button type="button" data-follow-up-group="${esc(group.group_key)}">Needs follow-up</button>
          <button type="button" data-dismiss-group="${esc(group.group_key)}">Dismiss group</button>
        </div>
      `));
      proposalReview.replaceChildren(item(`
        <div class="line"><span class="title">${esc(review.mode || "unknown")}</span><span class="meta">${esc(review.group_count ?? 0)} group(s)</span></div>
        <div class="badge-row">
          ${warnBadge(`new ${review.new_count ?? 0}`, (review.new_count ?? 0) > 0)}
          ${warnBadge(`queued ${review.queued_count ?? 0}`, (review.queued_count ?? 0) > 0)}
          ${warnBadge(`promoted ${review.promoted_count ?? 0}`, (review.promoted_count ?? 0) > 0)}
          ${badge(`reviewed-only ${review.reviewed_only_count ?? 0}`)}
          ${badge(`follow-up ${review.follow_up_count ?? 0}`)}
          ${badge(`resolved ${review.resolved_count ?? 0}`)}
          ${badge(`closed ${review.ignored_count ?? 0}`)}
          ${badge(`handled ${review.handled_count ?? 0}`)}
          ${badge(`mail ${review.handled_mail_count ?? 0}`)}
          ${warnBadge(`rich follow-up ${review.rich_follow_up_review_count ?? 0}`, (review.rich_follow_up_review_count ?? 0) > 0)}
          ${badge(`stable key ${review.handled_stable_key_match_count ?? 0}`)}
          ${badge(`rotated ${review.handled_evidence_rotation_count ?? 0}`)}
        </div>
        <div class="next">${esc(review.summary || "")}</div>
        ${review.handled_history_summary ? `<div class="next">${esc(review.handled_history_summary)}</div>` : ""}
        ${(review.handled_evidence_rotation_count ?? 0) > 0
          ? `<div class="next"><strong>Handled history</strong>: ${esc(review.handled_evidence_rotation_count)} newer evidence event(s) are still covered by stable proposal keys.</div>`
          : (data.handled_actions || []).slice(0, 3).map(entry => `<div class="next"><strong>Handled</strong>: ${esc(entry.action?.title || "proposal")} - ${esc(entry.lineage_reason || "")}</div>`).join("")}
        <div class="next">${esc(review.next_action || "")}</div>
        ${(review.group_history || []).slice(0, 3).map(entry => `<div class="next"><strong>Recent group action</strong>: ${esc(entry.event_type)} ${esc(entry.group_key)} (${esc(entry.status)})</div>`).join("")}
      `), ...(groupRows.length ? groupRows : [item(`<div class="next">No active proposal groups.</div>`)]));
      const decisionGroups = reviewGroups.filter(group => {
        const route = group.routing_recommendation || {};
        return (route.operator_decision_required_count ?? 0) > 0;
      });
      const decisionRows = decisionGroups.slice(0, 5).map(group => {
        const route = group.routing_recommendation || {};
        return item(`
          <div class="line"><span class="title">${esc(group.source)}${group.project ? " / " + esc(group.project) : ""}</span><span class="meta">${esc(route.operator_decision_required_count ?? 0)} decision(s)</span></div>
          <div class="badge-row">
            ${warnBadge(`approval ${route.operator_decision_required_count ?? 0}`, (route.operator_decision_required_count ?? 0) > 0)}
            ${badge(`follow up ${route.follow_up_candidate_count ?? 0}`)}
            ${badge(`suppress ${route.suppress_candidate_count ?? 0}`)}
          </div>
          <div class="next">${esc(route.reason || group.next_action || "")}</div>
          <div class="next">${esc(route.suggested_next_action || "")}</div>
          <div class="button-row">
            <button type="button" data-save-route="operator_decision" data-route-group="${esc(group.group_key)}">Save approval lane</button>
            <button type="button" data-follow-up-group="${esc(group.group_key)}">Needs follow-up</button>
          </div>
        `);
      });
      operatorDecisionRequired.replaceChildren(
        ...(decisionRows.length
          ? decisionRows
          : [item(`<div class="next">No outbound operator decisions are waiting.</div>`)])
      );
      proposalReview.querySelectorAll("button[data-save-group]").forEach(button => {
        button.addEventListener("click", () => saveProposalGroup(button.dataset.saveGroup));
      });
      operatorDecisionRequired.querySelectorAll("button[data-save-route]").forEach(button => {
        button.addEventListener("click", () => saveProposalGroup(button.dataset.routeGroup, button.dataset.saveRoute));
      });
      operatorDecisionRequired.querySelectorAll("button[data-follow-up-group]").forEach(button => {
        button.addEventListener("click", () => outcomeProposalGroup(button.dataset.followUpGroup, "needs_follow_up"));
      });
      proposalReview.querySelectorAll("button[data-queue-group]").forEach(button => {
        button.addEventListener("click", () => queueProposalGroup(button.dataset.queueGroup));
      });
      proposalReview.querySelectorAll("button[data-save-route]").forEach(button => {
        button.addEventListener("click", () => saveProposalGroup(button.dataset.routeGroup, button.dataset.saveRoute));
      });
      proposalReview.querySelectorAll("button[data-queue-route]").forEach(button => {
        button.addEventListener("click", () => queueProposalGroup(button.dataset.routeGroup, button.dataset.queueRoute));
      });
      proposalReview.querySelectorAll("button[data-dismiss-route]").forEach(button => {
        button.addEventListener("click", () => dismissProposalGroup(button.dataset.routeGroup, button.dataset.dismissRoute));
      });
      proposalReview.querySelectorAll("button[data-follow-up-group]").forEach(button => {
        button.addEventListener("click", () => outcomeProposalGroup(button.dataset.followUpGroup, "needs_follow_up"));
      });
      proposalReview.querySelectorAll("button[data-dismiss-group]").forEach(button => {
        button.addEventListener("click", () => dismissProposalGroup(button.dataset.dismissGroup));
      });
      nextSignal.replaceChildren(item(`
        <div class="line"><span class="title">${esc(signal.title || "Next signal")}</span><span class="meta">${esc(signal.status || "unknown")}</span></div>
        <div class="badge-row">
          ${badge(`hidden ${signal.hidden_action_count ?? 0}`)}
          ${badge(`dismissed ${signal.dismissed_count ?? 0}`)}
          ${badge(`policy-covered ${signal.policy_covered_repeated_count ?? 0}`)}
        </div>
        <div class="next">${esc(signal.summary || "")}</div>
        <div class="next">${esc(signal.next_action || "")}</div>
      `));
    }
    async function loadNoiseCandidateReview() {
      const res = await fetch("/review/noise-candidates?limit=6");
      const data = await res.json();
      const candidates = data.candidates || [];
      const rows = candidates.slice(0, 6).map(candidate => item(`
        <div class="line"><span class="title">${esc(candidate.title || "Untitled")}</span><span class="meta">${esc(candidate.decision_hint || "inspect")}</span></div>
        <div class="badge-row">
          ${badge(candidate.source)}
          ${candidate.project ? badge(candidate.project) : ""}
          ${badge(candidate.level)}
          ${warnBadge(`x${candidate.count ?? 0}`, (candidate.count ?? 0) > 1)}
        </div>
        <div class="next">${esc(candidate.body || "")}</div>
        ${candidate.suggested_rule ? `<div class="next"><strong>Candidate rule</strong>: ${esc(candidate.suggested_rule)}</div>` : ""}
      `));
      noiseCandidateReview.replaceChildren(item(`
        <div class="line"><span class="title">${esc(data.report_name || "Latest burn-in")}</span><span class="meta">${esc(data.status || "unknown")}</span></div>
        <div class="badge-row">
          ${warnBadge(`noise ${data.noise_candidate_count ?? 0}`, (data.noise_candidate_count ?? 0) > 0)}
        </div>
        <div class="next">${esc(data.next_action || data.error || "")}</div>
      `), ...(rows.length ? rows : [item(`<div class="next">No noise candidates need review.</div>`)]));
    }
    async function postProposalGroup(path, groupKey, reason, route) {
      if (!groupKey) {
        return null;
      }
      const res = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          group_key: groupKey,
          hours: actionProposalReviewWindowHours,
          limit: 25,
          reason,
          route
        })
      });
      return res.json();
    }
    async function saveProposalGroup(groupKey, route) {
      const data = await postProposalGroup("/review/action-proposal-group/package", groupKey, undefined, route);
      if (!data) {
        return;
      }
      packageDetail.replaceChildren(
        item(`<div class="line"><span class="title">Save group${route ? " / " + esc(route) : ""}</span><span class="meta">${esc(data.status)}</span></div>`),
        item(`<div class="next">Actions: ${esc(data.action_count ?? 0)}</div>`),
        item(`<div class="next">History: ${esc((data.group_history || {}).event_type || "not recorded")}</div>`),
        item(`<div class="next">${esc(data.next_action || data.error || "")}</div>`)
      );
      await loadPackages();
      await loadCoordinationConsole();
    }
    async function queueProposalGroup(groupKey, route) {
      if (!window.confirm("Queue this proposal group for operator review?")) {
        return;
      }
      const data = await postProposalGroup("/review/action-proposal-group/queue", groupKey, undefined, route);
      if (!data) {
        return;
      }
      const importResult = data.import_result || {};
      packageDetail.replaceChildren(
        item(`<div class="line"><span class="title">Queue group${route ? " / " + esc(route) : ""}</span><span class="meta">${esc(data.status)}</span></div>`),
        item(`<div class="next">Actions: ${esc(data.action_count ?? 0)} / queued: ${esc(importResult.queued_count ?? 0)}</div>`),
        item(`<div class="next">History: ${esc((data.group_history || {}).event_type || "not recorded")}</div>`),
        item(`<div class="next">${esc(data.next_action || data.error || "")}</div>`)
      );
      await loadPackages();
      await loadImportQueue();
      await loadCoordinationConsole();
    }
    async function dismissProposalGroup(groupKey, route) {
      if (!window.confirm("Dismiss this proposal group from the local console?")) {
        return;
      }
      const data = await postProposalGroup(
        "/review/action-proposal-group/dismiss",
        groupKey,
        route === "suppress"
          ? "Review UI dismissed the suppress route as already-covered mail workflow chatter."
          : "Review UI dismissed this grouped proposal as known noise.",
        route
      );
      if (!data) {
        return;
      }
      packageDetail.replaceChildren(
        item(`<div class="line"><span class="title">Dismiss group${route ? " / " + esc(route) : ""}</span><span class="meta">${esc(data.status)}</span></div>`),
        item(`<div class="next">Dismissed: ${esc(data.dismissed_count ?? 0)}</div>`),
        item(`<div class="next">History: ${esc((data.group_history || {}).event_type || "not recorded")}</div>`),
        item(`<div class="next">${esc(data.next_action || data.error || "")}</div>`)
      );
      await loadDismissals();
      await loadCoordinationConsole();
    }
    async function outcomeProposalGroup(groupKey, outcome) {
      if (!window.confirm("Record this proposal group outcome locally?")) {
        return;
      }
      const data = await fetch("/review/action-proposal-group/outcome", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          group_key: groupKey,
          outcome,
          hours: actionProposalReviewWindowHours,
          limit: 25,
          reason: "Review UI marked this grouped proposal for follow-up."
        })
      }).then(res => res.json());
      packageDetail.replaceChildren(
        item(`<div class="line"><span class="title">Group outcome</span><span class="meta">${esc(data.status)}</span></div>`),
        item(`<div class="next">Outcome: ${esc(data.outcome || "not recorded")}</div>`),
        item(`<div class="next">${esc(data.next_action || data.error || "")}</div>`)
      );
      await loadCoordinationConsole();
    }
    async function dismissActionProposal(dismissalKey) {
      if (!dismissalKey) {
        return;
      }
      const res = await fetch(`/review/action-proposal/${encodeURIComponent(dismissalKey)}/dismiss`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "Review UI dismissed repeated proposal as known noise." })
      });
      const data = await res.json();
      packageDetail.replaceChildren(
        item(`<div class="line"><span class="title">Dismiss proposal</span><span class="meta">${esc(data.status)}</span></div>`),
        item(`<div class="next">${esc(data.next_action || data.error || "")}</div>`)
      );
      await load();
    }
    async function loadPolicyDrift() {
      const res = await fetch("/review/policy-check");
      const data = await res.json();
      const drift = data.policy_drift || {};
      const missing = drift.missing_sample_noise_rules || [];
      policyDrift.replaceChildren(item(`
        <div class="line"><span class="title">${esc(drift.status || "unknown")}</span><span class="meta">${esc(data.status || "unknown")}</span></div>
        <div class="badge-row">
          ${badge(`live ${drift.live_noise_rule_count ?? 0}`)}
          ${badge(`sample ${drift.sample_noise_rule_count ?? 0}`)}
          ${warnBadge(`missing ${drift.missing_sample_noise_rule_count ?? 0}`, (drift.missing_sample_noise_rule_count ?? 0) > 0)}
          ${badge(`extra ${drift.extra_live_noise_rule_count ?? 0}`)}
        </div>
        <div class="next">${esc(drift.next_action || "")}</div>
        ${missing.slice(0, 3).map(rule => `<div class="next"><strong>Missing</strong>: ${esc(JSON.stringify(rule))}</div>`).join("")}
      `));
    }
    async function loadDismissals() {
      const res = await fetch("/review/action-proposal-dismissals?limit=10");
      const data = await res.json();
      renderList(dismissals, data.dismissals, d => item(`
        <div class="line"><span class="title">${esc(d.title || d.dismissal_key)}</span><span class="meta">${d.active ? "active" : "inactive"}</span></div>
        <div class="next">${esc(d.reason || "")}</div>
        <div class="next">${esc(d.dismissal_key)}</div>
        <div class="button-row">
          <button type="button" data-undismiss-key="${esc(d.dismissal_key)}">Undismiss</button>
        </div>
      `), "No active dismissals.");
      dismissals.querySelectorAll("button[data-undismiss-key]").forEach(button => {
        button.addEventListener("click", () => undismissActionProposal(button.dataset.undismissKey));
      });
    }
    async function undismissActionProposal(dismissalKey) {
      if (!dismissalKey) {
        return;
      }
      const res = await fetch(`/review/action-proposal/${encodeURIComponent(dismissalKey)}/undismiss`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "Review UI reactivated this proposal." })
      });
      const data = await res.json();
      packageDetail.replaceChildren(
        item(`<div class="line"><span class="title">Undismiss proposal</span><span class="meta">${esc(data.status)}</span></div>`),
        item(`<div class="next">${esc(data.next_action || data.error || "")}</div>`)
      );
      await load();
    }
    async function loadOperatorDailyState() {
      const res = await fetch("/review/operator-daily-state?hours=24&limit=5");
      const data = await res.json();
      const sessionRes = await fetch("/review/operator-review-session?hours=2&limit=10");
      const session = await sessionRes.json();
      renderOperatorState(data, session);
    }
    function renderOperatorState(data, session) {
      const queue = (data.queue_health || {}).health || {};
      const signal = ((data.coordination_console || {}).next_signal) || {};
      const reportFile = session.report_file || {};
      operatorState.replaceChildren(
        item(`<div class="line"><span class="title">Daily state</span><span class="meta">${esc(data.status)}</span></div>`),
        item(`<div class="badge-row">
          ${badge(`runtime ${(data.runtime || {}).status || "unknown"}`)}
          ${warnBadge(`queued ${queue.queued_count ?? 0}`, (queue.queued_count ?? 0) > 0)}
          ${warnBadge(`pending ${queue.promoted_pending_count ?? 0}`, (queue.promoted_pending_count ?? 0) > 0)}
          ${badge(`dismissals ${(data.dismissals || []).length}`)}
        </div>`),
        item(`<div class="next"><strong>Next signal</strong>: ${esc(signal.title || "unknown")}</div>`),
        item(`<div class="next">${esc(data.next_action || "")}</div>`),
        item(`<div class="line"><span class="title">Review session</span><span class="meta">${esc(session.status || "unknown")}</span></div>`),
        item(`<div class="badge-row">
          ${badge(`saved ${session.saved_count ?? 0}`)}
          ${warnBadge(`queued ${session.queued_count ?? 0}`, (session.queued_count ?? 0) > 0)}
          ${badge(`dismissed ${session.dismissed_count ?? 0}`)}
          ${badge(`outcomes ${session.outcome_count ?? 0}`)}
          ${badge(`reviewed ${session.reviewed_count ?? 0}`)}
          ${warnBadge(`active ${session.active_queue_count ?? 0}`, (session.active_queue_count ?? 0) > 0)}
          ${warnBadge(`pending ${session.pending_promotion_count ?? 0}`, (session.pending_promotion_count ?? 0) > 0)}
        </div>`),
        reportFile.requested ? item(`<div class="next"><strong>Saved report</strong>: ${esc(reportFile.status || "unknown")}${reportFile.path ? " / " + esc(reportFile.path) : ""}</div>`) : item(`<div class="next">No review-session report saved in this refresh.</div>`),
        item(`<div class="next">${esc(session.next_action || "")}</div>`)
      );
    }
    async function saveReviewSession() {
      const dailyRes = await fetch("/review/operator-daily-state?hours=24&limit=5");
      const daily = await dailyRes.json();
      const sessionRes = await fetch("/review/operator-review-session?hours=2&limit=25&save_report=true");
      const session = await sessionRes.json();
      renderOperatorState(daily, session);
      await loadReviewSessionReports();
      await loadReviewSessionRetention();
    }
    async function loadReviewSessionReports() {
      const res = await fetch("/review/operator-review-session-reports?limit=6");
      const data = await res.json();
      const latest = data.reports && data.reports.length > 0 ? data.reports[0] : null;
      if (latest) {
        latestReviewSession.replaceChildren(item(`
          <div class="line"><span class="title">${esc(latest.name)}</span><span class="meta">${esc(latest.status || "unknown")}</span></div>
          <div class="badge-row">
            ${badge(`groups ${latest.group_history_count ?? 0}`)}
            ${badge(`queue ${latest.queue_item_count ?? 0}`)}
            ${badge(`saved ${latest.saved_count ?? 0}`)}
            ${warnBadge(`queued ${latest.queued_count ?? 0}`, (latest.queued_count ?? 0) > 0)}
            ${badge(`reviewed ${latest.reviewed_count ?? 0}`)}
            ${warnBadge(`active ${latest.active_queue_count ?? 0}`, (latest.active_queue_count ?? 0) > 0)}
          </div>
          <div class="next">${esc(latest.next_action || "")}</div>
        `));
      } else {
        empty(latestReviewSession, "No saved review-session reports.");
      }
      renderList(reviewSessions, data.reports, r => item(`
        <div class="line"><span class="title">${esc(r.name)}</span><span class="meta">${esc(r.status)} / ${esc(r.hours || "unknown")}h</span></div>
        <div class="badge-row">
          ${badge(`groups ${r.group_history_count ?? 0}`)}
          ${badge(`queue ${r.queue_item_count ?? 0}`)}
          ${badge(`saved ${r.saved_count ?? 0}`)}
          ${warnBadge(`queued ${r.queued_count ?? 0}`, (r.queued_count ?? 0) > 0)}
          ${badge(`reviewed ${r.reviewed_count ?? 0}`)}
          ${warnBadge(`active ${r.active_queue_count ?? 0}`, (r.active_queue_count ?? 0) > 0)}
          ${warnBadge(`pending ${r.pending_promotion_count ?? 0}`, (r.pending_promotion_count ?? 0) > 0)}
        </div>
        <div class="next">${esc(r.next_action || "")}</div>
        <div class="button-row">
          <button type="button" data-review-session-report="${esc(r.name)}">Inspect</button>
        </div>
      `), "No saved review-session reports.");
      reviewSessions.querySelectorAll("button[data-review-session-report]").forEach(button => {
        button.addEventListener("click", () => loadReviewSessionDetail(button.dataset.reviewSessionReport));
      });
      if (data.reports && data.reports.length > 0) {
        await loadReviewSessionDetail(data.reports[0].name);
      } else {
        empty(reviewSessionDetail, "No review-session report selected.");
      }
    }
    async function loadReviewSessionRetention() {
      const res = await fetch("/review/operator-review-session-retention?keep=20");
      const data = await res.json();
      reviewSessionRetention.replaceChildren(
        item(`<div class="line"><span class="title">Retention</span><span class="meta">${esc(data.status || "unknown")}</span></div>`),
        item(`<div class="badge-row">
          ${badge(`total ${data.total_count ?? 0}`)}
          ${badge(`keep ${data.keep ?? 0}`)}
          ${warnBadge(`cleanup ${data.candidate_count ?? 0}`, (data.candidate_count ?? 0) > 0)}
          ${badge(`deleted ${data.deleted_count ?? 0}`)}
        </div>`),
        item(`<div class="next">${esc(data.next_action || data.error || "")}</div>`)
      );
    }
    async function loadReviewSessionDetail(name) {
      if (!name) {
        empty(reviewSessionDetail, "No review-session report selected.");
        return;
      }
      const res = await fetch(`/review/operator-review-session-report/${encodeURIComponent(name)}`);
      const data = await res.json();
      const summary = data.summary || {};
      const report = data.report || {};
      const groupRows = (report.group_summaries || []).slice(0, 4).map(group => item(`
        <div class="line"><span class="title">${esc(group.group_key)}</span><span class="meta">${esc(group.latest_event_type || "unknown")}</span></div>
        <div class="badge-row">
          ${badge(`actions ${group.action_count ?? 0}`)}
          ${badge(`saved ${group.saved_count ?? 0}`)}
          ${warnBadge(`queued ${group.queued_count ?? 0}`, (group.queued_count ?? 0) > 0)}
          ${badge(`dismissed ${group.dismissed_count ?? 0}`)}
          ${badge(`outcomes ${group.outcome_count ?? 0}`)}
        </div>
        <div class="next">${esc(group.latest_outcome || group.latest_recorded_at || "")}</div>
      `));
      const historyRows = (report.recent_group_history || []).slice(0, 5).map(entry => item(`
        <div class="line"><span class="title">${esc(entry.event_type)}</span><span class="meta">${esc(entry.status)}</span></div>
        <div class="next">${esc(entry.group_key)} / ${esc(entry.recorded_at)}</div>
      `));
      reviewSessionDetail.replaceChildren(
        item(`<div class="line"><span class="title">${esc(data.name)}</span><span class="meta">${esc(data.status)}</span></div>`),
        item(`<div class="next">${esc(data.path)}</div>`),
        item(`<div class="line"><span class="title">Generated</span><span class="meta">${esc(data.generated_at || "unknown")}</span></div>`),
        item(`<div class="badge-row">
          ${badge(`groups ${summary.group_history_count ?? 0}`)}
          ${badge(`queue ${summary.queue_item_count ?? 0}`)}
          ${badge(`saved ${summary.saved_count ?? 0}`)}
          ${warnBadge(`queued ${summary.queued_count ?? 0}`, (summary.queued_count ?? 0) > 0)}
          ${badge(`dismissed ${summary.dismissed_count ?? 0}`)}
          ${badge(`outcomes ${summary.outcome_count ?? 0}`)}
          ${badge(`reviewed ${summary.reviewed_count ?? 0}`)}
        </div>`),
        item(`<div class="next">${esc(summary.next_action || data.error || "")}</div>`),
        ...(groupRows.length ? groupRows : [item(`<div class="next">No grouped summary in this report.</div>`)]),
        ...(historyRows.length ? historyRows : [item(`<div class="next">No recent group-history entries in this report.</div>`)])
      );
    }
    async function runHandoffDrill() {
      const res = await fetch("/review/operator-handoff-drill?save_burn_in_report=true", { method: "POST" });
      const data = await res.json();
      const scenario = data.scenario || {};
      const burnIn = data.queue_burn_in || {};
      const reportFile = burnIn.report_file || {};
      operatorState.replaceChildren(
        item(`<div class="line"><span class="title">Handoff drill</span><span class="meta">${esc(data.status)}</span></div>`),
        item(`<div class="next">Scenario: ${esc(scenario.status || "unknown")}</div>`),
        item(`<div class="next">Rich evidence ready: ${esc(String(Boolean(scenario.rich_evidence_ready)))} (${esc(scenario.evidence_quality || "unknown")})</div>`),
        item(`<div class="next">Burn-in: ${esc(burnIn.status || "unknown")}</div>`),
        item(`<div class="next">Ready for live promotion: ${esc(String(Boolean(burnIn.ready_for_live_promotion)))}</div>`),
        item(`<div class="next">Saved proof: ${esc(reportFile.status || "not_requested")}${reportFile.path ? ` - ${esc(reportFile.path)}` : ""}</div>`),
        item(`<div class="next">${esc(data.next_action || "")}</div>`)
      );
      await loadCoordinationConsole();
    }
    function renderPackage(state) {
      packageState.replaceChildren(
        item(`<div class="line"><span class="title">Saved</span><span class="meta">${state && state.path ? "yes" : "no"}</span></div>`),
        item(`<div class="next">${state && state.path ? esc(state.path) : "No package saved in this server session."}</div>`),
        item(`<div class="line"><span class="title">Validation</span><span class="meta">${state && state.validation_status ? esc(state.validation_status) : "not run"}</span></div>`)
      );
    }
    async function post(path) {
      const res = await fetch(path, { method: "POST" });
      const data = await res.json();
      await load();
      return data;
    }
    async function loadPackages() {
      const res = await fetch("/review/packages?limit=6");
      const data = await res.json();
      renderList(packages, data.packages, p => item(`
        <div class="line"><span class="title">${esc(p.name)}</span><span class="meta">${esc(p.validation_status)} / ${esc(p.valid_action_count)} valid</span></div>
        <div class="next">${esc(p.path)}</div>
        <div class="button-row">
          <button type="button" data-package="${esc(p.name)}">Inspect</button>
          <button type="button" data-queue-package="${esc(p.name)}">Queue</button>
          <button type="button" data-delete-package="${esc(p.name)}">Delete</button>
        </div>
      `), "No saved review packages.");
      packages.querySelectorAll("button[data-package]").forEach(button => {
        button.addEventListener("click", () => loadPackageDetail(button.dataset.package));
      });
      packages.querySelectorAll("button[data-delete-package]").forEach(button => {
        button.addEventListener("click", () => deletePackage(button.dataset.deletePackage));
      });
      packages.querySelectorAll("button[data-queue-package]").forEach(button => {
        button.addEventListener("click", () => queuePackage(button.dataset.queuePackage));
      });
      if (data.packages && data.packages.length > 0) {
        await loadPackageDetail(data.packages[0].name);
      } else {
        empty(packageDetail, "No package selected.");
      }
    }
    async function loadPackageDetail(name) {
      if (!name) {
        empty(packageDetail, "No package selected.");
        return;
      }
      const res = await fetch(`/review/package/${encodeURIComponent(name)}`);
      const data = await res.json();
      const actionRows = (data.actions || []).slice(0, 6).map(a => item(`
        <div class="line"><span class="title">${esc(a.title)}</span><span class="meta">${esc(a.priority)}/${esc(a.state)} x${esc(a.count)}</span></div>
        <div class="next">${esc(a.source)}${a.project ? " / " + esc(a.project) : ""} / ${esc(a.intent)}</div>
        <div class="next">${esc(a.suggested_next_action)}</div>
        <div class="next">Evidence quality: ${esc(a.evidence_quality || "thin")}</div>
        <div class="next">Action ID: ${esc(a.action_id)}</div>
        <div class="next">Evidence: ${esc(a.evidence_event_id)} / ${esc(a.evidence_timestamp)}</div>
        ${Object.keys(a.evidence_context || {}).length ? `<div class="next">Context: ${esc(Object.entries(a.evidence_context).map(([key, value]) => `${key}=${value}`).join(" / "))}</div>` : ""}
      `));
      const queueItems = data.queue_items || [];
      const queueRows = queueItems.slice(0, 6).map(q => item(`
        <div class="line"><span class="title">${esc(q.title)}</span><span class="meta">${esc(q.status)}${q.promotion_outcome ? " / " + esc(q.promotion_outcome) : ""}</span></div>
        <div class="next">Queue ID: ${esc(q.queue_id)}${q.promotion_target_id ? " / target " + esc(q.promotion_target_id) : ""}</div>
        <div class="next">Updated: ${esc(q.updated_at || q.enqueued_at || "unknown")}</div>
      `));
      packageDetail.replaceChildren(
        item(`<div class="line"><span class="title">${esc(data.name)}</span><span class="meta">${esc(data.status)}</span></div>`),
        item(`<div class="next">${esc(data.path)}</div>`),
        item(`<div class="line"><span class="title">Generated</span><span class="meta">${esc(data.generated_at || "unknown")} / ${esc(data.hours || "unknown")}h</span></div>`),
        item(`<div class="line"><span class="title">Validation</span><span class="meta">${esc(data.validation.valid_action_count)} valid / ${esc(data.validation.error_count)} errors</span></div>`),
        item(`<div class="line"><span class="title">Queue lineage</span><span class="meta">${esc(queueItems.length)}</span></div>`),
        ...(queueRows.length ? queueRows : [item(`<div class="next">No queue lineage for this package yet.</div>`)]),
        ...((data.validation.errors || []).map(error => item(`<div class="next">${esc(error)}</div>`))),
        ...actionRows
      );
    }
    async function deletePackage(name) {
      if (!name) {
        return;
      }
      const res = await fetch(`/review/package/${encodeURIComponent(name)}`, { method: "DELETE" });
      const data = await res.json();
      if (data.status !== "ok") {
        packageDetail.replaceChildren(
          item(`<div class="line"><span class="title">${esc(name)}</span><span class="meta">${esc(data.status)}</span></div>`),
          item(`<div class="next">${esc(data.error)}</div>`)
        );
        return;
      }
      await loadPackages();
    }
    async function queuePackage(name) {
      if (!name) {
        return;
      }
      const res = await fetch(`/review/package/${encodeURIComponent(name)}/queue`, { method: "POST" });
      const data = await res.json();
      packageDetail.replaceChildren(
        item(`<div class="line"><span class="title">${esc(name)}</span><span class="meta">${esc(data.status)}</span></div>`),
        item(`<div class="next">Queued: ${esc(data.queued_count)} / skipped: ${esc(data.skipped_count)}</div>`),
        item(`<div class="next">${esc(data.next_action || data.error)}</div>`)
      );
      await loadImportQueue();
    }
    async function loadBurnInReports() {
      const res = await fetch("/review/burn-in-reports?limit=6");
      const data = await res.json();
      const reports = data.reports || [];
      const latest = reports[0] || {};
      renderList(burnInReports, data.reports, r => item(`
        <div class="line"><span class="title">${esc(r.name)}</span><span class="meta">${esc(r.status)} / ${r.ready_for_live_promotion ? "ready" : "not ready"}</span></div>
        <div class="badge-row">
          ${badge(ageLabel(r.generated_at || r.modified_at))}
          ${badge(`runtime ${r.runtime_status || "unknown"}`)}
          ${warnBadge(`queued ${r.queued_count ?? 0}`, (r.queued_count ?? 0) > 0)}
          ${warnBadge(`pending ${r.pending_count ?? 0}`, (r.pending_count ?? 0) > 0)}
          ${warnBadge(`stale ${r.stale_count ?? 0}`, (r.stale_count ?? 0) > 0)}
          ${warnBadge(`noise ${r.noise_candidate_count ?? 0}`, (r.noise_candidate_count ?? 0) > 0)}
        </div>
        ${r.name === latest.name ? `<div class="next"><strong>Proof trend</strong>: ${esc(proofTrendLabel(reports))}</div>` : ""}
        <div class="next">${esc(r.next_action || "")}</div>
        <div class="button-row">
          <button type="button" data-burn-in-report="${esc(r.name)}">Inspect</button>
        </div>
      `), "No saved burn-in reports.");
      burnInReports.querySelectorAll("button[data-burn-in-report]").forEach(button => {
        button.addEventListener("click", () => loadBurnInDetail(button.dataset.burnInReport));
      });
      if (data.reports && data.reports.length > 0) {
        await loadBurnInDetail(data.reports[0].name);
      } else {
        empty(burnInDetail, "No burn-in report selected.");
      }
    }
    async function loadBurnInDetail(name) {
      if (!name) {
        empty(burnInDetail, "No burn-in report selected.");
        return;
      }
      const res = await fetch(`/review/burn-in-report/${encodeURIComponent(name)}`);
      const data = await res.json();
      const summary = data.summary || {};
      burnInDetail.replaceChildren(
        item(`<div class="line"><span class="title">${esc(data.name)}</span><span class="meta">${esc(data.status)}</span></div>`),
        item(`<div class="next">${esc(data.path)}</div>`),
        item(`<div class="line"><span class="title">Generated</span><span class="meta">${esc(data.generated_at || "unknown")}</span></div>`),
        item(`<div class="line"><span class="title">Ready</span><span class="meta">${summary.ready_for_live_promotion ? "yes" : "no"}</span></div>`),
        item(`<div class="badge-row">
          ${warnBadge(`queued ${summary.queued_count ?? 0}`, (summary.queued_count ?? 0) > 0)}
          ${warnBadge(`pending ${summary.pending_count ?? 0}`, (summary.pending_count ?? 0) > 0)}
          ${warnBadge(`stale ${summary.stale_count ?? 0}`, (summary.stale_count ?? 0) > 0)}
          ${badge(`runtime ${summary.runtime_status || "unknown"}`)}
          ${warnBadge(`noise ${summary.noise_candidate_count ?? 0}`, (summary.noise_candidate_count ?? 0) > 0)}
        </div>`),
        item(`<div class="next">${esc(summary.next_action || data.error || "")}</div>`)
      );
    }
    async function loadImportQueue() {
      const res = await fetch("/review/import-queue?limit=25");
      const data = await res.json();
      const health = data.health || {};
      const reminder = data.outcome_sync_reminder || {};
      const nextCommands = data.next_commands || [];
      importQueueHealth.replaceChildren(
        item(`
          <div class="line"><span class="title">Queue health</span><span class="meta">${esc(health.status || "unknown")}</span></div>
          <div class="badge-row">
            ${warnBadge(`queued ${health.queued_count ?? 0}`, (health.queued_count ?? 0) > 0)}
            ${warnBadge(`pending ${health.promoted_pending_count ?? 0}`, (health.promoted_pending_count ?? 0) > 0)}
            ${warnBadge(`stale ${health.promoted_pending_stale_count ?? 0}`, (health.promoted_pending_stale_count ?? 0) > 0)}
            ${badge(`resolved ${(health.promoted_accepted_count ?? 0) + (health.promoted_rejected_count ?? 0) + (health.promoted_ignored_count ?? 0)}`)}
          </div>
          <div class="next">${esc(health.next_action || "")}</div>
          ${reminder.should_remind ? `<div class="next"><strong>Outcome sync</strong>: ${esc(reminder.next_action || "")}</div>` : ""}
          ${nextCommands.length ? `<div class="next"><strong>Next command</strong>: ${esc(nextCommands[0])}</div>` : ""}
        `)
      );
      const filter = importQueueFilter ? importQueueFilter.value : "open";
      const staleAfterSeconds = Number(health.stale_after_hours || 4) * 60 * 60;
      const isPending = q => q.status === "promoted" && (q.promotion_outcome || "pending") === "pending";
      const isStale = q => {
        if (!isPending(q)) {
          return false;
        }
        const parsed = Date.parse(q.promotion_outcome_at || q.promoted_at || q.updated_at || "");
        return !Number.isNaN(parsed) && ((Date.now() - parsed) / 1000) >= staleAfterSeconds;
      };
      const rows = (data.items || []).filter(q => {
        if (filter === "all") {
          return true;
        }
        if (filter === "promoted") {
          return q.status === "promoted";
        }
        if (filter === "pending") {
          return isPending(q);
        }
        if (filter === "stale") {
          return isStale(q);
        }
        if (filter === "queued") {
          return q.status === "queued" || q.status === "snoozed";
        }
        if (filter === "reviewed") {
          return q.status === "reviewed";
        }
        if (filter === "resolved") {
          return q.status === "rejected" || q.status === "reviewed" || q.status === "superseded" || (q.status === "promoted" && !isPending(q));
        }
        return q.status === "queued" || q.status === "snoozed" || isPending(q);
      });
      renderList(importQueue, rows, q => item(`
        <div class="line"><span class="title">${esc(q.title)}</span><span class="meta">${esc(q.priority)}/${esc(q.state)}</span></div>
        <div class="badge-row">
          ${warnBadge(q.status, q.status === "queued")}
          ${warnBadge(q.promotion_outcome, isPending(q))}
          ${warnBadge(isStale(q) ? "stale" : "", true)}
          ${badge(ageLabel(q.updated_at || q.enqueued_at))}
        </div>
        <div class="next">${esc(q.source_package_name)}${q.promotion_target_id ? " / " + esc(q.promotion_target_id) : ""}</div>
        <div class="next">Evidence: ${esc(q.evidence_event_id)}</div>
        <div class="button-row">
          <button type="button" data-queue-id="${esc(q.queue_id)}" data-queue-status="reviewed">Reviewed only</button>
          <button type="button" data-queue-id="${esc(q.queue_id)}" data-queue-status="promoted">Promote</button>
          <button type="button" data-queue-id="${esc(q.queue_id)}" data-queue-status="snoozed">Snooze</button>
          <button type="button" data-queue-id="${esc(q.queue_id)}" data-queue-status="rejected">Reject</button>
        </div>
      `), "No queued import handoff items.");
      importQueue.querySelectorAll("button[data-queue-id]").forEach(button => {
        button.addEventListener("click", () => updateQueueItem(button.dataset.queueId, button.dataset.queueStatus));
      });
    }
    async function loadQueueReview() {
      const res = await fetch("/review/import-queue-review?limit=25");
      const data = await res.json();
      const batches = data.batches || [];
      queueReview.replaceChildren(item(`
        <div class="line"><span class="title">Queued handoff review</span><span class="meta">${esc(data.status || "unknown")}</span></div>
        <div class="badge-row">
          ${warnBadge(`queued ${data.queued_count ?? 0}`, (data.queued_count ?? 0) > 0)}
          ${warnBadge(`operator decisions ${data.operator_decision_count ?? 0}`, (data.operator_decision_count ?? 0) > 0)}
          ${warnBadge(`pending ${data.pending_count ?? 0}`, (data.pending_count ?? 0) > 0)}
          ${warnBadge(`stale ${data.stale_count ?? 0}`, (data.stale_count ?? 0) > 0)}
          ${badge(`batches ${data.batch_count ?? 0}`)}
        </div>
        <div class="next">${esc(data.next_action || "")}</div>
        ${(data.next_commands || []).slice(0, 2).map(command => `<div class="next"><strong>Next command</strong>: ${esc(command)}</div>`).join("")}
      `), ...batches.slice(0, 5).map(batch => item(`
        <div class="line"><span class="title">${esc(batch.title)}</span><span class="meta">${esc(batch.item_count)} item(s)</span></div>
        <div class="badge-row">
          ${warnBadge(batch.priority, batch.priority === "high")}
          ${badge(batch.state)}
          ${badge(batch.source_package_name)}
        </div>
        <div class="next">${esc(batch.suggested_next_action || "")}</div>
        ${batch.first_queue_id ? `<div class="next">First queue id: ${esc(batch.first_queue_id)}</div>` : ""}
        ${(batch.summaries || []).slice(0, 3).map(summary => `<div class="next">${esc(summary)}</div>`).join("")}
      `)));
    }
    async function updateQueueItem(queueId, status) {
      if (!queueId || !status) {
        return;
      }
      const body = { status, reason: `Review UI marked ${status}` };
      if (status === "snoozed") {
        body.snoozed_until = new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString();
      }
      if (status === "promoted") {
        body.promotion_target = "personal-ops task suggestion";
        body.promotion_outcome = "pending";
        body.promotion_outcome_note = "Review UI marked the handoff promoted; record the personal-ops outcome later.";
      }
      const res = await fetch(`/review/import-queue/${encodeURIComponent(queueId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
      const data = await res.json();
      packageDetail.replaceChildren(
        item(`<div class="line"><span class="title">${esc(queueId)}</span><span class="meta">${esc(data.status)}</span></div>`),
        item(`<div class="next">${esc(data.next_action || data.error)}</div>`)
      );
      await loadQueueReview();
      await loadImportQueue();
    }
    document.getElementById("refresh").addEventListener("click", load);
    importQueueFilter.addEventListener("change", loadImportQueue);
    document.getElementById("savePackage").addEventListener("click", () => post("/review/save-package"));
    document.getElementById("saveSession").addEventListener("click", saveReviewSession);
    document.getElementById("validatePackage").addEventListener("click", () => post("/review/validate-package"));
    document.getElementById("runDrill").addEventListener("click", runHandoffDrill);
    load().catch(err => {
      summary.replaceChildren(item(`<span class="empty">Unable to load review data: ${err}</span>`));
    });
  </script>
</body>
</html>"""


def _validation_error_summary(errors: Sequence[Any]) -> list[dict[str, object]]:
    summary: list[dict[str, object]] = []
    for error in errors:
        if not isinstance(error, dict):
            continue
        typed_error = cast(dict[str, object], error)
        loc = typed_error.get("loc")
        item: dict[str, object] = {
            "type": typed_error.get("type"),
            "loc": loc,
        }
        ctx = typed_error.get("ctx")
        if isinstance(ctx, dict):
            item["ctx"] = ctx
        if (
            typed_error.get("type") == "literal_error"
            and isinstance(loc, tuple)
            and loc[-1] in {"source", "level"}
        ):
            input_value = typed_error.get("input")
            if isinstance(input_value, str):
                item["input"] = input_value[:80]
        summary.append(item)
    return summary


def _safe_review_error(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, str) and value in _REVIEW_SAFE_ERROR_MESSAGES:
        return value
    return _REVIEW_GENERIC_ERROR


def _review_response(value: object) -> object:
    if isinstance(value, dict):
        safe: dict[str, object] = {}
        typed_value = cast(dict[object, object], value)
        for raw_key, nested_value in typed_value.items():
            key = str(raw_key)
            if key in {"error", "load_error"}:
                safe[key] = _safe_review_error(nested_value)
            elif key == "errors" and isinstance(nested_value, list):
                typed_errors = cast(list[object], nested_value)
                safe[key] = [_safe_review_error(error) for error in typed_errors]
            else:
                safe[key] = _review_response(nested_value)
        return safe
    if isinstance(value, list):
        typed_items = cast(list[object], value)
        return [_review_response(item) for item in typed_items]
    return value


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Log event validation failures without logging request bodies."""
    if request.url.path == "/events":
        logger.warning(
            "Rejected event payload from %s: %s",
            request.client.host if request.client else "unknown",
            _validation_error_summary(exc.errors()),
        )
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.post("/events", response_model=EventResponse, status_code=201)
async def create_event(event: Event) -> EventResponse:
    """Accept a notification event, classify it, route to channels, and confirm."""
    global _event_count
    stored = process_event(event)
    _event_count += 1
    return EventResponse(
        event_id=stored.event_id,
        level=stored.classified_level or stored.level,
    )


@app.get("/health")
async def health() -> dict[str, object]:
    """Server health check."""
    uptime = time.monotonic() - _start_time if _start_time else 0
    return {
        "status": "ok",
        "uptime_seconds": round(uptime, 1),
        "events_processed": _event_count,
        "watcher_active": _observer is not None,
    }


async def _collect_health_details() -> dict[str, object]:
    """Detailed runtime readiness without exposing secrets."""
    base = await health()
    readiness = collect_runtime_readiness()
    base.update(readiness)
    base["suppression"] = get_suppression_engine().snapshot()
    readiness_retention = readiness["retention"]
    runtime_retention = get_retention_runtime_status()
    assert isinstance(readiness_retention, dict)
    base["retention"] = {
        **readiness_retention,
        "last_checked_at": runtime_retention["last_checked_at"],
        "last_status": runtime_retention["last_status"],
        "last_rotated": runtime_retention["last_rotated"],
        "last_archive_path": runtime_retention["last_archive_path"],
    }
    return base


@app.get("/health/details")
async def health_details() -> dict[str, object]:
    """Detailed runtime readiness without exposing secrets."""
    return await _collect_health_details()


@app.get("/review", response_class=HTMLResponse)
async def review() -> HTMLResponse:
    """Local operator review surface."""
    return HTMLResponse(
        REVIEW_HTML.replace(
            "__ACTION_PROPOSAL_REVIEW_WINDOW_HOURS__",
            str(ACTION_PROPOSAL_REVIEW_WINDOW_HOURS),
        )
    )


@app.get("/review/data")
async def review_data(hours: int = 2, limit: int = 6) -> dict[str, object]:
    """Read-only data backing the local review surface."""
    safe_hours = max(hours, 1)
    safe_limit = max(limit, 1)
    inbox = run_inbox(hours=safe_hours, limit=safe_limit)
    actions = run_personal_ops_action_export(hours=safe_hours, limit=safe_limit)
    runtime = await _review_runtime_status()
    queue_health = run_personal_ops_import_queue_health_check(limit=safe_limit)
    outcome_sync_reminder = run_personal_ops_outcome_sync_reminder(limit=safe_limit)
    coordination_readiness = await asyncio.to_thread(
        run_coordination_readiness,
        limit=safe_limit,
    )
    operator_focus = _review_operator_focus(
        runtime=runtime,
        action_count=len(actions["actions"]),
        queue_health=dict(queue_health["health"]),
        outcome_sync_reminder=dict(outcome_sync_reminder),
    )
    operator_focus.update(
        {
            "queued_count": queue_health["health"]["queued_count"],
            "pending_count": queue_health["health"]["promoted_pending_count"],
            "stale_count": queue_health["health"]["promoted_pending_stale_count"],
            "action_count": len(actions["actions"]),
        }
    )
    return cast(dict[str, object], _review_response({
        "status": "ok"
        if (
            inbox["status"] == "ok"
            and actions["status"] == "ok"
            and runtime["status"] == "ok"
            and queue_health["status"] == "ok"
            and outcome_sync_reminder["status"] == "ok"
            and coordination_readiness["status"] == "ok"
        )
        else "degraded",
        "hours": safe_hours,
        "limit": safe_limit,
        "runtime": runtime,
        "inbox": inbox,
        "actions": actions,
        "operator_focus": operator_focus,
        "coordination_readiness": coordination_readiness,
        "queue_health": queue_health["health"],
        "outcome_sync_reminder": outcome_sync_reminder,
        "trust": {
            "proposed": bool(actions["actions"]),
            "saved": _latest_review_package_path is not None,
            "validated": False,
            "imported": False,
            "applied": False,
            "next_action": "Save and validate a review package before any personal-ops import step.",
        },
        "review_package": _review_package_state(),
    }))


@app.post("/review/save-package")
async def review_save_package(hours: int = 2, limit: int = 6) -> dict[str, object]:
    """Stage a local action review package without importing or applying it."""
    global _latest_review_package_path, _latest_review_package_validation_status
    report = run_personal_ops_action_export(
        hours=max(hours, 1),
        limit=max(limit, 1),
        save_review_package=True,
    )
    path = report["review_package"]["path"]
    _latest_review_package_path = path if isinstance(path, str) else None
    _latest_review_package_validation_status = None
    return cast(dict[str, object], _review_response({
        "status": report["status"],
        "applied": False,
        "review_package": report["review_package"],
        "action_count": len(report["actions"]),
    }))


@app.get("/review/packages")
async def review_packages(limit: int = 10) -> dict[str, object]:
    """List recent saved review packages without importing or applying them."""
    return cast(dict[str, object], _review_response({
        "status": "ok",
        "packages": list_action_review_packages(limit=max(limit, 1)),
        "applied": False,
    }))


@app.get("/review/package/{name}")
async def review_package_detail(name: str) -> dict[str, object]:
    """Inspect one saved review package without importing or applying it."""
    return cast(dict[str, object], _review_response(load_action_review_package_detail(name=name)))


@app.post("/review/package/{name}/queue")
async def review_queue_package(name: str) -> dict[str, object]:
    """Queue one saved review package for operator-mediated personal-ops import."""
    detail = load_action_review_package_detail(name=name)
    package_path = action_review_package_path_for_name(name=name)
    if package_path is None:
        return cast(dict[str, object], _review_response({
            "status": "degraded",
            "path": str(detail["path"]),
            "dry_run": True,
            "applied": False,
            "enqueued": False,
            "queued_count": 0,
            "skipped_count": 0,
            "queue_path": None,
            "validation": detail["validation"],
            "next_action": "Choose a valid saved review package before queueing it.",
            "error": "invalid review package name",
        }))
    report = run_personal_ops_import_stub(path=package_path, enqueue=True)
    return cast(dict[str, object], _review_response(report))


@app.get("/review/import-queue")
async def review_import_queue(limit: int = 10, stale_after_hours: float = 4.0) -> dict[str, object]:
    """List queued personal-ops handoff items without applying them."""
    queue_health = run_personal_ops_import_queue_health_check(
        limit=max(limit, 1), stale_after_hours=stale_after_hours
    )
    outcome_sync_reminder = run_personal_ops_outcome_sync_reminder(
        limit=max(limit, 1),
        stale_after_hours=stale_after_hours,
    )
    return cast(dict[str, object], _review_response({
        "status": "ok",
        "items": list_personal_ops_import_queue(limit=max(limit, 1)),
        "health": queue_health["health"],
        "next_commands": queue_health["next_commands"],
        "outcome_sync_reminder": outcome_sync_reminder,
        "applied": False,
    }))


@app.get("/review/import-queue-review")
async def review_import_queue_review(
    limit: int = 25,
    stale_after_hours: float = 4.0,
) -> dict[str, object]:
    """Summarize queued handoff batches without applying decisions."""
    report = await asyncio.to_thread(
        run_personal_ops_queue_review,
        limit=max(limit, 1),
        stale_after_hours=stale_after_hours,
    )
    return cast(dict[str, object], _review_response(report))


@app.get("/review/burn-in-reports")
async def review_burn_in_reports(limit: int = 10) -> dict[str, object]:
    """List saved queue burn-in reports without applying work."""
    return cast(dict[str, object], _review_response({
        "status": "ok",
        "reports": list_personal_ops_queue_burn_in_reports(limit=max(limit, 1)),
        "applied": False,
    }))


@app.get("/review/burn-in-report/{name}")
async def review_burn_in_report_detail(name: str) -> dict[str, object]:
    """Inspect one saved queue burn-in report without applying work."""
    return cast(
        dict[str, object],
        _review_response(load_personal_ops_queue_burn_in_report_detail(name=name)),
    )


@app.get("/review/noise-candidates")
async def review_noise_candidates(limit: int = 10) -> dict[str, object]:
    """Summarize the latest saved burn-in noise candidates without applying work."""
    report = await asyncio.to_thread(review_latest_noise_candidates, limit=max(limit, 1))
    return cast(dict[str, object], _review_response(report))


@app.get("/review/coordination-readiness")
async def review_coordination_readiness(limit: int = 5) -> dict[str, object]:
    """Summarize coordination expansion readiness without applying work."""
    report = await asyncio.to_thread(run_coordination_readiness, limit=max(limit, 1))
    return cast(dict[str, object], _review_response(report))


@app.get("/review/coordination-console")
async def review_coordination_console(hours: int = 24, limit: int = 5) -> dict[str, object]:
    """Return one compact coordination console summary without applying work."""
    report = await asyncio.to_thread(
        run_coordination_console,
        hours=max(hours, 1),
        limit=max(limit, 1),
    )
    return cast(dict[str, object], _review_response(report))


@app.get("/review/policy-check")
async def review_policy_check() -> dict[str, object]:
    """Return live policy diagnostics for the local review surface."""
    report = await asyncio.to_thread(run_policy_check)
    return cast(dict[str, object], _review_response(report))


async def _action_proposal_group_body(request: Request) -> dict[str, object]:
    try:
        raw_body = await request.json()
    except ValueError:
        raw_body = {}
    return cast(dict[str, object], raw_body) if isinstance(raw_body, dict) else {}


@app.post("/review/action-proposal-group/package")
async def review_save_action_proposal_group(request: Request) -> dict[str, object]:
    """Stage one proposal-review group as a saved package without applying work."""
    body = await _action_proposal_group_body(request)
    group_key = body.get("group_key")
    route = body.get("route")
    hours = body.get("hours", ACTION_PROPOSAL_REVIEW_WINDOW_HOURS)
    limit = body.get("limit", 25)
    report = await asyncio.to_thread(
        save_action_proposal_group_package,
        group_key=group_key if isinstance(group_key, str) else "",
        route=route if isinstance(route, str) else None,
        hours=int(hours) if isinstance(hours, int) else ACTION_PROPOSAL_REVIEW_WINDOW_HOURS,
        limit=int(limit) if isinstance(limit, int) else 25,
        enqueue=False,
    )
    return cast(dict[str, object], _review_response(report))


@app.post("/review/action-proposal-group/queue")
async def review_queue_action_proposal_group(request: Request) -> dict[str, object]:
    """Save and queue one proposal-review group without applying personal-ops work."""
    body = await _action_proposal_group_body(request)
    group_key = body.get("group_key")
    route = body.get("route")
    hours = body.get("hours", ACTION_PROPOSAL_REVIEW_WINDOW_HOURS)
    limit = body.get("limit", 25)
    report = await asyncio.to_thread(
        save_action_proposal_group_package,
        group_key=group_key if isinstance(group_key, str) else "",
        route=route if isinstance(route, str) else None,
        hours=int(hours) if isinstance(hours, int) else ACTION_PROPOSAL_REVIEW_WINDOW_HOURS,
        limit=int(limit) if isinstance(limit, int) else 25,
        enqueue=True,
    )
    return cast(dict[str, object], _review_response(report))


@app.post("/review/action-proposal-group/dismiss")
async def review_dismiss_action_proposal_group(request: Request) -> dict[str, object]:
    """Dismiss every active proposal in one proposal-review group locally."""
    body = await _action_proposal_group_body(request)
    group_key = body.get("group_key")
    route = body.get("route")
    reason = body.get("reason")
    hours = body.get("hours", ACTION_PROPOSAL_REVIEW_WINDOW_HOURS)
    limit = body.get("limit", 25)
    report = await asyncio.to_thread(
        dismiss_action_proposal_group,
        group_key=group_key if isinstance(group_key, str) else "",
        reason=reason
        if isinstance(reason, str) and reason.strip()
        else "Review UI dismissed a grouped proposal as known noise.",
        route=route if isinstance(route, str) else None,
        hours=int(hours) if isinstance(hours, int) else ACTION_PROPOSAL_REVIEW_WINDOW_HOURS,
        limit=int(limit) if isinstance(limit, int) else 25,
    )
    return cast(dict[str, object], _review_response(report))


@app.post("/review/action-proposal-group/outcome")
async def review_record_action_proposal_group_outcome(request: Request) -> dict[str, object]:
    """Record a local outcome for one proposal-review group without applying work."""
    body = await _action_proposal_group_body(request)
    group_key = body.get("group_key")
    outcome = body.get("outcome")
    reason = body.get("reason")
    hours = body.get("hours", ACTION_PROPOSAL_REVIEW_WINDOW_HOURS)
    limit = body.get("limit", 25)
    report = await asyncio.to_thread(
        record_action_proposal_group_outcome,
        group_key=group_key if isinstance(group_key, str) else "",
        outcome=outcome if isinstance(outcome, str) else "",
        reason=reason
        if isinstance(reason, str) and reason.strip()
        else "Review UI recorded a grouped proposal outcome.",
        hours=int(hours) if isinstance(hours, int) else ACTION_PROPOSAL_REVIEW_WINDOW_HOURS,
        limit=int(limit) if isinstance(limit, int) else 25,
    )
    return cast(dict[str, object], _review_response(report))


@app.post("/review/action-proposal/{dismissal_key}/dismiss")
async def review_dismiss_action_proposal(dismissal_key: str, request: Request) -> dict[str, object]:
    """Dismiss one repeated action proposal from the local review surface."""
    try:
        raw_body = await request.json()
    except ValueError:
        raw_body = {}
    body = cast(dict[str, object], raw_body) if isinstance(raw_body, dict) else {}
    reason_value = body.get("reason")
    reason = (
        reason_value
        if isinstance(reason_value, str) and reason_value.strip()
        else "Review UI dismissed repeated proposal as known noise."
    )
    actions = await asyncio.to_thread(
        run_personal_ops_action_export,
        hours=24,
        limit=100,
        include_dismissed=True,
    )
    matched = next(
        (action for action in actions["actions"] if action["dismissal_key"] == dismissal_key),
        None,
    )
    report = dismiss_action_proposal(
        dismissal_key=dismissal_key,
        reason=reason,
        source=matched["source"] if matched is not None else None,
        project=matched["project"] if matched is not None else None,
        intent=matched["intent"] if matched is not None else None,
        title=matched["title"] if matched is not None else None,
        body=matched["signal_body"] if matched is not None else None,
        evidence_event_id=matched["evidence_event_id"] if matched is not None else None,
    )
    return cast(dict[str, object], _review_response({
        **dict(report),
        "next_action": "Proposal dismissed from the local console. Future matching proposals stay hidden until the dismissal file is edited.",
    }))


@app.get("/review/action-proposal-dismissals")
async def review_action_proposal_dismissals(
    limit: int = 25,
    dismissal_key: str | None = None,
    include_inactive: bool = False,
) -> dict[str, object]:
    """List local action proposal dismissals without applying work."""
    report = await asyncio.to_thread(
        run_action_proposal_dismissal_list,
        limit=max(limit, 1),
        dismissal_key=dismissal_key,
        include_inactive=include_inactive,
    )
    return cast(dict[str, object], _review_response(report))


@app.post("/review/action-proposal/{dismissal_key}/undismiss")
async def review_undismiss_action_proposal(
    dismissal_key: str,
    request: Request,
) -> dict[str, object]:
    """Reactivate one dismissed proposal without deleting dismissal history."""
    try:
        raw_body = await request.json()
    except ValueError:
        raw_body = {}
    body = cast(dict[str, object], raw_body) if isinstance(raw_body, dict) else {}
    reason_value = body.get("reason")
    reason = (
        reason_value
        if isinstance(reason_value, str) and reason_value.strip()
        else "Review UI reactivated this proposal."
    )
    report = undismiss_action_proposal(dismissal_key=dismissal_key, reason=reason)
    return cast(dict[str, object], _review_response({
        **dict(report),
        "next_action": "Proposal reactivated. Matching future proposals can appear in the local console again.",
    }))


@app.get("/review/operator-daily-state")
async def review_operator_daily_state(
    hours: int = 24,
    limit: int = 10,
    save_report: bool = False,
) -> dict[str, object]:
    """Build a resume-ready operator state snapshot without applying work."""
    report = await asyncio.to_thread(
        run_operator_daily_state,
        hours=max(hours, 1),
        limit=max(limit, 1),
        save_report=save_report,
    )
    return cast(dict[str, object], _review_response(report))


@app.get("/review/operator-review-session")
async def review_operator_review_session(
    hours: int = 2,
    limit: int = 25,
    save_report: bool = False,
) -> dict[str, object]:
    """Summarize recent review-session activity without applying work."""
    report = await asyncio.to_thread(
        run_operator_review_session,
        hours=max(hours, 1),
        limit=max(limit, 1),
        save_report=save_report,
    )
    return cast(dict[str, object], _review_response(report))


@app.get("/review/operator-review-session-reports")
async def review_operator_review_session_reports(limit: int = 10) -> dict[str, object]:
    """List saved review-session reports without applying work."""
    return cast(dict[str, object], _review_response({
        "status": "ok",
        "reports": list_operator_review_session_reports(limit=max(limit, 1)),
        "applied": False,
    }))


@app.get("/review/operator-review-session-retention")
async def review_operator_review_session_retention(keep: int = 20) -> dict[str, object]:
    """Summarize saved review-session cleanup pressure without applying work."""
    report = await asyncio.to_thread(
        prune_operator_review_session_reports,
        keep=max(keep, 1),
        dry_run=True,
    )
    return cast(dict[str, object], _review_response(report))


@app.get("/review/operator-review-session-report/{name}")
async def review_operator_review_session_report_detail(name: str) -> dict[str, object]:
    """Inspect one saved review-session report without applying work."""
    return cast(
        dict[str, object],
        _review_response(load_operator_review_session_report_detail(name=name)),
    )


@app.post("/review/operator-handoff-drill")
async def review_operator_handoff_drill(save_burn_in_report: bool = False) -> dict[str, object]:
    """Run a temporary handoff lifecycle drill without touching the live queue."""
    report = await asyncio.to_thread(
        run_operator_handoff_drill,
        save_burn_in_report=save_burn_in_report,
    )
    return cast(dict[str, object], _review_response(report))


@app.get("/review/outcome-sync-reminder")
async def review_outcome_sync_reminder(
    limit: int = 10,
    stale_after_hours: float = 4.0,
) -> dict[str, object]:
    """Report promoted handoffs that still need outcome sync without applying work."""
    return cast(dict[str, object], _review_response(
        run_personal_ops_outcome_sync_reminder(
            limit=max(limit, 1),
            stale_after_hours=stale_after_hours,
        )
    ))


@app.patch("/review/import-queue/{queue_id}")
async def review_update_import_queue(queue_id: str, request: Request) -> dict[str, object]:
    """Update one queued handoff lifecycle state without applying it."""
    body = cast(dict[str, object], await request.json())
    status = body.get("status")
    if not isinstance(status, str):
        return cast(dict[str, object], _review_response({
            "status": "degraded",
            "queue_id": queue_id,
            "updated": False,
            "item": None,
            "next_action": "Choose a lifecycle status before updating this queue item.",
            "error": "missing status",
        }))
    reason_value = body.get("reason")
    snoozed_until_value = body.get("snoozed_until")
    promotion_target_value = body.get("promotion_target")
    promotion_target_id_value = body.get("promotion_target_id")
    promotion_outcome_value = body.get("promotion_outcome")
    promotion_outcome_note_value = body.get("promotion_outcome_note")
    report = update_personal_ops_import_queue_item(
        queue_id=queue_id,
        status=status,
        reason=reason_value if isinstance(reason_value, str) else None,
        snoozed_until=snoozed_until_value if isinstance(snoozed_until_value, str) else None,
        promotion_target=promotion_target_value
        if isinstance(promotion_target_value, str)
        else None,
        promotion_target_id=promotion_target_id_value
        if isinstance(promotion_target_id_value, str)
        else None,
        promotion_outcome=promotion_outcome_value
        if isinstance(promotion_outcome_value, str)
        else None,
        promotion_outcome_note=promotion_outcome_note_value
        if isinstance(promotion_outcome_note_value, str)
        else None,
    )
    return cast(dict[str, object], _review_response(report))


@app.delete("/review/package/{name}")
async def review_delete_package(name: str) -> dict[str, object]:
    """Delete one saved review package without importing or applying it."""
    global _latest_review_package_path, _latest_review_package_validation_status
    report = delete_action_review_package(name=name)
    if report["deleted"] and _latest_review_package_path == report["path"]:
        _latest_review_package_path = None
        _latest_review_package_validation_status = None
    return cast(dict[str, object], _review_response(report))


@app.post("/review/validate-package")
async def review_validate_package() -> dict[str, object]:
    """Validate the latest staged review package without importing or applying it."""
    global _latest_review_package_validation_status
    package_path = get_latest_review_package_path()
    package_name: str | None = Path(package_path).name if package_path is not None else None
    if package_path is None:
        packages = list_action_review_packages(limit=1)
        if packages:
            package_path = packages[0]["path"]
            package_name = packages[0]["name"]
            global _latest_review_package_path
            _latest_review_package_path = package_path
    if package_path is None:
        _latest_review_package_validation_status = "not_found"
        return cast(dict[str, object], _review_response({
            "status": "degraded",
            "applied": False,
            "error": "no review package has been saved in this server session",
            "review_package": _review_package_state("not_found"),
        }))
    safe_package_path = (
        action_review_package_path_for_name(name=package_name)
        if isinstance(package_name, str)
        else None
    )
    if safe_package_path is None:
        _latest_review_package_validation_status = "invalid"
        return cast(dict[str, object], _review_response({
            "status": "degraded",
            "applied": False,
            "error": "invalid review package name",
            "review_package": _review_package_state("invalid"),
        }))
    validation = validate_action_package(safe_package_path)
    _latest_review_package_validation_status = validation["status"]
    return cast(dict[str, object], _review_response({
        "status": validation["status"],
        "applied": False,
        "validation": validation,
        "review_package": _review_package_state(validation["status"]),
    }))
