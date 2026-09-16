from __future__ import annotations

import json
from pathlib import Path

import pytest

from app_server.service_state import ServiceFiles
from app_server.state_backup import StatePaths, create_snapshot, restore_snapshot
from core.application.application_lease import ApplicationLease
from core.persistence.database import Database
from core.persistence.migrations import LATEST_SCHEMA_VERSION, MigrationError
from core.providers.credentials import CredentialStore
from core.private_storage import atomic_write_private_json
from core.sessions.store import SessionStore


def state(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    monkeypatch.delenv("DEEPCODE_SESSIONS_DIR", raising=False)
    paths = StatePaths.current(ServiceFiles(home / "state" / "state.sqlite3"))
    db = Database(paths.database)
    db.initialize()
    with db.transaction() as connection:
        connection.execute("CREATE TABLE snapshot_probe(value TEXT)")
        connection.execute("INSERT INTO snapshot_probe VALUES ('before')")
    store = SessionStore(paths.sessions, use_index=False)
    session = store.create_session(title="Before")
    atomic_write_private_json(
        paths.config, {"agents": {"defaults": {"model": "before"}}}
    )
    CredentialStore(paths.credentials).set("route", "private-before")
    atomic_write_private_json(
        paths.revisions / "old.json", {"privateHeader": "snapshot-header"}
    )
    return paths, db, store, session


def test_offline_snapshot_and_restore_keep_the_complete_state_and_pre_restore_copy(
    tmp_path, monkeypatch
):
    paths, db, store, session = state(tmp_path, monkeypatch)
    snapshot = tmp_path / "snapshot"
    result = create_snapshot(paths, snapshot)
    assert result["fileCount"] >= 5
    assert "private-before" not in json.dumps(result)
    with db.transaction() as connection:
        connection.execute("UPDATE snapshot_probe SET value='after'")
    CredentialStore(paths.credentials).set("route", "private-after")
    (paths.sessions / session.session_id / "settings.json").write_text('{"after":true}')
    atomic_write_private_json(paths.revisions / "new.json", {"later": True})
    restored = restore_snapshot(paths, snapshot, replace_data=True)
    with db.read() as connection:
        assert (
            connection.execute("SELECT value FROM snapshot_probe").fetchone()[0]
            == "before"
        )
    assert CredentialStore(paths.credentials).get("route") == "private-before"
    assert not (paths.sessions / session.session_id / "settings.json").exists()
    assert not (paths.revisions / "new.json").exists()
    assert Path(restored["beforeRestore"], "manifest.json").is_file()
    assert not db.restore_marker.exists()
    restore_snapshot(paths, Path(restored["beforeRestore"]), replace_data=True)
    assert CredentialStore(paths.credentials).get("route") == "private-after"


def test_snapshot_rejects_live_application_and_session_owners(tmp_path, monkeypatch):
    paths, _db, store, session = state(tmp_path, monkeypatch)
    lease = ApplicationLease.acquire(paths.database)
    lease.downgrade()
    try:
        with pytest.raises(ValueError, match="in use"):
            create_snapshot(paths, tmp_path / "snapshot")
    finally:
        lease.close()
    activity = store.acquire_activity_lease(session.session_id)
    try:
        with pytest.raises(ValueError, match="in use"):
            create_snapshot(paths, tmp_path / "snapshot")
    finally:
        activity.close()
    assert not (tmp_path / "snapshot").exists()


def test_restore_rejects_tampering_before_modifying_current_state(
    tmp_path, monkeypatch
):
    paths, db, _store, _session = state(tmp_path, monkeypatch)
    snapshot = tmp_path / "snapshot"
    create_snapshot(paths, snapshot)
    (snapshot / "config.json").write_text("{}")
    before = paths.config.read_bytes()
    with pytest.raises(ValueError, match="checksum"):
        restore_snapshot(paths, snapshot, replace_data=True)
    assert paths.config.read_bytes() == before
    assert not db.restore_marker.exists()


def test_interrupted_restore_blocks_startup_and_resumes_idempotently(
    tmp_path, monkeypatch
):
    import app_server.state_backup as backups

    paths, db, _store, _session = state(tmp_path, monkeypatch)
    snapshot = tmp_path / "snapshot"
    create_snapshot(paths, snapshot)
    CredentialStore(paths.credentials).set("route", "after")
    install = backups._install_file
    count = 0

    def fail(source, target):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("injected disk failure")
        install(source, target)

    monkeypatch.setattr(backups, "_install_file", fail)
    with pytest.raises(OSError, match="injected"):
        restore_snapshot(paths, snapshot, replace_data=True)
    assert db.restore_marker.exists()
    with pytest.raises(RuntimeError, match="restore is pending"):
        db.initialize()
    monkeypatch.setattr(backups, "_install_file", install)
    restore_snapshot(paths, snapshot, replace_data=True)
    db.initialize()
    assert CredentialStore(paths.credentials).get("route") == "private-before"


def test_older_runtime_refuses_newer_schema_before_backup_or_mutation(
    tmp_path, monkeypatch
):
    paths, db, _store, _session = state(tmp_path, monkeypatch)
    with db.transaction() as connection:
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, 'future', 'now')",
            (LATEST_SCHEMA_VERSION + 1,),
        )
    before = paths.database.read_bytes()
    with pytest.raises(MigrationError, match="newer"):
        db.initialize()
    assert paths.database.read_bytes() == before
    assert not (paths.database.parent / "backups").exists()


def test_restored_application_pauses_goals_and_schedules_before_recovery(
    tmp_path, monkeypatch
):
    from core.application import DeepCodeApplication
    from core.domain import AutomationScheduleKind, AutomationStatus, TrustState
    from core.domain.thread_goal import ThreadGoalStatus

    home = tmp_path / "home"
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    monkeypatch.delenv("DEEPCODE_SESSIONS_DIR", raising=False)
    paths = StatePaths.current(ServiceFiles(home / "state" / "state.sqlite3"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = DeepCodeApplication.open(
        paths.database, session_store=SessionStore(paths.sessions)
    )
    try:
        project = app.projects.add(str(workspace))
        app.projects.update(project.id, trust_state=TrustState.TRUSTED)
        created = app.automations.create(
            project_id=project.id,
            name="Review",
            prompt="Inspect the project",
            schedule_kind=AutomationScheduleKind.INTERVAL,
            interval_seconds=3600,
        )
        if app.goals.read(created.thread.id) is None:
            app.goals.create(
                created.thread.id, objective="Review after restore", start=False
            )
        assert app.goals.read(created.thread.id).status is ThreadGoalStatus.ACTIVE
    finally:
        app.close()
        # The injected store belongs to this caller, not the Application.
        app.session_store.close()
    snapshot = tmp_path / "snapshot"
    create_snapshot(paths, snapshot)
    restore_snapshot(paths, snapshot, replace_data=True)
    app = DeepCodeApplication.open(
        paths.database, session_store=SessionStore(paths.sessions)
    )
    try:
        assert (
            app.automations.read(created.automation.id).status
            is AutomationStatus.PAUSED
        )
        goal = app.goals.read(created.thread.id)
        assert goal.status is ThreadGoalStatus.PAUSED
        assert not Database(paths.database).restore_recovery_marker.exists()
    finally:
        app.close()
        # The injected store belongs to this caller, not the Application.
        app.session_store.close()
