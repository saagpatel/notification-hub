"""Immutable runtime generation installer for the Notification Hub daemon."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GENERATION_SCHEMA = "NotificationHubRuntimeGenerationV1"
INSTALL_RECEIPT_SCHEMA = "NotificationHubRuntimeInstallReceiptV1"
DEFAULT_STATE_ROOT = Path.home() / ".local" / "state" / "notification-hub"
MIN_FREE_BYTES = 2 * 1024 * 1024 * 1024


class RuntimeGenerationError(RuntimeError):
    """Raised when generation staging or activation cannot be proven safe."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_digest(root: Path) -> str:
    """Bind relative names, types, modes, link targets, and regular-file bytes."""
    records: list[str] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISDIR(metadata.st_mode):
            records.append(f"d\0{relative}\0{mode:o}\0")
        elif stat.S_ISLNK(metadata.st_mode):
            records.append(f"l\0{relative}\0{mode:o}\0{os.readlink(path)}")
        elif stat.S_ISREG(metadata.st_mode):
            records.append(f"f\0{relative}\0{mode:o}\0{sha256_file(path)}")
        else:
            raise RuntimeGenerationError(f"unsupported installed file type: {path}")
    return _sha256_bytes("\n".join(records).encode("utf-8"))


def _run(args: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeGenerationError(f"command failed ({args[0]}): {detail}")
    return completed.stdout.strip()


def _git(source: Path, *args: str) -> str:
    return _run(["/usr/bin/git", *args], cwd=source)


def _require_clean_source(source: Path, expected_commit: str | None) -> tuple[str, str]:
    root = Path(_git(source, "rev-parse", "--show-toplevel")).resolve()
    if root != source.resolve():
        raise RuntimeGenerationError(f"source must be the Git root: {source}")
    commit = _git(source, "rev-parse", "HEAD")
    if expected_commit is not None and commit != expected_commit:
        raise RuntimeGenerationError(
            f"source commit mismatch: expected {expected_commit}, observed {commit}"
        )
    if _git(source, "status", "--porcelain=v1", "--untracked-files=all"):
        raise RuntimeGenerationError("source worktree must be clean before staging")
    tree = _git(source, "rev-parse", "HEAD^{tree}")
    return commit, tree


def _tracked_files(source: Path) -> list[Path]:
    completed = subprocess.run(
        ["/usr/bin/git", "ls-files", "-z"],
        cwd=source,
        check=True,
        capture_output=True,
    )
    return [Path(item.decode("utf-8")) for item in completed.stdout.split(b"\0") if item]


def _copy_tracked_source(source: Path, destination: Path) -> None:
    for relative in _tracked_files(source):
        source_path = source / relative
        destination_path = destination / relative
        metadata = source_path.lstat()
        destination_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if stat.S_ISREG(metadata.st_mode):
            shutil.copyfile(source_path, destination_path)
            os.chmod(destination_path, stat.S_IMODE(metadata.st_mode))
        elif stat.S_ISLNK(metadata.st_mode):
            os.symlink(os.readlink(source_path), destination_path)
        else:
            raise RuntimeGenerationError(f"unsupported tracked file type: {relative}")


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _launcher_source(interpreter: Path) -> str:
    return f"#!{interpreter}\n" + '''from __future__ import annotations
import hashlib
import json
import os
import stat
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def tree(root: Path) -> str:
    rows = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISDIR(metadata.st_mode):
            rows.append(f"d\\0{relative}\\0{mode:o}\\0")
        elif stat.S_ISLNK(metadata.st_mode):
            rows.append(f"l\\0{relative}\\0{mode:o}\\0{os.readlink(path)}")
        elif stat.S_ISREG(metadata.st_mode):
            rows.append(f"f\\0{relative}\\0{mode:o}\\0{sha(path)}")
        else:
            raise SystemExit(f"unsupported installed file type: {path}")
    return hashlib.sha256("\\n".join(rows).encode("utf-8")).hexdigest()

release = Path(__file__).resolve().parents[1]
manifest_path = release / "generation.json"
sidecar_path = release / "generation.sha256"
manifest_bytes = manifest_path.read_bytes()
expected_manifest_sha = sidecar_path.read_text(encoding="utf-8").strip()
observed_manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
if observed_manifest_sha != expected_manifest_sha:
    raise SystemExit("generation manifest digest mismatch")
manifest = json.loads(manifest_bytes)
app = release / "app"
observed_tree_sha = tree(app)
if observed_tree_sha != manifest["app_tree_sha256"]:
    raise SystemExit("installed application tree digest mismatch")
python = app / ".venv" / "bin" / "python"
if str(python.resolve()) != manifest["interpreter"]["resolved_path"]:
    raise SystemExit("installed interpreter path mismatch")
if sha(python.resolve()) != manifest["interpreter"]["sha256"]:
    raise SystemExit("installed interpreter digest mismatch")
identity = {
    "schema": "NotificationHubRuntimeIdentityV1",
    "observed_at": datetime.now(UTC).isoformat(),
    "pid": os.getpid(),
    "generation_id": manifest["generation_id"],
    "release_root": str(release),
    "source_commit": manifest["source"]["commit"],
    "source_tree": manifest["source"]["tree"],
    "manifest_sha256": observed_manifest_sha,
    "app_tree_sha256": observed_tree_sha,
    "interpreter": str(python.resolve()),
    "launcher": str(Path(__file__).resolve()),
    "claim_ceiling": "verified installed generation before exec; external process readback is required for loaded-runtime proof"
}
if sys.argv[1:] == ["--verify-only"]:
    print(json.dumps(identity, sort_keys=True))
    raise SystemExit(0)
state_root = release.parents[1]
receipt = state_root / "runtime-identity.json"
fd, temporary_name = tempfile.mkstemp(prefix=".runtime-identity.", dir=state_root)
temporary = Path(temporary_name)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(identity, handle, sort_keys=True, indent=2)
        handle.write("\\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, receipt)
finally:
    if temporary.exists():
        temporary.unlink()
environment = os.environ.copy()
environment["NOTIFICATION_HUB_RUNTIME_GENERATION"] = manifest["generation_id"]
environment["NOTIFICATION_HUB_RUNTIME_MANIFEST"] = str(manifest_path)
environment["PYTHONDONTWRITEBYTECODE"] = "1"
os.execve(str(python), [str(python), "-m", "uvicorn", "notification_hub.server:app", "--host", "127.0.0.1", "--port", "9199"], environment)
'''


def _make_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            continue
        if stat.S_ISDIR(metadata.st_mode):
            os.chmod(path, 0o555)
        elif stat.S_ISREG(metadata.st_mode):
            executable = bool(metadata.st_mode & 0o111)
            os.chmod(path, 0o555 if executable else 0o444)
    os.chmod(root, 0o555)


def _atomic_symlink(root: Path, name: str, target: str) -> None:
    temporary = root / f".{name}.{os.getpid()}"
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    os.symlink(target, temporary)
    os.replace(temporary, root / name)


def install_generation(
    *,
    source: Path,
    state_root: Path,
    uv_executable: Path,
    expected_commit: str | None,
    activate: bool,
) -> dict[str, Any]:
    source = source.resolve()
    state_root = state_root.resolve()
    commit, tree = _require_clean_source(source, expected_commit)
    lock_path = source / "uv.lock"
    project_path = source / "pyproject.toml"
    if not lock_path.is_file() or not project_path.is_file():
        raise RuntimeGenerationError("source must contain uv.lock and pyproject.toml")
    lock_sha = sha256_file(lock_path)
    generation_id = f"{commit[:12]}-{lock_sha[:12]}"
    releases = state_root / "releases"
    staging_root = state_root / "staging"
    receipts = state_root / "installation-receipts"
    for directory in (state_root, releases, staging_root, receipts, state_root / "cache"):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
    if shutil.disk_usage(state_root).free < MIN_FREE_BYTES:
        raise RuntimeGenerationError("insufficient free space for immutable generation staging")
    release = releases / generation_id
    reused = release.is_dir()
    if not reused:
        staging = Path(tempfile.mkdtemp(prefix=f"{generation_id}.", dir=staging_root))
        try:
            app = staging / "app"
            app.mkdir(mode=0o700)
            _copy_tracked_source(source, app)
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(state_root / "build-home"),
                    "UV_CACHE_DIR": str(state_root / "cache"),
                    "UV_LINK_MODE": "copy",
                    "PYTHONDONTWRITEBYTECODE": "1",
                }
            )
            Path(environment["HOME"]).mkdir(parents=True, exist_ok=True, mode=0o700)
            _run(
                [str(uv_executable), "sync", "--frozen", "--no-dev", "--no-editable"],
                cwd=app,
                env=environment,
            )
            interpreter = app / ".venv" / "bin" / "python"
            if not interpreter.exists():
                raise RuntimeGenerationError("staged interpreter is missing")
            _run(
                [str(interpreter), "-c", "import notification_hub"],
                cwd=app,
                env=environment,
            )
            _make_read_only(app)
            app_sha = tree_digest(app)
            manifest: dict[str, Any] = {
                "schema": GENERATION_SCHEMA,
                "generation_id": generation_id,
                "installed_at": datetime.now(UTC).isoformat(),
                "source": {
                    "owner": "notification-hub",
                    "repository": str(source),
                    "commit": commit,
                    "tree": tree,
                    "pyproject_sha256": sha256_file(project_path),
                    "lock_sha256": lock_sha,
                },
                "builder": {
                    "uv_executable": str(uv_executable.resolve()),
                    "uv_sha256": sha256_file(uv_executable.resolve()),
                },
                "interpreter": {
                    "configured_path": "app/.venv/bin/python",
                    "resolved_path": str(interpreter.resolve()),
                    "sha256": sha256_file(interpreter.resolve()),
                },
                "app_tree_sha256": app_sha,
                "launcher": "bin/notification-hub-daemon",
                "rollback_contract": "previous symlink plus retained generation; never replay outcome_unknown work",
            }
            manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
            (staging / "generation.json").write_bytes(manifest_bytes)
            (staging / "generation.sha256").write_text(
                f"{_sha256_bytes(manifest_bytes)}\n", encoding="utf-8"
            )
            launcher_dir = staging / "bin"
            launcher_dir.mkdir(mode=0o700)
            launcher = launcher_dir / "notification-hub-daemon"
            launcher.write_text(_launcher_source(interpreter.resolve()), encoding="utf-8")
            os.chmod(launcher, 0o700)
            _make_read_only(staging)
            os.replace(staging, release)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    launcher = release / "bin" / "notification-hub-daemon"
    verified = json.loads(_run([str(launcher), "--verify-only"], cwd=release))
    if verified.get("generation_id") != generation_id:
        raise RuntimeGenerationError("installed launcher returned the wrong generation")
    before = None
    current = state_root / "current"
    if current.is_symlink():
        before = current.resolve().name
    if activate and before != generation_id:
        if before is not None:
            _atomic_symlink(state_root, "previous", f"releases/{before}")
        _atomic_symlink(state_root, "current", f"releases/{generation_id}")
    receipt: dict[str, Any] = {
        "schema": INSTALL_RECEIPT_SCHEMA,
        "observed_at": datetime.now(UTC).isoformat(),
        "generation_id": generation_id,
        "release_root": str(release),
        "reused_existing_release": reused,
        "activated": activate,
        "previous_generation": before,
        "current_generation": current.resolve().name if current.is_symlink() else None,
        "source_commit": commit,
        "source_tree": tree,
        "external_effects": [],
    }
    receipt_path = receipts / (
        f"{generation_id}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    _write_private_json(receipt_path, receipt)
    receipt["receipt_path"] = str(receipt_path)
    return receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument("--uv", type=Path, default=Path("/opt/homebrew/bin/uv"))
    parser.add_argument("--expected-commit")
    parser.add_argument("--stage-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    receipt = install_generation(
        source=args.source,
        state_root=args.state_root,
        uv_executable=args.uv,
        expected_commit=args.expected_commit,
        activate=not args.stage_only,
    )
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
