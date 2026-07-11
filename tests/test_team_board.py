"""Tests for the P4 TaskBoard — dependencies, atomic claim, reclaim, cycles."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.team.board import (  # noqa: E402
    DONE,
    PENDING,
    READY,
    RUNNING,
    CycleError,
    TaskBoard,
)


def _board(tmp_path) -> TaskBoard:
    return TaskBoard(tmp_path / "board.db")


def test_ready_vs_pending_on_add(tmp_path):
    b = _board(tmp_path)
    a = b.add_task("a", spec="root")
    c = b.add_task("c", spec="needs a", deps=["a"])
    assert a.status == READY  # no deps → immediately claimable
    assert c.status == PENDING  # blocked on a


def test_claim_and_complete_unblocks_dependents(tmp_path):
    b = _board(tmp_path)
    b.add_task("a")
    b.add_task("b", deps=["a"])

    claimed = b.claim("w1")
    assert claimed is not None and claimed.id == "a" and claimed.status == RUNNING
    # b is still blocked while a is running.
    assert b.claim("w2") is None

    b.complete("a", success=True, result="done a")
    nxt = b.claim("w2")
    assert nxt is not None and nxt.id == "b"  # unblocked by a's completion
    assert b.get("a").status == DONE


def test_claim_returns_none_when_empty(tmp_path):
    b = _board(tmp_path)
    assert b.claim("w1") is None


def test_atomic_claim_no_double_assignment(tmp_path):
    b = _board(tmp_path)
    for i in range(50):
        b.add_task(f"t{i}")

    claimed_by: dict[str, str] = {}
    lock = threading.Lock()

    def worker(wid: str):
        while True:
            t = b.claim(wid)
            if t is None:
                return
            with lock:
                # A task must never be handed to two workers.
                assert t.id not in claimed_by, f"{t.id} double-claimed"
                claimed_by[t.id] = wid
            b.complete(t.id, success=True)

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(claimed_by) == 50  # every task claimed exactly once
    assert b.all_succeeded()


def test_reclaim_stale(tmp_path):
    b = _board(tmp_path)
    b.add_task("a")
    b.claim("dead-worker")  # now RUNNING
    assert b.get("a").status == RUNNING
    # Nothing stale yet with a huge timeout.
    assert b.reclaim_stale(timeout=10_000) == 0
    # With timeout 0 the claim is considered stale → back to ready.
    assert b.reclaim_stale(timeout=0) == 1
    assert b.get("a").status == READY


def test_cycle_detection(tmp_path):
    b = _board(tmp_path)
    b.add_task("a", deps=["b"])
    b.add_task("b", deps=["a"])
    with pytest.raises(CycleError):
        b.validate()


def test_unknown_dependency_rejected(tmp_path):
    b = _board(tmp_path)
    b.add_task("a", deps=["ghost"])
    with pytest.raises(CycleError):
        b.validate()


def test_settled_and_succeeded(tmp_path):
    b = _board(tmp_path)
    b.add_task("a")
    b.add_task("b")
    assert not b.all_settled()
    b.complete("a", success=True)
    b.complete("b", success=False, result="gave up")
    assert b.all_settled()  # both terminal
    assert not b.all_succeeded()  # one failed


def test_durable_across_reopen(tmp_path):
    b = _board(tmp_path)
    b.add_task("a", spec="remember me")
    b.complete("a", success=True, result="r")
    b.close()
    b2 = TaskBoard(tmp_path / "board.db")
    assert b2.get("a").status == DONE
    assert b2.get("a").spec == "remember me"
