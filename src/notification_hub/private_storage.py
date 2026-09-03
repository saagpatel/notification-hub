"""Owner-private storage invariants for Notification Hub runtime state."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from notification_hub import config as config_mod

PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
PRIVATE_EXECUTABLE_MODE = 0o700


class PrivateStorageError(RuntimeError):
    """Raised when a private storage path cannot be secured safely."""


def ensure_private_directory(path: Path) -> None:
    """Create or tighten one exact directory, refusing links and non-directories."""
    if path.is_symlink():
        raise PrivateStorageError(f"private directory must not be a symlink: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIRECTORY_MODE)
    current = path.lstat()
    if not stat.S_ISDIR(current.st_mode):
        raise PrivateStorageError(f"private directory is not a directory: {path}")
    os.chmod(path, PRIVATE_DIRECTORY_MODE)
    observed = stat.S_IMODE(path.lstat().st_mode)
    if observed != PRIVATE_DIRECTORY_MODE:
        raise PrivateStorageError(
            f"private directory mode mismatch for {path}: {oct(observed)}"
        )


def ensure_private_regular_file(path: Path, *, executable: bool = False) -> None:
    """Tighten one existing regular file without following a symlink."""
    if not path.exists() and not path.is_symlink():
        return
    current = path.lstat()
    if stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode):
        raise PrivateStorageError(f"private file must be a regular file: {path}")
    expected = PRIVATE_EXECUTABLE_MODE if executable else PRIVATE_FILE_MODE
    os.chmod(path, expected, follow_symlinks=False)
    observed = stat.S_IMODE(path.lstat().st_mode)
    if observed != expected:
        raise PrivateStorageError(f"private file mode mismatch for {path}: {oct(observed)}")


def protect_sqlite_family(path: Path) -> None:
    """Protect an exact SQLite database and its two standard sidecars."""
    ensure_private_directory(path.parent)
    for member in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        ensure_private_regular_file(member)


def ensure_runtime_storage_roots() -> None:
    """Secure the exact live roots and known live files before runtime startup."""
    ensure_private_directory(config_mod.EVENTS_DIR)
    ensure_private_directory(config_mod.DAEMON_LOG_DIR)
    for path in (
        config_mod.EVENTS_LOG,
        config_mod.PRODUCER_OUTBOX_DB,
        config_mod.DAEMON_STDOUT_LOG,
        config_mod.DAEMON_STDERR_LOG,
    ):
        ensure_private_regular_file(path)
    protect_sqlite_family(config_mod.DURABLE_INBOX_DB)
