"""Windows NTFS ACL restriction tests for core.private_storage.

These tests assert that private directories and files are restricted to the
current user with full control and that dangerous well-known ACEs (Everyone,
Authenticated Users, BUILTIN\\Users) are removed after the restriction runs.
They are skipped on non-Windows platforms.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from core.private_storage import (
    ensure_private_directory,
    harden_private_tree,
    open_private_file,
)

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="NTFS ACL restriction applies on Windows only",
)

_DANGEROUS_ACES = ("Authenticated Users", "BUILTIN\\Users", "Everyone")


def _windows_identity() -> str:
    completed = subprocess.run(
        ["whoami"],
        capture_output=True,
        text=True,
        encoding="mbcs",
        errors="replace",
        timeout=5,
        check=True,
    )
    return (completed.stdout or "").strip()


def _acl_lines(path: Path) -> list[str]:
    completed = subprocess.run(
        ["icacls", os.fspath(path)],
        capture_output=True,
        text=True,
        encoding="mbcs",
        errors="replace",
        timeout=15,
        check=True,
    )
    return [
        line.strip() for line in (completed.stdout or "").splitlines() if ":" in line
    ]


def _assert_no_dangerous_aces(path: Path) -> None:
    lines = _acl_lines(path)
    joined = "\n".join(lines).lower()
    for ace in _DANGEROUS_ACES:
        assert ace.lower() not in joined, (
            f"{path} still exposes dangerous ACE {ace!r}:\n{joined}"
        )


def _assert_current_user_has_full_control(path: Path) -> None:
    identity = _windows_identity().lower()
    # ``whoami`` may return either ``domain\user`` or a bare ``user`` depending
    # on which binary is on PATH, while ``icacls`` always prints the fully
    # qualified principal.  Compare the last path segment so both match.
    short_name = identity.rsplit("\\", 1)[-1]
    lines = _acl_lines(path)
    for line in lines:
        # Split from the right: icacls lines start with a Windows path that
        # contains a drive-letter colon (``C:\\...``), so the first colon is
        # not the principal/rights separator.
        principal, rights = line.rsplit(":", 1)
        principal_short = principal.strip().lower().rsplit("\\", 1)[-1]
        if principal_short == short_name:
            assert "(f)" in rights.lower(), (
                f"{path} does not grant the current user full control:\n{line}"
            )
            return
    raise AssertionError(
        f"{path} has no ACE for the current user {identity!r}:\n" + "\n".join(lines)
    )


def test_windows_private_directory_is_restricted(tmp_path: Path) -> None:
    directory = ensure_private_directory(tmp_path / "private" / "nested")

    _assert_no_dangerous_aces(directory)
    _assert_current_user_has_full_control(directory)


def test_windows_private_file_is_restricted(tmp_path: Path) -> None:
    target = tmp_path / "private" / "credentials.json"
    descriptor = open_private_file(target, os.O_WRONLY | os.O_CREAT)
    try:
        os.write(descriptor, b"secret")
    finally:
        os.close(descriptor)

    _assert_no_dangerous_aces(target)
    _assert_current_user_has_full_control(target)
    assert target.read_bytes() == b"secret"


def test_windows_harden_private_tree_restricts_every_entry(tmp_path: Path) -> None:
    root = tmp_path / "legacy-private"
    session = root / "session-1"
    session.mkdir(parents=True)
    (session / "session.jsonl").write_text("legacy\n", encoding="utf-8")
    (root / "settings.json").write_text("{}", encoding="utf-8")

    harden_private_tree(root)

    for path in (root, session, session / "session.jsonl", root / "settings.json"):
        _assert_no_dangerous_aces(path)
        _assert_current_user_has_full_control(path)


def test_windows_open_existing_file_does_not_rerun_acl(monkeypatch, tmp_path: Path) -> None:
    """The per-open ACL re-run is gone: opening an existing private file does
    not call _restrict_windows_acl again (the ACL was applied at creation)."""
    import core.private_storage as ps

    calls = []
    monkeypatch.setattr(ps, "_restrict_windows_acl", lambda p: calls.append(p))

    target = tmp_path / "existing.jsonl"
    # First open: file does not exist → new → restrict once.
    fd = ps.open_private_file(target, os.O_CREAT | os.O_RDWR)
    os.close(fd)
    assert len(calls) == 1, "new file must be restricted exactly once"

    # Second open: file exists → must NOT re-run the ACL restriction.
    fd = ps.open_private_file(target, os.O_RDWR)
    os.close(fd)
    assert len(calls) == 1, "existing file must not re-run the ACL restriction"


def test_windows_open_created_file_restricts_once(monkeypatch, tmp_path: Path) -> None:
    """A file created via open_private_file is restricted exactly once."""
    import core.private_storage as ps

    calls = []
    monkeypatch.setattr(ps, "_restrict_windows_acl", lambda p: calls.append(p))

    for _ in range(3):
        fd = ps.open_private_file(tmp_path / "fresh.jsonl", os.O_CREAT | os.O_RDWR)
        os.close(fd)
    assert len(calls) == 1, "created once, restricted once, never again"
