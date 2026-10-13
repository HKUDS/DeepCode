"""One live writer per Session — the dsh contract, enforced.

The store lease is the mechanism; the registry gate is the policy. Two
processes may hold the same Session open and alternate turns; what is
refused, with the holder named, is executing at the same time.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.sessions import SessionStore  # noqa: E402


@pytest.fixture()
def store(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path / "sessions")


def test_second_holder_is_refused_and_told_who_holds(store: SessionStore) -> None:
    session = store.create_session(title="t", metadata={})
    first = store.acquire_run_lease(session.session_id, holder="desktop (pid 11)")
    assert first is not None
    try:
        second = store.acquire_run_lease(session.session_id, holder="cli (pid 22)")
        assert second is None
        assert store.run_holder(session.session_id) == "desktop (pid 11)"
    finally:
        first.close()


def test_release_hands_the_session_over(store: SessionStore) -> None:
    session = store.create_session(title="t", metadata={})
    first = store.acquire_run_lease(session.session_id, holder="desktop (pid 11)")
    assert first is not None
    first.close()
    second = store.acquire_run_lease(session.session_id, holder="cli (pid 22)")
    assert second is not None
    second.close()


def test_leases_are_per_session_not_global(store: SessionStore) -> None:
    a = store.create_session(title="a", metadata={})
    b = store.create_session(title="b", metadata={})
    hold_a = store.acquire_run_lease(a.session_id, holder="desktop (pid 11)")
    assert hold_a is not None
    try:
        hold_b = store.acquire_run_lease(b.session_id, holder="cli (pid 22)")
        assert hold_b is not None, "a busy Session must not block a different one"
        hold_b.close()
    finally:
        hold_a.close()


def test_this_process_is_named_while_it_takes_the_lease(
    store: SessionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The holder's name is written after the lock is taken. Until then the
    note names the previous holder or no one, so a check in between took this
    process for a stranger and refused its own Turn as "running in another
    DeepCode process".
    """
    session = store.create_session(title="t", metadata={})
    previous = store.acquire_run_lease(session.session_id, holder="cli (pid 22)")
    assert previous is not None
    previous.close()

    naming = threading.Event()
    os_replace = os.replace

    def slow_holder_write(source, destination, *args, **kwargs):
        if Path(destination).parent.name == ".running":
            naming.set()
            time.sleep(0.3)
        return os_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "replace", slow_holder_write)
    leases = []
    taker = threading.Thread(
        target=lambda: leases.append(
            store.acquire_run_lease(session.session_id, holder="desktop (pid 11)")
        )
    )
    taker.start()
    try:
        assert naming.wait(5)
        seen = store.run_holder(session.session_id)
    finally:
        taker.join()
        for lease in leases:
            if lease is not None:
                lease.close()
    assert seen == "desktop (pid 11)"


def test_run_holder_is_none_when_no_one_runs_the_session(store: SessionStore) -> None:
    session = store.create_session(title="t", metadata={})
    assert store.run_holder(session.session_id) is None
    lease = store.acquire_run_lease(session.session_id, holder="desktop (pid 11)")
    assert lease is not None
    lease.close()
    assert store.run_holder(session.session_id) is None


def test_holder_is_named_where_a_held_lock_file_cannot_be_read(
    store: SessionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On Windows a lock keeps the locked file from being read or written
    through any other handle. With the holder's name kept inside the lock
    file, this process's own running Turn read as an unnamed stranger, and a
    message sent meanwhile was refused as "running in another DeepCode
    process".
    """
    session = store.create_session(title="t", metadata={})

    def locked(original):
        def guarded(path: Path, *args, **kwargs):
            if path.parent.name == ".running" and path.suffix == ".lock":
                raise PermissionError(13, "locked by another handle", str(path))
            return original(path, *args, **kwargs)

        return guarded

    for name in ("open", "read_text", "write_text", "read_bytes", "write_bytes"):
        monkeypatch.setattr(Path, name, locked(getattr(Path, name)))
    lease = store.acquire_run_lease(session.session_id, holder="desktop (pid 11)")
    assert lease is not None
    try:
        assert store.run_holder(session.session_id) == "desktop (pid 11)"
    finally:
        lease.close()


def test_releasing_a_lease_removes_its_holder_note_and_no_other(
    store: SessionStore,
) -> None:
    session = store.create_session(title="t", metadata={})
    running = store.root / ".running"
    first = store.acquire_run_lease(session.session_id, holder="desktop (pid 11)")
    assert first is not None
    first.close()
    assert [path.name for path in running.iterdir()] == [f"{session.session_id}.lock"]

    second = store.acquire_run_lease(session.session_id, holder="cli (pid 22)")
    assert second is not None
    try:
        # Releasing the first lease again must not take the next one's name.
        first.close()
        assert store.run_holder(session.session_id) == "cli (pid 22)"
    finally:
        second.close()
    assert [path.name for path in running.iterdir()] == [f"{session.session_id}.lock"]
