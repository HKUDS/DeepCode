from __future__ import annotations

import json
from pathlib import Path

from cli.session_cli import run
from core.application import DeepCodeApplication
from core.sessions import SessionStore


def _seed(tmp_path: Path, monkeypatch) -> tuple[SessionStore, str]:
    home = tmp_path / "home"
    sessions = tmp_path / "sessions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    monkeypatch.setenv("DEEPCODE_SESSIONS_DIR", str(sessions))
    store = SessionStore(sessions)
    application = DeepCodeApplication.open(session_store=store)
    try:
        project = application.projects.add(str(workspace))
        thread = application.threads.start(project.id, title="CLI deletion")
        store.append_message(thread.id, "user", "temporary")
        return store, thread.id
    finally:
        application.close()


def test_session_delete_cli_uses_shared_application_service(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    store, session_id = _seed(tmp_path, monkeypatch)

    assert run(["delete", session_id, "--yes", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "deleted": True,
        "sessionId": session_id,
        "cleanupPending": False,
    }
    assert store.get_session(session_id) is None


def test_session_delete_cli_reports_cross_process_owner(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    store, session_id = _seed(tmp_path, monkeypatch)
    activity = store.acquire_activity_lease(session_id)
    assert activity is not None
    try:
        assert run(["delete", session_id, "--yes", "--json"]) == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["deleted"] is False
        assert payload["error"]["code"] == "CONFLICT"
        assert payload["error"]["details"]["blockers"][0]["code"] == "SESSION_IN_USE"
    finally:
        activity.close()


def test_session_delete_cli_requires_yes_when_piped(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _store, session_id = _seed(tmp_path, monkeypatch)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    assert run(["delete", session_id]) == 2
    assert "requires --yes" in capsys.readouterr().err
