"""Tests for the derived SQLite session index and its store integration.

The index is an optimisation over the JSONL source of truth, so the load-
bearing property is *parity*: an index-served query must return exactly what
the pure-JSONL scan returns. We also cover self-heal (a deleted index.db
rebuilds from disk) and graceful fallback (use_index=False).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.sessions.store import SessionStore  # noqa: E402


def _seed(store: SessionStore) -> dict[str, str]:
    """Create three sessions with messages/tasks; return {title: id}."""
    ids: dict[str, str] = {}
    for title in ("alpha", "beta", "gamma"):
        s = store.create_session(title=title)
        ids[title] = s.session_id
        store.append_message(s.session_id, "user", f"hello from {title}")
        store.append_message(s.session_id, "assistant", f"hi {title}")
    store.attach_task(
        ids["beta"], "task-42", task_kind="paper2code", task_dir="/tmp/t42"
    )
    return ids


def test_index_db_created(tmp_path):
    store = SessionStore(tmp_path)
    store.create_session(title="x")
    assert (tmp_path / "index.db").exists()
    assert store._index is not None and store._index.available


def test_list_parity_index_vs_scan(tmp_path):
    store = SessionStore(tmp_path)
    _seed(store)
    from_index = store.list_sessions(limit=50)
    from_scan = store._list_sessions_scan(limit=50, order="recent")
    # Same sessions, same order, same counts.
    assert [s.session_id for s in from_index] == [s.session_id for s in from_scan]
    by_id = {s.session_id: s for s in from_scan}
    for s in from_index:
        assert s.message_count == by_id[s.session_id].message_count
        assert s.task_count == by_id[s.session_id].task_count


def test_message_and_task_counts(tmp_path):
    store = SessionStore(tmp_path)
    ids = _seed(store)
    listing = {s.session_id: s for s in store.list_sessions()}
    assert listing[ids["alpha"]].message_count == 2
    assert listing[ids["beta"]].task_count == 1
    assert listing[ids["alpha"]].task_count == 0


def test_find_session_by_task_uses_index(tmp_path):
    store = SessionStore(tmp_path)
    ids = _seed(store)
    found = store.find_session_by_task("task-42")
    assert found is not None and found.session_id == ids["beta"]
    assert store.find_session_by_task("nonexistent") is None


def test_list_attached_tasks(tmp_path):
    store = SessionStore(tmp_path)
    ids = _seed(store)
    pairs = store.list_attached_tasks()
    assert len(pairs) == 1
    session, task = pairs[0]
    assert session.session_id == ids["beta"]
    assert task.task_id == "task-42"


def test_delete_removes_from_index(tmp_path):
    store = SessionStore(tmp_path)
    ids = _seed(store)
    assert store.delete_session(ids["alpha"]) is True
    remaining = {s.session_id for s in store.list_sessions()}
    assert ids["alpha"] not in remaining
    assert ids["beta"] in remaining


def test_task_status_update_reflected(tmp_path):
    store = SessionStore(tmp_path)
    ids = _seed(store)
    store.update_task_status(ids["beta"], "task-42", "completed")
    # Still resolvable, and the underlying task carries the new status.
    found = store.find_session_by_task("task-42")
    assert found is not None
    assert found.tasks[0].status == "completed"


def test_self_heal_after_index_deleted(tmp_path):
    # Build sessions, then simulate a wiped/absent index (older build).
    store1 = SessionStore(tmp_path)
    ids = _seed(store1)
    store1._index.close()
    (tmp_path / "index.db").unlink()

    # A fresh store sees an empty index but non-empty disk → reconcile rebuild.
    store2 = SessionStore(tmp_path)
    listing = store2.list_sessions()
    assert {s.session_id for s in listing} == set(ids.values())
    # The task link was rebuilt from JSONL too.
    assert store2.find_session_by_task("task-42").session_id == ids["beta"]


def test_reindex_rebuilds(tmp_path):
    store = SessionStore(tmp_path)
    ids = _seed(store)
    n = store.reindex()
    assert n == 3
    assert {s.session_id for s in store.list_sessions()} == set(ids.values())


def test_fallback_without_index_matches(tmp_path):
    # use_index=False forces the pure-JSONL path; results must be identical.
    indexed = SessionStore(tmp_path / "a")
    plain = SessionStore(tmp_path / "b", use_index=False)
    assert plain._index is None
    for store in (indexed, plain):
        s = store.create_session(title="one")
        store.append_message(s.session_id, "user", "hi")
        store.attach_task(s.session_id, "t1", task_kind="k", task_dir="/tmp")
    a = store_summary(indexed)
    b = store_summary(plain)
    assert a == b


def store_summary(store: SessionStore) -> list[tuple[str, int, int]]:
    return [
        (s.title, s.message_count, s.task_count)
        for s in store.list_sessions(order="oldest")
    ]
