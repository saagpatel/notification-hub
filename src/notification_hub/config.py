"""Configuration constants, paths, and loadable policy settings."""

from __future__ import annotations

import logging
import subprocess
import time
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeGuard, cast

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 9199

EVENTS_DIR = Path.home() / ".local" / "share" / "notification-hub"
EVENTS_LOG = EVENTS_DIR / "events.jsonl"
APP_CONFIG_DIR = Path.home() / ".config" / "notification-hub"
POLICY_CONFIG = APP_CONFIG_DIR / "config.toml"
EXAMPLE_POLICY_CONFIG = Path(__file__).resolve().parents[2] / "config" / "policy.example.toml"
LAUNCH_AGENT_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.saagar.notification-hub.plist"

BRIDGE_FILE = Path.home() / ".claude" / "projects" / "-Users-d" / "memory" / "claude_ai_context.md"

# Sections in the bridge file that trigger events when changed
WATCHED_SECTIONS = (
    "## Recent Claude Code Activity",
    "## Recent Codex Activity",
)

# Keychain service/account for Slack webhook URL
KEYCHAIN_SERVICE = "slack-webhook"
KEYCHAIN_ACCOUNT = "notification-hub"
MISSING_WEBHOOK_RECHECK_SECONDS = 60.0

_UNSET = object()
_cached_webhook_url: str | None | object = _UNSET
_cached_webhook_checked_at: float | None = None
_cached_policy: PolicyConfig | None = None
_cached_policy_mtime_ns: int | None = None

# Default classifier policy
DEFAULT_URGENT_KEYWORDS: tuple[str, ...] = (
    "verification fail",
    "test regression",
    "eval degradation",
    "approval needed",
    "approval required",
    "can_auto_archive=false",
    "security finding",
    "security audit",
    "needs approval",
    "action required",
    "runtime issue",
)

DEFAULT_NORMAL_KEYWORDS: tuple[str, ...] = (
    "session complete",
    "automation report",
    "milestone",
    "bridge sync",
    "[shipped]",
    "phase complete",
    "all phases complete",
    "v1.0 done",
    "deployed",
    "released",
    "submitted to app store",
    "published to github",
    "merged to main",
    "production deploy",
)

DEFAULT_INFO_KEYWORDS: tuple[str, ...] = (
    "can_auto_archive=true",
    "bridge file read",
    "status update",
    "routine check",
)

# Default suppression policy
DEFAULT_QUIET_START_HOUR = 23
DEFAULT_QUIET_END_HOUR = 7
DEFAULT_DEDUP_WINDOW_MINUTES = 30
DEFAULT_MAX_PUSH_PER_HOUR = 5
DEFAULT_MAX_SLACK_PER_HOUR = 20
DEFAULT_MAX_OVERFLOW_BUFFER = 500
DEFAULT_MAX_QUIET_QUEUE = 200
DEFAULT_RETENTION_ENABLED = True
DEFAULT_RETENTION_INTERVAL_MINUTES = 60
DEFAULT_RETENTION_MAX_EVENTS = 2000
DEFAULT_RETENTION_KEEP_ARCHIVES = 10
VALID_LEVELS = frozenset(("urgent", "normal", "info"))
VALID_SOURCES = frozenset(("codex", "cc", "claude_ai", "bridge_watcher"))


@dataclass(frozen=True)
class ClassificationPolicy:
    urgent_keywords: tuple[str, ...] = DEFAULT_URGENT_KEYWORDS
    normal_keywords: tuple[str, ...] = DEFAULT_NORMAL_KEYWORDS
    info_keywords: tuple[str, ...] = DEFAULT_INFO_KEYWORDS


@dataclass(frozen=True)
class SuppressionPolicy:
    quiet_start_hour: int = DEFAULT_QUIET_START_HOUR
    quiet_end_hour: int = DEFAULT_QUIET_END_HOUR
    dedup_window_minutes: int = DEFAULT_DEDUP_WINDOW_MINUTES
    max_push_per_hour: int = DEFAULT_MAX_PUSH_PER_HOUR
    max_slack_per_hour: int = DEFAULT_MAX_SLACK_PER_HOUR
    max_overflow_buffer: int = DEFAULT_MAX_OVERFLOW_BUFFER
    max_quiet_queue: int = DEFAULT_MAX_QUIET_QUEUE


@dataclass(frozen=True)
class RetentionPolicy:
    enabled: bool = DEFAULT_RETENTION_ENABLED
    interval_minutes: int = DEFAULT_RETENTION_INTERVAL_MINUTES
    max_events: int = DEFAULT_RETENTION_MAX_EVENTS
    keep_archives: int = DEFAULT_RETENTION_KEEP_ARCHIVES


