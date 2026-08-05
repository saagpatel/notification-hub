"""Locate the shared Codex-harness irreversible-action envelope module.

The envelope module lives in the Codex harness (`~/.codex/scripts/security/`),
outside this repository and not version-controlled alongside it. Tests that
exercise the authorization seam need its real path.

Resolution order:
  1. ``NOTIFICATION_HUB_ENVELOPE_MODULE`` if set (absolute or ``~``-relative).
  2. ``~/.codex/scripts/security/irreversible_action_envelope.py``.

Both are machine-relative rather than hardcoded to one operator's home, so the
suite is runnable by anyone with the harness installed and skips cleanly for
anyone without it instead of failing on a missing path.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

ENVELOPE_MODULE_ENV = "NOTIFICATION_HUB_ENVELOPE_MODULE"

DEFAULT_ENVELOPE_MODULE = (
    Path.home() / ".codex" / "scripts" / "security" / "irreversible_action_envelope.py"
)


def resolve_envelope_module() -> Path:
    """Return the configured envelope-module path without asserting it exists."""
    override = os.environ.get(ENVELOPE_MODULE_ENV)
    if override:
        return Path(override).expanduser()
    return DEFAULT_ENVELOPE_MODULE


SHARED_ENVELOPE_MODULE = resolve_envelope_module()

requires_shared_envelope_module = pytest.mark.skipif(
    not SHARED_ENVELOPE_MODULE.is_file(),
    reason=(
        f"shared envelope module not found at {SHARED_ENVELOPE_MODULE}; "
        f"set {ENVELOPE_MODULE_ENV} to its location"
    ),
)
