from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from core.application.config_store import ConfigStore
from core.persistence.database import Database
from core.private_storage import harden_private_tree
from core.providers.credentials import CredentialStore
from core.sessions.store import SessionStore

pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="Windows protects the user profile with ACLs instead of POSIX modes",
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_runtime_state_is_created_with_user_only_permissions(tmp_path: Path) -> None:
    sessions = tmp_path / "home" / "sessions"
    store = SessionStore(sessions)
    session = store.create_session(title="private")
    store.append_message(session.session_id, "user", "private transcript")
    store.attach_task(
        session.session_id,
        "task-1",
        task_kind="code",
        task_dir=tmp_path,
    )
    store.update_settings(session.session_id, model="example/model")

    database = Database(tmp_path / "home" / "state" / "deepcode.sqlite3")
    database.initialize()
    ConfigStore(tmp_path / "home" / "deepcode_config.json").mutate(lambda _: {})
    CredentialStore(tmp_path / "home" / "credentials.json").set(
        "example",
        "secret",
    )

    session_directory = sessions / session.session_id
    for directory in (
        tmp_path / "home",
        sessions,
        sessions / ".locks",
        session_directory,
        database.path.parent,
    ):
        assert _mode(directory) == 0o700

    for path in (
        sessions / "index.db",
        session_directory / "session.jsonl",
        session_directory / "tasks.jsonl",
        session_directory / "settings.json",
        database.path,
        tmp_path / "home" / "deepcode_config.json",
        tmp_path / "home" / "credentials.json",
    ):
        assert _mode(path) == 0o600


def test_existing_private_tree_permissions_are_repaired(tmp_path: Path) -> None:
    root = tmp_path / "legacy-sessions"
    session_directory = root / "legacy"
    session_directory.mkdir(parents=True)
    transcript = session_directory / "session.jsonl"
    transcript.write_text("legacy\n", encoding="utf-8")
    os.chmod(root, 0o755)
    os.chmod(session_directory, 0o755)
    os.chmod(transcript, 0o644)

    SessionStore(root, use_index=False)

    assert _mode(root) == 0o700
    assert _mode(session_directory) == 0o700
    assert _mode(transcript) == 0o600


def test_credential_read_repairs_historical_mode_before_returning_value(
    tmp_path: Path,
) -> None:
    path = tmp_path / "home" / "credentials.json"
    path.parent.mkdir()
    path.write_text(
        '{"version": 1, "connections": {"example": "secret"}}\n',
        encoding="utf-8",
    )
    os.chmod(path, 0o644)

    assert CredentialStore(path).get("example") == "secret"
    assert _mode(path) == 0o600


def test_credential_store_rejects_symlink_and_retains_path_identity(
    tmp_path: Path,
) -> None:
    external = tmp_path / "external.json"
    external.write_text(
        '{"version": 1, "connections": {"example": "outside"}}\n',
        encoding="utf-8",
    )
    link = tmp_path / "credentials.json"
    link.symlink_to(external)
    store = CredentialStore(link)

    assert store.path == link.absolute()
    with pytest.raises(OSError):
        store.get("example")
    with pytest.raises(OSError):
        store.set("example", "replacement")
    assert "outside" in external.read_text(encoding="utf-8")
    assert "replacement" not in external.read_text(encoding="utf-8")


def test_credential_store_rejects_symlinked_mutation_lock(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    external_lock = tmp_path / "external.lock"
    external_lock.write_text("unchanged", encoding="utf-8")
    lock_link = path.with_suffix(path.suffix + ".lock")
    lock_link.symlink_to(external_lock)

    with pytest.raises(OSError):
        CredentialStore(path).set("example", "secret")

    assert external_lock.read_text(encoding="utf-8") == "unchanged"
    assert not path.exists()


def test_credential_store_rejects_non_regular_file(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    path.mkdir()

    with pytest.raises(OSError):
        CredentialStore(path).get("example")


def test_private_tree_repair_does_not_follow_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "private"
    root.mkdir()
    external = tmp_path / "external.txt"
    external.write_text("outside", encoding="utf-8")
    os.chmod(external, 0o644)
    (root / "link").symlink_to(external)

    harden_private_tree(root)

    assert _mode(root) == 0o700
    assert _mode(external) == 0o644


def test_suite_redirects_all_user_state_to_the_test_directory(
    tmp_path: Path,
) -> None:
    home = Path(os.environ["DEEPCODE_HOME"])
    sessions = Path(os.environ["DEEPCODE_SESSIONS_DIR"])

    assert home == tmp_path / "deepcode-home"
    assert sessions == home / "sessions"