@dataclass(frozen=True)
class RoutingRule:
    source: str | None = None
    project: str | None = None
    project_prefix: str | None = None
    title_contains: str | None = None
    body_contains: str | None = None
    text_contains: str | None = None
    force_level: str | None = None
    disable_push: bool = False
    disable_slack: bool = False
    continue_matching: bool = False


@dataclass(frozen=True)
class RoutingPolicy:
    rules: tuple[RoutingRule, ...] = ()


@dataclass(frozen=True)
class PolicyConfig:
    path: Path = POLICY_CONFIG
    config_found: bool = False
    load_error: str | None = None
    classification: ClassificationPolicy = field(default_factory=ClassificationPolicy)
    suppression: SuppressionPolicy = field(default_factory=SuppressionPolicy)
    retention: RetentionPolicy = field(default_factory=RetentionPolicy)
    routing: RoutingPolicy = field(default_factory=RoutingPolicy)


_CLASSIFICATION_PRECEDENCE: tuple[tuple[str, str], ...] = (
    ("urgent", "urgent"),
    ("info", "info"),
    ("normal", "normal"),
)


def _is_cached_webhook_url(value: str | None | object) -> TypeGuard[str | None]:
    """Narrow the cache sentinel away for static type checkers."""
    return isinstance(value, str) or value is None


def _as_mapping(value: object) -> Mapping[str, object]:
    """Return a mapping view when possible, otherwise an empty mapping."""
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return {}


def _as_string_tuple(value: object, fallback: tuple[str, ...]) -> tuple[str, ...]:
    """Coerce a config list of strings into an immutable tuple."""
    if not isinstance(value, list):
        return fallback

    items: list[str] = []
    for item in cast(list[object], value):
        if isinstance(item, str):
            stripped = item.strip().lower()
            if stripped:
                items.append(stripped)
    return tuple(items) if items else fallback


