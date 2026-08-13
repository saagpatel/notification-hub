from __future__ import annotations

import os
from pathlib import Path

import pytest

from notification_hub import config as config_mod
from notification_hub.private_storage import (
    PrivateStorageError,
    ensure_private_directory,
    ensure_private_regular_file,
    ensure_runtime_storage_roots,
)


def test_exact_private_paths_are_tightened(tmp_path: Path) -> None:
    directory = tmp_path / "state"
    directory.mkdir(mode=0o755)
    payload = directory / "payload.sqlite3"
    payload.write_text("fixture", encoding="utf-8")
    payload.chmod(0o644)

    ensure_private_directory(directory)
    ensure_private_regular_file(payload)

    assert directory.stat().st_mode & 0o777 == 0o700
    assert payload.stat().st_mode & 0o777 == 0o600


def test_private_file_refuses_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("fixture", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(target)

    with pytest.raises(PrivateStorageError, match="regular file"):
        ensure_private_regular_file(link)


def test_runtime_roots_cover_live_database_sidecars_and_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = tmp_path / "events"
    logs = tmp_path / "logs"
    monkeypatch.setattr(config_mod, "EVENTS_DIR", events)
    monkeypatch.setattr(config_mod, "EVENTS_LOG", events / "events.jsonl")
    monkeypatch.setattr(config_mod, "PRODUCER_OUTBOX_DB", events / "producer.sqlite3")
    monkeypatch.setattr(config_mod, "DURABLE_INBOX_DB", events / "inbox.sqlite3")
    monkeypatch.setattr(config_mod, "DAEMON_LOG_DIR", logs)
    monkeypatch.setattr(config_mod, "DAEMON_STDOUT_LOG", logs / "stdout.log")
    monkeypatch.setattr(config_mod, "DAEMON_STDERR_LOG", logs / "stderr.log")
    events.mkdir(mode=0o755)
    logs.mkdir(mode=0o755)
    for path in (
        config_mod.EVENTS_LOG,
        config_mod.PRODUCER_OUTBOX_DB,
        config_mod.DURABLE_INBOX_DB,
        Path(f"{config_mod.DURABLE_INBOX_DB}-wal"),
        Path(f"{config_mod.DURABLE_INBOX_DB}-shm"),
        config_mod.DAEMON_STDOUT_LOG,
        config_mod.DAEMON_STDERR_LOG,
    ):
        path.write_text("fixture", encoding="utf-8")
        path.chmod(0o644)

    ensure_runtime_storage_roots()

    assert events.stat().st_mode & 0o777 == 0o700
    assert logs.stat().st_mode & 0o777 == 0o700
    for path in (
        config_mod.EVENTS_LOG,
        config_mod.PRODUCER_OUTBOX_DB,
        config_mod.DURABLE_INBOX_DB,
        Path(f"{config_mod.DURABLE_INBOX_DB}-wal"),
        Path(f"{config_mod.DURABLE_INBOX_DB}-shm"),
        config_mod.DAEMON_STDOUT_LOG,
        config_mod.DAEMON_STDERR_LOG,
    ):
        assert os.stat(path).st_mode & 0o777 == 0o600
