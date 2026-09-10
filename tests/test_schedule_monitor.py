"""Tests for the monitor gate — hash-suppressed change detection.

Coverage:

- SHA-256 byte-exact hashing (no normalisation).
- Unified diff generation and truncation.
- First run = baseline (``changed=True``).
- No change = ``changed=False`` (silent skip).
- Change = ``changed=True`` with a diff + current output in the block.
- Source failure = ERROR, never treated as a change, stored hash untouched.
- ``MonitorStore`` persistence (upsert / read after restart).
- ``monitor_gate`` high-level flow (persists the new hash after a change).
"""

from __future__ import annotations

from pathlib import Path

from core.schedule.monitor import (
    MAX_DIFF_CHARS,
    MonitorStore,
    build_monitor_diff,
    check_monitor,
    hash_monitor_output,
    monitor_gate,
)


def test_hash_monitor_output_byte_exact() -> None:
    assert hash_monitor_output("hello") == hash_monitor_output("hello")
    assert hash_monitor_output("hello") != hash_monitor_output("hello\n")
    assert hash_monitor_output("a") == hash_monitor_output("a")


def test_build_monitor_diff() -> None:
    diff = build_monitor_diff("line1\nline2\n", "line1\nchanged\n")
    assert "-line2" in diff
    assert "+changed" in diff


def test_build_monitor_diff_truncates() -> None:
    old = "a\n" * 10_000
    new = "b\n" * 10_000
    diff = build_monitor_diff(old, new)
    assert len(diff) <= MAX_DIFF_CHARS + 100  # truncation marker adds a little


def test_first_run_is_baseline() -> None:
    outcome = check_monitor(
        monitor_script="echo hello",
        stored_hash="",
        stored_snapshot="",
    )
    assert outcome.changed is True
    assert "MONITOR BASELINE" in outcome.context_block
    assert outcome.error == ""


def test_no_change_is_silent() -> None:
    out = "stable output\n"
    outcome = check_monitor(
        monitor_script="echo stable output",
        stored_hash=hash_monitor_output(out),
        stored_snapshot=out,
    )
    assert outcome.changed is False
    assert outcome.error == ""
    assert outcome.context_block == ""


def test_change_detected_with_diff() -> None:
    old = "price=100\n"
    outcome = check_monitor(
        monitor_script="echo price=120",
        stored_hash=hash_monitor_output(old),
        stored_snapshot=old,
    )
    assert outcome.changed is True
    assert "MONITOR CHANGE DETECTED" in outcome.context_block
    assert "price=120" in outcome.context_block
    assert (
        "-price=100" in outcome.context_block or "+price=120" in outcome.context_block
    )


def test_script_failure_is_error_not_change() -> None:
    outcome = check_monitor(
        monitor_script="exit 1",
        stored_hash="oldhash",
        stored_snapshot="old",
    )
    assert outcome.changed is False
    assert outcome.error != ""
    assert "exit code 1" in outcome.error


def test_missing_source_is_error() -> None:
    outcome = check_monitor(stored_hash="", stored_snapshot="")
    assert outcome.changed is False
    assert outcome.error != ""


def test_monitor_store_upsert_and_restart(tmp_path: Path) -> None:
    db = tmp_path / "monitor.db"
    store1 = MonitorStore(db)
    store1.save_job("job1", "abc123", "snapshot-A")
    # Simulate a process restart: a fresh instance reads the same row.
    store2 = MonitorStore(db)
    row = store2.get_job("job1")
    assert row is not None
    assert row["monitor_hash"] == "abc123"
    assert row["snapshot"] == "snapshot-A"
    # Upsert updates the row.
    store2.save_job("job1", "def456", "snapshot-B")
    assert MonitorStore(db).get_job("job1")["monitor_hash"] == "def456"
    # Unknown job returns None.
    assert store2.get_job("nope") is None


def test_monitor_gate_persists_hash(tmp_path: Path) -> None:
    store = MonitorStore(tmp_path / "monitor.db")

    # First run -> baseline + persist.
    out1 = monitor_gate("job-x", monitor_script="echo hello", store=store)
    assert out1.changed is True
    assert store.get_job("job-x")["monitor_hash"] == hash_monitor_output("hello\n")

    # Same output -> no change.
    out2 = monitor_gate("job-x", monitor_script="echo hello", store=store)
    assert out2.changed is False
    assert out2.context_block == ""

    # Changed output -> changed + persist the new hash.
    out3 = monitor_gate("job-x", monitor_script="echo world", store=store)
    assert out3.changed is True
    assert store.get_job("job-x")["monitor_hash"] == hash_monitor_output("world\n")


def test_monitor_gate_keeps_hash_on_source_failure(tmp_path: Path) -> None:
    store = MonitorStore(tmp_path / "monitor.db")
    monitor_gate("job-y", monitor_script="echo ok", store=store)
    before = store.get_job("job-y")["monitor_hash"]

    outcome = monitor_gate("job-y", monitor_script="exit 3", store=store)
    assert outcome.error != ""
    assert outcome.changed is False
    # A failed source must not clobber the stored baseline.
    assert store.get_job("job-y")["monitor_hash"] == before
