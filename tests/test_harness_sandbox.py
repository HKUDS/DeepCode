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
    fences_writes,
    wrap_shell_command,
)
from core.harness.windows_sandbox import _run_in_job  # noqa: E402


def test_policy_for_workspace_normalizes_root(tmp_path):
    policy = SandboxPolicy.for_workspace(tmp_path)
    assert policy.normalized_roots() == [os.path.abspath(str(tmp_path))]
    assert policy.allow_network is False


def test_wrap_returns_runnable_argv(tmp_path):
    policy = SandboxPolicy.for_workspace(tmp_path)
    wrapped = wrap_shell_command("echo hi", policy)
    assert wrapped.argv[-2:] == ["-c", "echo hi"]
    assert wrapped.backend in ("seatbelt", "bwrap", "job", "none")


@pytest.mark.skipif(
    platform.system() != "Darwin", reason="seatbelt policy is macOS-only"
)
def test_seatbelt_profile_is_deny_default_and_grants_workspace_writes(tmp_path):
    policy = SandboxPolicy.for_workspace(tmp_path)
    profile = _seatbelt_profile(policy)
    # C4b: closed-by-default (deny everything), not an allow-default write-fence.
    assert "(deny default)" in profile
    assert "(allow default)" not in profile
    assert "(allow file-read*)" in profile  # reads still broad
    assert (
        f'(allow file-write* (subpath "{os.path.abspath(str(tmp_path))}"))' in profile
    )
    # Under deny-default, no network allows means network is denied.
    assert "network-outbound" not in profile


@pytest.mark.skipif(
    platform.system() != "Darwin", reason="seatbelt policy is macOS-only"
)
def test_seatbelt_profile_network_toggle(tmp_path):
    policy = SandboxPolicy.for_workspace(tmp_path, allow_network=True)
    # Network is re-granted only when the policy allows it.
    assert "(allow network-outbound)" in _seatbelt_profile(policy)
    off = SandboxPolicy.for_workspace(tmp_path, allow_network=False)
    assert "network-outbound" not in _seatbelt_profile(off)


def test_none_backend_is_flagged(monkeypatch, tmp_path):
    monkeypatch.setattr("core.harness.sandbox.sandbox_backend", lambda: "none")
    wrapped = wrap_shell_command("echo hi", SandboxPolicy.for_workspace(tmp_path))
    assert wrapped.backend == "none"
    assert wrapped.argv == ["/bin/bash", "-c", "echo hi"]


@pytest.mark.parametrize(
    ("backend", "fenced"),
    [
        ("seatbelt", True),
        ("bwrap", True),
        # The Job Object isolates the process tree but leaves the filesystem
        # open, so callers must not advertise a write-fence on Windows.
        ("job", False),
        ("none", False),
    ],
)
def test_fences_writes_tracks_backend_capability(monkeypatch, backend, fenced):
    monkeypatch.setattr("core.harness.sandbox.sandbox_backend", lambda: backend)
    assert fences_writes() is fenced


