from __future__ import annotations

import multiprocessing
import os
from pathlib import Path

import pytest

from core.sessions import SessionStore


def _hold_activity_lease(root: str, session_id: str, ready, release) -> None:
    store = SessionStore(root)
    lease = store.acquire_activity_lease(session_id)
    ready.put(lease is not None)
    try:
        release.wait(timeout=5)
    finally:
        if lease is not None:
            lease.close()


def test_staged_deletion_is_hidden_and_can_roll_back(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create_session(session_id="session-a", title="Keep me")
    store.append_message(session.session_id, "user", "hello")

    with store.deletion_guard(session.session_id) as guarded:
        ticket = guarded.stage()

        assert guarded.pending is True
        assert store.get_session(session.session_id) is None
        assert store.list_sessions() == []
        assert not (store.root / session.session_id).exists()

        guarded.rollback(ticket)

    restored = store.get_session(session.session_id)
    assert restored is not None
    assert restored.title == "Keep me"
    assert [message.content for message in restored.messages] == ["hello"]
    assert store.pending_deletions() == ()


def test_finalized_deletion_removes_canonical_bytes_and_index(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create_session(session_id="session-a", title="Delete me")

    with store.deletion_guard(session.session_id) as guarded:
        ticket = guarded.stage()
        assert guarded.finalize(ticket) is True

    assert store.get_session(session.session_id) is None
    assert store.list_sessions() == []
    assert store.pending_deletions() == ()
    assert list((store.root / ".trash").iterdir()) == []


def test_pending_tombstone_prevents_explicit_id_recreation(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create_session(session_id="session-a")

    with store.deletion_guard(session.session_id) as guarded:
        guarded.stage()

    assert store.is_deletion_pending(session.session_id) is True
    try:
        store.create_session(session_id=session.session_id)
    except FileExistsError:
        pass
    else:  # pragma: no cover - explicit regression assertion
        raise AssertionError("a pending deletion must reserve its Session id")


def test_session_creation_failure_never_leaves_an_unreadable_final_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SessionStore(tmp_path / "sessions")
    original_replace = os.replace
    failed = False

    def fail_first_session_publish(source, destination):
        nonlocal failed
        if not failed and str(source).endswith(".creating"):
            failed = True
            raise OSError("injected publish failure")
        return original_replace(source, destination)

    monkeypatch.setattr("core.sessions.store.os.replace", fail_first_session_publish)
    with pytest.raises(OSError, match="injected publish failure"):
        store.create_session(session_id="session-a", title="Atomic")

    assert not (store.root / "session-a").exists()
    assert list(store.root.glob(".session-a.*.creating")) == []

    interrupted = store.root / ".session-a.interrupted.creating"
    interrupted.mkdir()
    (interrupted / "session.jsonl").write_text(
        '{"_type":"metadata","session_id":"session-a","title":"partial",'
        '"created_at":"2026-07-29T00:00:00+00:00",'
        '"updated_at":"2026-07-29T00:00:00+00:00","metadata":{}}\n',
        encoding="utf-8",
    )
    assert store.list_sessions() == []

    created = store.create_session(session_id="session-a", title="Atomic")
    assert store.get_session(created.session_id) is not None


def test_activity_lease_excludes_permanent_deletion_lease(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create_session(session_id="session-a")

    activity = store.acquire_activity_lease(session.session_id)
    assert activity is not None
    try:
        assert store.acquire_deletion_lease(session.session_id) is None
    finally:
        activity.close()

    deletion = store.acquire_deletion_lease(session.session_id)
    assert deletion is not None
    deletion.close()


def test_activity_lease_coordinates_independent_processes(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create_session(session_id="session-a")
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    release = context.Event()
    process = context.Process(
        target=_hold_activity_lease,
        args=(str(store.root), session.session_id, ready, release),
    )
    process.start()
    try:
        assert ready.get(timeout=5) is True
        assert store.acquire_deletion_lease(session.session_id) is None
    finally:
        release.set()
        process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=2)
    assert process.exitcode == 0

    deletion = store.acquire_deletion_lease(session.session_id)
    assert deletion is not None
    deletion.close()
