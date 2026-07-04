"""Tests for the P1 sandbox command-wrapping library.

Backend-agnostic assertions on wrapping structure + a real macOS seatbelt
enforcement smoke (skipped off Darwin / when sandbox-exec is absent).
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.sandbox import (  # noqa: E402
    SandboxPolicy,
    _seatbelt_profile,
    wrap_shell_command,
)


def test_policy_for_workspace_normalizes_root(tmp_path):
    policy = SandboxPolicy.for_workspace(tmp_path)
    assert policy.normalized_roots() == [os.path.abspath(str(tmp_path))]
    assert policy.allow_network is False


def test_wrap_returns_runnable_argv(tmp_path):
    policy = SandboxPolicy.for_workspace(tmp_path)
    wrapped = wrap_shell_command("echo hi", policy)
    assert wrapped.argv[-2:] == ["-c", "echo hi"]
    assert wrapped.backend in ("seatbelt", "bwrap", "none")


def test_seatbelt_profile_grants_only_workspace_writes(tmp_path):
    policy = SandboxPolicy.for_workspace(tmp_path)
    profile = _seatbelt_profile(policy)
    assert "(deny file-write*)" in profile
    assert (
        f'(allow file-write* (subpath "{os.path.abspath(str(tmp_path))}"))' in profile
    )
    assert "(deny network*)" in profile  # network denied by default


def test_seatbelt_profile_network_toggle(tmp_path):
    policy = SandboxPolicy.for_workspace(tmp_path, allow_network=True)
    assert "(deny network*)" not in _seatbelt_profile(policy)


def test_none_backend_is_flagged(monkeypatch, tmp_path):
    monkeypatch.setattr("core.harness.sandbox.sandbox_backend", lambda: "none")
    wrapped = wrap_shell_command("echo hi", SandboxPolicy.for_workspace(tmp_path))
    assert wrapped.backend == "none"
    assert wrapped.argv == ["/bin/bash", "-c", "echo hi"]


# ---- real enforcement smoke (macOS only) -----------------------------------

_seatbelt = platform.system() == "Darwin" and os.path.exists("/usr/bin/sandbox-exec")


@pytest.mark.skipif(not _seatbelt, reason="seatbelt sandbox not available")
def test_seatbelt_allows_write_inside_workspace(tmp_path):
    policy = SandboxPolicy.for_workspace(tmp_path)
    target = tmp_path / "inside.txt"
    wrapped = wrap_shell_command(f"echo ok > {target}", policy)
    try:
        proc = subprocess.run(wrapped.argv, capture_output=True, timeout=30)
    finally:
        wrapped.cleanup()
    assert proc.returncode == 0, proc.stderr.decode()
    assert target.read_text().strip() == "ok"


@pytest.mark.skipif(not _seatbelt, reason="seatbelt sandbox not available")
def test_seatbelt_blocks_write_outside_workspace(tmp_path):
    # The target must be outside BOTH the workspace and the always-allowed
    # temp areas (the profile grants /tmp and /private/var/folders so builds
    # work). ``tmp_path`` lives under temp, so use a path in $HOME instead.
    workspace = tmp_path / "ws"
    workspace.mkdir()
    forbidden = Path.home() / ".deepcode_sandbox_leak_test.txt"
    if forbidden.exists():
        forbidden.unlink()
    policy = SandboxPolicy.for_workspace(workspace)
    wrapped = wrap_shell_command(f"echo leak > {forbidden}", policy)
    try:
        proc = subprocess.run(wrapped.argv, capture_output=True, timeout=30)
        leaked = forbidden.exists()
    finally:
        wrapped.cleanup()
        if forbidden.exists():
            forbidden.unlink()
    assert proc.returncode != 0
    assert not leaked


@pytest.mark.skipif(not _seatbelt, reason="seatbelt sandbox not available")
def test_seatbelt_allows_reads_everywhere(tmp_path):
    # Reads outside the workspace must still succeed (agents read sys headers).
    policy = SandboxPolicy.for_workspace(tmp_path / "ws")
    (tmp_path / "ws").mkdir()
    readable = tmp_path / "readme.txt"
    readable.write_text("hello")
    wrapped = wrap_shell_command(f"cat {readable}", policy)
    try:
        proc = subprocess.run(wrapped.argv, capture_output=True, timeout=30)
    finally:
        wrapped.cleanup()
    assert proc.returncode == 0
    assert proc.stdout.decode().strip() == "hello"