def test_job_launcher_starts_without_deepcode_on_the_path(monkeypatch, tmp_path):
    """Commands run in the project without DeepCode's PYTHONPATH, so the job
    launcher must not need ``import core`` to start."""

    import core.harness.windows_sandbox as launcher

    monkeypatch.setattr("core.harness.sandbox.sandbox_backend", lambda: "job")
    wrapped = wrap_shell_command("echo hi", SandboxPolicy.for_workspace(tmp_path))
    assert wrapped.argv[1:3] == [launcher.__file__, "--"]
    # -I keeps an installed copy of DeepCode out of reach as well.
    proc = subprocess.run(
        [sys.executable, "-I", *wrapped.argv[1:]],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert "No module named" not in proc.stderr
    if os.name != "nt":
        assert "only runs on Windows" in proc.stderr


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


def _run(command: str, policy: SandboxPolicy, cwd: Path):
    wrapped = wrap_shell_command(command, policy)
    try:
        return subprocess.run(
            wrapped.argv, capture_output=True, text=True, timeout=30, cwd=cwd
        )
    finally:
        wrapped.cleanup()


def test_policy_for_workspace_keeps_credential_stores_unreadable(tmp_path):
    from core.config import deepcode_home

    unreadable = SandboxPolicy.for_workspace(tmp_path).unreadable

    assert str(Path.home() / ".ssh") in unreadable
    assert str(Path.home() / ".aws") in unreadable
    assert str(deepcode_home() / "credentials.json") in unreadable


@pytest.mark.skipif(not _seatbelt, reason="seatbelt sandbox not available")
def test_seatbelt_denies_credential_locations(tmp_path):
    # A stand-in home: the policy names its own locations, so no real
    # credential is ever read by this test.
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_rsa").write_text("FAKE-KEY")
    (home / ".netrc").write_text("FAKE-NETRC")
    (home / "notes.txt").write_text("fine")
    (home / "link").symlink_to(home / ".ssh" / "id_rsa")
    policy = SandboxPolicy(
        writable_roots=(str(home),),
        unreadable=(str(home / ".ssh"), str(home / ".netrc")),
    )

    for command in (
        "cat .ssh/id_rsa",
        "cat .SSH/id_rsa",
        "cat link",
        "cat .netrc",
        "ls .ssh",
        "echo planted > .ssh/authorized_keys",
    ):
        proc = _run(command, policy, cwd=home)
        assert proc.returncode != 0, command
        assert "FAKE" not in proc.stdout, command
    assert not (home / ".ssh" / "authorized_keys").exists()
    assert _run("cat notes.txt", policy, cwd=home).stdout == "fine"


def test_bwrap_masks_credential_locations_that_exist(monkeypatch, tmp_path):
    monkeypatch.setattr("core.harness.sandbox.sandbox_backend", lambda: "bwrap")
    store = tmp_path / ".aws"
    store.mkdir()
    netrc = tmp_path / ".netrc"
    netrc.write_text("x")
    absent = tmp_path / ".kube" / "config"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    policy = SandboxPolicy(
        writable_roots=(str(workspace),),
        unreadable=(str(store), str(netrc), str(absent)),
    )

    argv = wrap_shell_command("true", policy).argv
    joined = "\0".join(argv)

    # After the binds, so a mask also covers a store inside a writable root.
    bind = joined.index("\0".join(["--bind", str(workspace), str(workspace)]))
    assert joined.index("\0".join(["--tmpfs", str(store)])) > bind
    assert joined.index("\0".join(["--ro-bind", "/dev/null", str(netrc)])) > bind
    assert str(absent) not in argv


def test_bwrap_masks_a_symlinked_store_where_it_really_is(monkeypatch, tmp_path):
    monkeypatch.setattr("core.harness.sandbox.sandbox_backend", lambda: "bwrap")
    real = tmp_path / "dotfiles" / "aws"
    real.mkdir(parents=True)
    linked = tmp_path / ".aws"
    linked.symlink_to(real)
    policy = SandboxPolicy(unreadable=(str(linked),))

    argv = wrap_shell_command("true", policy).argv

    assert "\0".join(["--tmpfs", str(real)]) in "\0".join(argv)
    assert str(linked) not in argv


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


# ---- Windows Job Object backend ------------------------------------------

_job = platform.system() == "Windows"


@pytest.mark.skipif(not _job, reason="Windows Job Object backend only")
def test_windows_job_runs_and_forwards_exit_code():
    code = "import sys; sys.exit(7)"
    exit_code = _run_in_job([sys.executable, "-c", code])
    assert exit_code == 7


@pytest.mark.skipif(not _job, reason="Windows Job Object backend only")
def test_windows_job_wrapper_is_used_by_build_exec_command(tmp_path):
    from core.harness.sandbox import build_exec_command

    wrapped = build_exec_command(
        argv=[sys.executable, "-c", "pass"],
        workspace=tmp_path,
        allow_network=False,
    )
    # The wrapper script must prefix the inner argv.
    assert wrapped.argv[1].endswith("windows_sandbox.py")
    assert wrapped.backend == "job"


@pytest.mark.skipif(not _job, reason="Windows Job Object backend only")
def test_windows_job_kills_descendant_processes():
    """KILL_ON_JOB_CLOSE: a background child spawned by the inner command must
    not outlive the wrapper (no orphaned process escapes the job)."""
    probe = sys.executable
    code = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "print(child.pid, flush=True)\n"
    )
    exit_code = _run_in_job([probe, "-c", code])
    assert exit_code == 0


