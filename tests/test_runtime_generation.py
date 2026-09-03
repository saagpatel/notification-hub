from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from notification_hub.runtime_generation import _launcher_source, tree_digest


def test_tree_digest_changes_with_content_and_mode(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    payload = app / "payload"
    payload.write_text("one", encoding="utf-8")
    first = tree_digest(app)
    payload.write_text("two", encoding="utf-8")
    second = tree_digest(app)
    payload.chmod(0o700)
    third = tree_digest(app)
    assert first != second
    assert second != third


def test_generated_launcher_verifies_installed_tree_and_detects_tamper(tmp_path: Path) -> None:
    state = tmp_path / "state"
    release = state / "releases" / "fixture-generation"
    app = release / "app"
    python_path = app / ".venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.symlink_to(Path(sys.executable).resolve())
    payload = app / "payload.txt"
    payload.write_text("fixture", encoding="utf-8")
    launcher = release / "bin" / "notification-hub-daemon"
    launcher.parent.mkdir()
    launcher.write_text(_launcher_source(Path(sys.executable).resolve()), encoding="utf-8")
    launcher.chmod(0o700)
    manifest = {
        "generation_id": "fixture-generation",
        "source": {"commit": "a" * 40, "tree": "b" * 40},
        "interpreter": {
            "resolved_path": str(python_path.resolve()),
            "sha256": hashlib.sha256(python_path.resolve().read_bytes()).hexdigest(),
        },
        "app_tree_sha256": tree_digest(app),
    }
    manifest_bytes = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    (release / "generation.json").write_bytes(manifest_bytes)
    (release / "generation.sha256").write_text(
        hashlib.sha256(manifest_bytes).hexdigest() + "\n", encoding="utf-8"
    )

    verified = subprocess.run(
        [str(launcher), "--verify-only"], check=False, capture_output=True, text=True
    )
    assert verified.returncode == 0, verified.stderr
    identity = json.loads(verified.stdout)
    assert identity["generation_id"] == "fixture-generation"

    payload.write_text("tampered", encoding="utf-8")
    rejected = subprocess.run(
        [str(launcher), "--verify-only"], check=False, capture_output=True, text=True
    )
    assert rejected.returncode != 0
    assert "tree digest mismatch" in rejected.stderr


def test_launchagent_uses_stable_generation_and_owner_private_umask() -> None:
    source = Path("ops/launchagents/com.saagar.notification-hub.plist").read_text(
        encoding="utf-8"
    )
    assert "current/bin/notification-hub-daemon" in source
    assert "current/app" in source
    assert "<integer>63</integer>" in source
    assert "/opt/homebrew/bin/uv</string>" not in source
    assert "NOTIFICATION_HUB_BRIDGE_CURSOR_ENABLED" in source
    assert "NOTIFICATION_HUB_PRESERVE_HISTORY" in source
