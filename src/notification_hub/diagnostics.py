"""Runtime diagnostics and operator-facing doctor checks."""

from __future__ import annotations

from typing import Any, TypedDict, cast

import httpx

import notification_hub.channels as channels_mod
import notification_hub.config as config_mod


class DeliveryStatus(TypedDict):
    push_notifier_available: bool
    slack_webhook_configured: bool


class PathStatus(TypedDict):
    bridge_file_exists: bool
    events_dir_exists: bool
    events_log_exists: bool
    launch_agent_exists: bool


class ConfigStatus(TypedDict):
    path: str
    exists: bool
    load_error: str | None
    routing_rule_count: int
    warning_count: int


class RetentionStatus(TypedDict):
    enabled: bool
    interval_minutes: int
    max_events: int
    keep_archives: int


def _path_exists(path: object) -> bool:
    """Wrapper to keep path checks easy to patch in tests."""
    return bool(getattr(path, "exists")())


def collect_runtime_readiness() -> dict[str, object]:
    """Collect local readiness facts without depending on the running HTTP server."""
    policy = config_mod.get_policy_config()
    delivery: DeliveryStatus = {
        "push_notifier_available": channels_mod.has_push_notifier(),
        "slack_webhook_configured": config_mod.has_slack_webhook_configured(),
    }
    paths: PathStatus = {
        "bridge_file_exists": _path_exists(config_mod.BRIDGE_FILE),
        "events_dir_exists": _path_exists(config_mod.EVENTS_DIR),
        "events_log_exists": _path_exists(config_mod.EVENTS_LOG),
        "launch_agent_exists": _path_exists(config_mod.LAUNCH_AGENT_PLIST),
    }
    config: ConfigStatus = {
        "path": str(policy.path),
        "exists": policy.config_found,
        "load_error": policy.load_error,
        "routing_rule_count": len(policy.routing.rules),
        "warning_count": len(config_mod.analyze_policy_config(policy)),
    }
    retention: RetentionStatus = {
        "enabled": policy.retention.enabled,
        "interval_minutes": policy.retention.interval_minutes,
        "max_events": policy.retention.max_events,
        "keep_archives": policy.retention.keep_archives,
    }
    return {
        "delivery": delivery,
        "paths": paths,
        "config": config,
        "retention": retention,
    }


def collect_doctor_report() -> dict[str, object]:
    """Return a compact local operator report for runtime and config health."""
    readiness = collect_runtime_readiness()
    health_url = f"http://{config_mod.HOST}:{config_mod.PORT}/health/details"

    local_api: dict[str, object]
    try:
        response = httpx.get(health_url, timeout=2.0)
        payload: dict[str, Any] | None = None
        if response.headers.get("content-type", "").startswith("application/json"):
            payload = response.json()
        local_api = {
            "reachable": response.status_code == 200,
            "status_code": response.status_code,
            "url": health_url,
            "payload": payload,
        }
    except httpx.HTTPError as exc:
        local_api = {
            "reachable": False,
            "status_code": None,
            "url": health_url,
            "error": str(exc),
        }

    paths = cast(PathStatus, readiness["paths"])
    delivery = cast(DeliveryStatus, readiness["delivery"])
    config = cast(ConfigStatus, readiness["config"])

    checks = {
        "local_api_healthy": bool(local_api["reachable"]),
        "launch_agent_present": bool(paths["launch_agent_exists"]),
        "bridge_file_present": bool(paths["bridge_file_exists"]),
        "push_notifier_available": bool(delivery["push_notifier_available"]),
        "slack_configured": bool(delivery["slack_webhook_configured"]),
        "policy_load_ok": config["load_error"] is None,
    }
    overall_status = "ok" if all(checks.values()) else "degraded"

    return {
        "status": overall_status,
        "checks": checks,
        "local_api": local_api,
        **readiness,
    }
