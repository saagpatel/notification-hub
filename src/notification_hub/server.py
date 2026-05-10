"""FastAPI server — event intake, health check, bridge file watcher lifecycle."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Sequence
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
    delete_action_review_package,
    list_action_review_packages,
    list_personal_ops_import_queue,
    list_personal_ops_queue_burn_in_reports,
    load_action_review_package_detail,
    load_personal_ops_queue_burn_in_report_detail,
    run_coordination_console,
    run_coordination_readiness,
    run_inbox,
    run_personal_ops_action_export,
    run_personal_ops_import_queue_health_check,
    run_personal_ops_import_stub,
    run_personal_ops_outcome_sync_reminder,
    run_retention,
    update_personal_ops_import_queue_item,
    validate_action_package,
)
from notification_hub.pipeline import get_suppression_engine, process_event
from notification_hub.watcher import ObserverHandle, start_watcher

logger = logging.getLogger(__name__)

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
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
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
    .next { color: #475467; font-size: 13px; margin-top: 4px; }
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
        <button id="validatePackage" type="button">Validate package</button>
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
        <div class="toolbar">
          <h2>Import Queue</h2>
          <select id="importQueueFilter" aria-label="Import queue filter">
            <option value="open">Open</option>
            <option value="pending">Pending outcome</option>
            <option value="stale">Stale outcome</option>
            <option value="queued">Queued</option>
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
    const actions = document.getElementById("actions");
    const rollups = document.getElementById("rollups");
    const attention = document.getElementById("attention");
    const trust = document.getElementById("trust");
    const packageState = document.getElementById("package");
    const packages = document.getElementById("packages");
    const packageDetail = document.getElementById("packageDetail");
    const burnInReports = document.getElementById("burnInReports");
    const burnInDetail = document.getElementById("burnInDetail");
    const importQueueHealth = document.getElementById("importQueueHealth");
    const importQueue = document.getElementById("importQueue");
    const importQueueFilter = document.getElementById("importQueueFilter");

    function item(html) {
      const li = document.createElement("li");
      li.innerHTML = html;
      return li;
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
    function renderList(target, rows, render, emptyText) {
      if (!rows || rows.length === 0) {
        empty(target, emptyText);
        return;
      }
      target.replaceChildren(...rows.map(render));
    }
    async function load() {
      const res = await fetch("/review/data?hours=2&limit=6");
      const data = await res.json();
      summary.replaceChildren(
        item(`<span>Runtime</span><strong>${esc(data.runtime.status)}</strong>`),
        item(`<span>Events</span><strong>${esc(data.inbox.events_seen)}</strong>`),
        item(`<span>Actions</span><strong>${esc(data.actions.actions.length)}</strong>`),
        item(`<span>Rollups</span><strong>${esc(data.inbox.rollups.length)}</strong>`),
        item(`<span>Applied</span><strong>${data.trust.applied ? "yes" : "no"}</strong>`)
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
        <div class="next">${esc(readiness.summary || "")}</div>
        <div class="next">${esc(readiness.next_action || "")}</div>
      `));
      renderList(actions, data.actions.actions, a => item(`
        <div class="line"><span class="title">${esc(a.title)}</span><span class="meta">${esc(a.priority)}/${esc(a.state)} x${esc(a.count)}</span></div>
        <div class="next">${esc(a.suggested_next_action)}</div>
      `), "No action proposals.");
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
      await loadImportQueue();
      await loadCoordinationConsole();
    }
    async function loadCoordinationConsole() {
      const res = await fetch("/review/coordination-console?hours=2&limit=5");
      const data = await res.json();
      const readiness = data.readiness || {};
      const queue = data.queue_health || {};
      const reminder = data.outcome_sync_reminder || {};
      coordinationConsole.replaceChildren(item(`
        <div class="line"><span class="title">${esc(readiness.decision || "unknown")}</span><span class="meta">${esc(data.status || "unknown")}</span></div>
        <div class="badge-row">
          ${badge(`actions ${data.action_count ?? 0}`)}
          ${warnBadge(`queued ${queue.queued_count ?? 0}`, (queue.queued_count ?? 0) > 0)}
          ${warnBadge(`pending ${queue.promoted_pending_count ?? 0}`, (queue.promoted_pending_count ?? 0) > 0)}
          ${warnBadge(`stale ${queue.promoted_pending_stale_count ?? 0}`, (queue.promoted_pending_stale_count ?? 0) > 0)}
          ${warnBadge(`reminders ${reminder.pending_count ?? 0}`, (reminder.pending_count ?? 0) > 0)}
          ${badge(`reports ${(data.burn_in_reports || []).length}`)}
        </div>
        <div class="next">${esc(data.next_action || "")}</div>
      `));
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
        <div class="next">Action ID: ${esc(a.action_id)}</div>
        <div class="next">Evidence: ${esc(a.evidence_event_id)} / ${esc(a.evidence_timestamp)}</div>
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
      renderList(burnInReports, data.reports, r => item(`
        <div class="line"><span class="title">${esc(r.name)}</span><span class="meta">${esc(r.status)} / ${r.ready_for_live_promotion ? "ready" : "not ready"}</span></div>
        <div class="badge-row">
          ${warnBadge(`queued ${r.queued_count ?? 0}`, (r.queued_count ?? 0) > 0)}
          ${warnBadge(`pending ${r.pending_count ?? 0}`, (r.pending_count ?? 0) > 0)}
          ${warnBadge(`stale ${r.stale_count ?? 0}`, (r.stale_count ?? 0) > 0)}
          ${warnBadge(`noise ${r.noise_candidate_count ?? 0}`, (r.noise_candidate_count ?? 0) > 0)}
        </div>
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
          <button type="button" data-queue-id="${esc(q.queue_id)}" data-queue-status="reviewed">Reviewed</button>
          <button type="button" data-queue-id="${esc(q.queue_id)}" data-queue-status="promoted">Promote</button>
          <button type="button" data-queue-id="${esc(q.queue_id)}" data-queue-status="snoozed">Snooze</button>
          <button type="button" data-queue-id="${esc(q.queue_id)}" data-queue-status="rejected">Reject</button>
        </div>
      `), "No queued import handoff items.");
      importQueue.querySelectorAll("button[data-queue-id]").forEach(button => {
        button.addEventListener("click", () => updateQueueItem(button.dataset.queueId, button.dataset.queueStatus));
      });
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
      await loadImportQueue();
    }
    document.getElementById("refresh").addEventListener("click", load);
    importQueueFilter.addEventListener("change", loadImportQueue);
    document.getElementById("savePackage").addEventListener("click", () => post("/review/save-package"));
    document.getElementById("validatePackage").addEventListener("click", () => post("/review/validate-package"));
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
    return HTMLResponse(REVIEW_HTML)


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
    return {
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
    }


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
    return {
        "status": report["status"],
        "applied": False,
        "review_package": report["review_package"],
        "action_count": len(report["actions"]),
    }


@app.get("/review/packages")
async def review_packages(limit: int = 10) -> dict[str, object]:
    """List recent saved review packages without importing or applying them."""
    return {
        "status": "ok",
        "packages": list_action_review_packages(limit=max(limit, 1)),
        "applied": False,
    }


@app.get("/review/package/{name}")
async def review_package_detail(name: str) -> dict[str, object]:
    """Inspect one saved review package without importing or applying it."""
    return dict(load_action_review_package_detail(name=name))


@app.post("/review/package/{name}/queue")
async def review_queue_package(name: str) -> dict[str, object]:
    """Queue one saved review package for operator-mediated personal-ops import."""
    detail = load_action_review_package_detail(name=name)
    report = run_personal_ops_import_stub(path=Path(detail["path"]), enqueue=True)
    return dict(report)


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
    return {
        "status": "ok",
        "items": list_personal_ops_import_queue(limit=max(limit, 1)),
        "health": queue_health["health"],
        "next_commands": queue_health["next_commands"],
        "outcome_sync_reminder": outcome_sync_reminder,
        "applied": False,
    }


@app.get("/review/burn-in-reports")
async def review_burn_in_reports(limit: int = 10) -> dict[str, object]:
    """List saved queue burn-in reports without applying work."""
    return {
        "status": "ok",
        "reports": list_personal_ops_queue_burn_in_reports(limit=max(limit, 1)),
        "applied": False,
    }


@app.get("/review/burn-in-report/{name}")
async def review_burn_in_report_detail(name: str) -> dict[str, object]:
    """Inspect one saved queue burn-in report without applying work."""
    return dict(load_personal_ops_queue_burn_in_report_detail(name=name))


@app.get("/review/coordination-readiness")
async def review_coordination_readiness(limit: int = 5) -> dict[str, object]:
    """Summarize coordination expansion readiness without applying work."""
    report = await asyncio.to_thread(run_coordination_readiness, limit=max(limit, 1))
    return dict(report)


@app.get("/review/coordination-console")
async def review_coordination_console(hours: int = 2, limit: int = 5) -> dict[str, object]:
    """Return one compact coordination console summary without applying work."""
    report = await asyncio.to_thread(
        run_coordination_console,
        hours=max(hours, 1),
        limit=max(limit, 1),
    )
    return dict(report)


@app.get("/review/outcome-sync-reminder")
async def review_outcome_sync_reminder(
    limit: int = 10,
    stale_after_hours: float = 4.0,
) -> dict[str, object]:
    """Report promoted handoffs that still need outcome sync without applying work."""
    return dict(
        run_personal_ops_outcome_sync_reminder(
            limit=max(limit, 1),
            stale_after_hours=stale_after_hours,
        )
    )


@app.patch("/review/import-queue/{queue_id}")
async def review_update_import_queue(queue_id: str, request: Request) -> dict[str, object]:
    """Update one queued handoff lifecycle state without applying it."""
    body = cast(dict[str, object], await request.json())
    status = body.get("status")
    if not isinstance(status, str):
        return {
            "status": "degraded",
            "queue_id": queue_id,
            "updated": False,
            "item": None,
            "next_action": "Choose a lifecycle status before updating this queue item.",
            "error": "missing status",
        }
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
    return dict(report)


@app.delete("/review/package/{name}")
async def review_delete_package(name: str) -> dict[str, object]:
    """Delete one saved review package without importing or applying it."""
    global _latest_review_package_path, _latest_review_package_validation_status
    report = delete_action_review_package(name=name)
    if report["deleted"] and _latest_review_package_path == report["path"]:
        _latest_review_package_path = None
        _latest_review_package_validation_status = None
    return dict(report)


@app.post("/review/validate-package")
async def review_validate_package() -> dict[str, object]:
    """Validate the latest staged review package without importing or applying it."""
    global _latest_review_package_validation_status
    package_path = get_latest_review_package_path()
    if package_path is None:
        packages = list_action_review_packages(limit=1)
        if packages:
            package_path = packages[0]["path"]
            global _latest_review_package_path
            _latest_review_package_path = package_path
    if package_path is None:
        _latest_review_package_validation_status = "not_found"
        return {
            "status": "degraded",
            "applied": False,
            "error": "no review package has been saved in this server session",
            "review_package": _review_package_state("not_found"),
        }
    validation = validate_action_package(Path(package_path))
    _latest_review_package_validation_status = validation["status"]
    return {
        "status": validation["status"],
        "applied": False,
        "validation": validation,
        "review_package": _review_package_state(validation["status"]),
    }