def _as_int(
    value: object,
    fallback: int,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    """Coerce a config value to a bounded int, falling back on invalid input."""
    if not isinstance(value, int):
        return fallback

    candidate = value
    if candidate < minimum:
        return fallback
    if maximum is not None and candidate > maximum:
        return fallback
    return candidate


def _as_bool(value: object, fallback: bool = False) -> bool:
    """Coerce config booleans safely."""
    if isinstance(value, bool):
        return value
    return fallback


def _as_optional_string(value: object) -> str | None:
    """Return a stripped string or None."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped if stripped else None


def _as_optional_lower_string(value: object) -> str | None:
    """Return a stripped lowercase string or None."""
    candidate = _as_optional_string(value)
    return None if candidate is None else candidate.lower()


def _as_optional_choice(value: object, valid: frozenset[str]) -> str | None:
    """Return a lowercase string when it matches the allowed choices."""
    candidate = _as_optional_string(value)
    if candidate is None:
        return None
    lowered = candidate.lower()
    return lowered if lowered in valid else None


def _parse_routing_rules(value: object) -> tuple[RoutingRule, ...]:
    """Parse routing rules from `[[routing.rules]]` TOML input."""
    if not isinstance(value, list):
        return ()

    rules: list[RoutingRule] = []
    for item in cast(list[object], value):
        raw_rule = _as_mapping(item)
        source = _as_optional_choice(raw_rule.get("source"), VALID_SOURCES)
        project = _as_optional_string(raw_rule.get("project"))
        project_prefix = _as_optional_string(raw_rule.get("project_prefix"))
        title_contains = _as_optional_lower_string(raw_rule.get("title_contains"))
        body_contains = _as_optional_lower_string(raw_rule.get("body_contains"))
        text_contains = _as_optional_lower_string(raw_rule.get("text_contains"))
        if (
            source is None
            and project is None
            and project_prefix is None
            and title_contains is None
            and body_contains is None
            and text_contains is None
        ):
            continue

        rules.append(
            RoutingRule(
                source=source,
                project=project,
                project_prefix=project_prefix,
                title_contains=title_contains,
                body_contains=body_contains,
                text_contains=text_contains,
                force_level=_as_optional_choice(raw_rule.get("force_level"), VALID_LEVELS),
                disable_push=_as_bool(raw_rule.get("disable_push")),
                disable_slack=_as_bool(raw_rule.get("disable_slack")),
                continue_matching=_as_bool(raw_rule.get("continue_matching")),
            )
        )
    return tuple(rules)


def _string_constraint_shadowed_by(
    previous_exact: str | None,
    previous_prefix: str | None,
    current_exact: str | None,
    current_prefix: str | None,
) -> bool:
    """Return whether a previous exact/prefix constraint covers the current one."""
    if previous_exact is not None:
        return current_exact == previous_exact
    if previous_prefix is not None:
        if current_exact is not None:
            return current_exact.startswith(previous_prefix)
        if current_prefix is not None:
            return current_prefix.startswith(previous_prefix)
        return False
    return True


def _contains_constraint_shadowed_by(previous: str | None, current: str | None) -> bool:
    """Return whether a previous contains constraint covers the current one."""
    if previous is None:
        return True
    if current is None:
        return False
    return current.find(previous) >= 0


def _routing_rule_shadowed_by(previous: RoutingRule, current: RoutingRule) -> bool:
    """Return whether an earlier rule matches every event the later rule could match."""
    return (
        _string_constraint_shadowed_by(previous.source, None, current.source, None)
        and _string_constraint_shadowed_by(
            previous.project,
            previous.project_prefix,
            current.project,
            current.project_prefix,
        )
        and _contains_constraint_shadowed_by(previous.title_contains, current.title_contains)
        and _contains_constraint_shadowed_by(previous.body_contains, current.body_contains)
        and _contains_constraint_shadowed_by(previous.text_contains, current.text_contains)
    )


def analyze_policy_config(policy: PolicyConfig | None = None) -> tuple[str, ...]:
    """Return human-readable warnings for overlapping or ineffective policy rules."""
    current_policy = get_policy_config() if policy is None else policy
    warnings: list[str] = []
    total_rules = len(current_policy.routing.rules)

    if not current_policy.retention.enabled:
        warnings.append(
            "automatic retention is disabled; the live event log will only prune when you run retention manually"
        )

    keyword_to_first_group: dict[str, str] = {}
    for group_name, attribute_name in _CLASSIFICATION_PRECEDENCE:
        for keyword in getattr(current_policy.classification, f"{attribute_name}_keywords"):
            first_group = keyword_to_first_group.setdefault(keyword, group_name)
            if first_group != group_name:
                warnings.append(
                    f"classifier keyword '{keyword}' appears in both {first_group} and {group_name}; "
                    f"{first_group} wins first"
                )

    prior_rules: list[RoutingRule] = []
    for index, rule in enumerate(current_policy.routing.rules, start=1):
        if rule.project is not None and rule.project_prefix is not None:
            warnings.append(
                f"routing rule {index} sets both project and project_prefix; project already implies the prefix"
            )

        if rule.force_level is None and not rule.disable_push and not rule.disable_slack:
            warnings.append(
                f"routing rule {index} does not change level or delivery behavior"
            )

        if rule.continue_matching and index == total_rules:
            warnings.append(
                f"routing rule {index} sets continue_matching but there is no later rule to continue into"
            )

        for prior_index, prior_rule in enumerate(prior_rules, start=1):
            if _routing_rule_shadowed_by(prior_rule, rule):
                warnings.append(
                    f"routing rule {index} is shadowed by earlier rule {prior_index} and will never match"
                )
                break
        if not rule.continue_matching:
            prior_rules.append(rule)

    return tuple(warnings)


def _build_policy_config(
    raw_config: object,
    *,
    path: Path,
    config_found: bool,
    load_error: str | None = None,
) -> PolicyConfig:
    """Build a validated policy object from TOML-like input."""
    root = _as_mapping(raw_config)
    classifier = _as_mapping(root.get("classifier"))
    suppression = _as_mapping(root.get("suppression"))
    retention = _as_mapping(root.get("retention"))
    routing = _as_mapping(root.get("routing"))

    return PolicyConfig(
        path=path,
        config_found=config_found,
        load_error=load_error,
        classification=ClassificationPolicy(
            urgent_keywords=_as_string_tuple(
                classifier.get("urgent_keywords"),
                DEFAULT_URGENT_KEYWORDS,
            ),
            normal_keywords=_as_string_tuple(
                classifier.get("normal_keywords"),
                DEFAULT_NORMAL_KEYWORDS,
            ),
            info_keywords=_as_string_tuple(
                classifier.get("info_keywords"),
                DEFAULT_INFO_KEYWORDS,
            ),
        ),
        suppression=SuppressionPolicy(
            quiet_start_hour=_as_int(
                suppression.get("quiet_start_hour"),
                DEFAULT_QUIET_START_HOUR,
                minimum=0,
                maximum=23,
            ),
            quiet_end_hour=_as_int(
                suppression.get("quiet_end_hour"),
                DEFAULT_QUIET_END_HOUR,
                minimum=0,
                maximum=23,
            ),
            dedup_window_minutes=_as_int(
                suppression.get("dedup_window_minutes"),
                DEFAULT_DEDUP_WINDOW_MINUTES,
                minimum=1,
            ),
            max_push_per_hour=_as_int(
                suppression.get("max_push_per_hour"),
                DEFAULT_MAX_PUSH_PER_HOUR,
                minimum=1,
            ),
            max_slack_per_hour=_as_int(
                suppression.get("max_slack_per_hour"),
                DEFAULT_MAX_SLACK_PER_HOUR,
                minimum=1,
            ),
            max_overflow_buffer=_as_int(
                suppression.get("max_overflow_buffer"),
                DEFAULT_MAX_OVERFLOW_BUFFER,
                minimum=1,
            ),
            max_quiet_queue=_as_int(
                suppression.get("max_quiet_queue"),
                DEFAULT_MAX_QUIET_QUEUE,
                minimum=1,
            ),
        ),
        retention=RetentionPolicy(
            enabled=_as_bool(retention.get("enabled"), DEFAULT_RETENTION_ENABLED),
            interval_minutes=_as_int(
                retention.get("interval_minutes"),
                DEFAULT_RETENTION_INTERVAL_MINUTES,
                minimum=1,
            ),
            max_events=_as_int(
                retention.get("max_events"),
                DEFAULT_RETENTION_MAX_EVENTS,
                minimum=1,
            ),
            keep_archives=_as_int(
                retention.get("keep_archives"),
                DEFAULT_RETENTION_KEEP_ARCHIVES,
                minimum=1,
            ),
        ),
        routing=RoutingPolicy(rules=_parse_routing_rules(routing.get("rules"))),
    )


def get_policy_config() -> PolicyConfig:
    """Load the optional policy config file, falling back to safe built-in defaults."""
    global _cached_policy, _cached_policy_mtime_ns

    try:
        stat = POLICY_CONFIG.stat()
    except FileNotFoundError:
        if _cached_policy is None or _cached_policy.config_found:
            _cached_policy = PolicyConfig(path=POLICY_CONFIG, config_found=False, load_error=None)
        _cached_policy_mtime_ns = None
        return _cached_policy

    if _cached_policy is not None and _cached_policy_mtime_ns == stat.st_mtime_ns:
        return _cached_policy

    try:
        with POLICY_CONFIG.open("rb") as handle:
            raw_config = tomllib.load(handle)
        _cached_policy = _build_policy_config(
            raw_config,
            path=POLICY_CONFIG,
            config_found=True,
            load_error=None,
        )
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.warning("Failed to load policy config %s: %s", POLICY_CONFIG, exc)
        _cached_policy = PolicyConfig(
            path=POLICY_CONFIG,
            config_found=True,
            load_error=str(exc),
        )

    _cached_policy_mtime_ns = stat.st_mtime_ns
    return _cached_policy


def clear_policy_cache() -> None:
    """Clear cached policy config. Used for testing."""
    global _cached_policy, _cached_policy_mtime_ns
    _cached_policy = None
    _cached_policy_mtime_ns = None


def get_slack_webhook_url() -> str | None:
    """Read Slack webhook URL from macOS Keychain with a short retry window for missing values."""
    global _cached_webhook_url, _cached_webhook_checked_at
    if isinstance(_cached_webhook_url, str):
        return _cached_webhook_url

    if _cached_webhook_url is None and _cached_webhook_checked_at is not None:
        if (time.monotonic() - _cached_webhook_checked_at) < MISSING_WEBHOOK_RECHECK_SECONDS:
            return None

    if _cached_webhook_url is not _UNSET:
        assert _is_cached_webhook_url(_cached_webhook_url)

    try:
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-a",
                KEYCHAIN_ACCOUNT,
                "-s",
                KEYCHAIN_SERVICE,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        _cached_webhook_checked_at = time.monotonic()
        if result.returncode == 0 and result.stdout.strip():
            _cached_webhook_url = result.stdout.strip()
            logger.info("Slack webhook URL loaded from Keychain")
            assert _is_cached_webhook_url(_cached_webhook_url)
            return _cached_webhook_url
        logger.warning(
            "Slack webhook not found in Keychain (service=%s, account=%s)",
            KEYCHAIN_SERVICE,
            KEYCHAIN_ACCOUNT,
        )
        _cached_webhook_url = None
        return None
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Failed to read Keychain: %s", exc)
        _cached_webhook_url = None
        return None


def clear_webhook_cache() -> None:
    """Clear cached webhook URL. Used for testing."""
    global _cached_webhook_url, _cached_webhook_checked_at
    _cached_webhook_url = _UNSET
    _cached_webhook_checked_at = None


def has_slack_webhook_configured() -> bool:
    """Return whether a Slack webhook is available via Keychain."""
    return get_slack_webhook_url() is not None
