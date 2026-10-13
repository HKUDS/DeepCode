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

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from core.network.safe_http import PublicResolver
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


def test_url_monitor_refuses_private_hosts() -> None:
    """Loopback/private URLs never reach the network (same policy as WebFetch)."""
    from core.schedule.monitor import check_monitor

    outcome = check_monitor(monitor_url="http://127.0.0.1:9/never")
    assert not outcome.changed and "network policy" in outcome.error


@pytest.fixture
def private_site(monkeypatch):
    """A loopback server that ``public.example`` resolves to at the OS level.

    ``/redirect`` sends the client on to the server's own loopback address.
    Yields ``(port, paths requested)``.
    """
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            hits.append(self.path)
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{port}/secret")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = b"internal secret"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:  # type: ignore[no-untyped-def]
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    real_getaddrinfo = socket.getaddrinfo

    def fake_getaddrinfo(host, port_, *args, **kwargs):  # type: ignore[no-untyped-def]
        if host == "public.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
        return real_getaddrinfo(host, port_, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    for name in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("no_proxy", "*")
    try:
        yield port, hits
    finally:
        server.shutdown()
        server.server_close()


def test_url_monitor_refuses_a_host_that_resolves_to_a_private_address(
    private_site,
) -> None:
    _port, hits = private_site

    outcome = check_monitor(monitor_url="http://public.example/page")

    assert not outcome.changed and outcome.error
    assert hits == []


def test_url_monitor_refuses_a_redirect_to_a_private_address(
    private_site, monkeypatch
) -> None:
    port, hits = private_site

    # Stand-in for a genuinely public answer, so the first hop is allowed.
    async def resolve(self, host, port_=0, family=socket.AF_UNSPEC):  # type: ignore[no-untyped-def]
        return [
            {
                "hostname": host,
                "host": "127.0.0.1",
                "port": port,
                "family": socket.AF_INET,
                "proto": 0,
                "flags": socket.AI_NUMERICHOST,
            }
        ]

    monkeypatch.setattr(PublicResolver, "resolve", resolve)

    outcome = check_monitor(monitor_url="http://public.example/redirect")

    assert not outcome.changed and "network policy" in outcome.error
    assert hits == ["/redirect"]


def test_script_monitor_refuses_destructive_commands() -> None:
    from core.schedule.monitor import check_monitor

    outcome = check_monitor(monitor_script="rm -rf /")
    assert not outcome.changed and outcome.error.startswith("Script refused")


def test_default_store_lives_under_the_deepcode_home(
    tmp_path: Path, monkeypatch
) -> None:
    from core.schedule.monitor import MonitorStore, default_monitor_store_path

    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "home"))
    expected = tmp_path / "home" / "state" / "monitor.sqlite3"
    assert default_monitor_store_path() == expected.resolve()
    store = MonitorStore()
    store.save_job("j", "h", "s")
    assert expected.exists()
