"""One live writer per Session — the dsh contract, enforced.

The store lease is the mechanism; the registry gate is the policy. Two
processes may hold the same Session open and alternate turns; what is
refused, with the holder named, is executing at the same time.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.sessions import SessionStore


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