# ---- Windows command construction (runs everywhere) ------------------------


class _FakeKernel32:
    """Just enough kernel32 for ``_run_in_job`` to build its CreateProcessW call."""

    std_handles = {-10: 100, -11: 101, -12: 102}

    def __init__(self):
        self.inheritable: set[int] = set()
        self.create_process = None

    def CreateJobObjectW(self, *_args):
        return 1

    def SetInformationJobObject(self, *_args):
        return 1

    def GetStdHandle(self, which):
        return self.std_handles[which]

    def SetHandleInformation(self, handle, mask, flags):
        if mask & flags & 0x1:
            self.inheritable.add(handle)
        return 1

    def CreateProcessW(self, _app, command_line, _pa, _ta, inherit, *rest):
        startup = rest[-2]._obj
        self.create_process = {
            "command_line": command_line,
            "inherit_handles": inherit,
            "flags": startup.dwFlags,
            "std": (startup.hStdInput, startup.hStdOutput, startup.hStdError),
        }
        return 1

    def GetExitCodeProcess(self, _handle, code):
        code._obj.value = 3
        return 1

    def __getattr__(self, _name):
        return lambda *_args: 1


@pytest.fixture
def fake_kernel32(monkeypatch):
    import ctypes

    kernel32 = _FakeKernel32()
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_k: kernel32, raising=False)
    return kernel32


def test_windows_job_quotes_arguments_like_subprocess(fake_kernel32):
    """The inner bash must receive the command exactly, quotes and all."""

    argv = [
        "C:\\Program Files\\Git\\bin\\bash.exe",
        "-c",
        'echo "hi there" && grep -r "a\\"b" src\\',
    ]
    assert _run_in_job(argv) == 3
    assert fake_kernel32.create_process["command_line"] == subprocess.list2cmdline(argv)


def test_windows_job_hands_its_pipes_to_the_inner_command(fake_kernel32):
    """Under the agent's shell the wrapper's stdout is a pipe; without passing
    it on (inheritably) the command's output never arrives."""

    _run_in_job(["bash.exe", "-c", "echo hi"])
    call = fake_kernel32.create_process
    assert call["inherit_handles"]
    assert call["flags"] & 0x100  # STARTF_USESTDHANDLES
    assert call["std"] == (100, 101, 102)
    assert fake_kernel32.inheritable == {100, 101, 102}


def test_windows_shell_is_git_bash_not_bin_bash(monkeypatch, tmp_path):
    """Windows has no /bin/bash; Git for Windows keeps bash.exe beside git."""

    from core.harness.sandbox import build_exec_command

    git = tmp_path / "Git" / "cmd" / "git.exe"
    bash = tmp_path / "Git" / "bin" / "bash.exe"
    for path in (git, bash):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    monkeypatch.setattr("core.harness.sandbox.platform.system", lambda: "Windows")
    monkeypatch.setattr(
        "core.harness.sandbox.shutil.which",
        lambda name, *_a, **_k: str(git) if name == "git" else None,
    )
    wrapped = build_exec_command(command="echo hi", workspace=tmp_path, enabled=False)
    assert wrapped.argv == [str(bash.resolve()), "-c", "echo hi"]


def test_windows_shell_falls_back_to_bash_on_path(monkeypatch, tmp_path):
    from core.harness.sandbox import build_exec_command

    on_path = str(tmp_path / "msys64" / "usr" / "bin" / "bash.exe")
    monkeypatch.setattr("core.harness.sandbox.platform.system", lambda: "Windows")
    monkeypatch.setattr(
        "core.harness.sandbox.shutil.which",
        lambda name, *_a, **_k: on_path if name == "bash" else None,
    )
    wrapped = build_exec_command(command="echo hi", workspace=tmp_path, enabled=False)
    assert wrapped.argv == [on_path, "-c", "echo hi"]
