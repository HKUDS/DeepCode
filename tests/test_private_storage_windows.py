"""Windows ACL tests for ``core.private_storage``.

POSIX-mode assertions live in ``test_private_storage.py`` (skipped on
Windows).  On Windows the equivalent guarantee is a *restricted NTFS ACL*:
inherited ACEs such as ``Authenticated Users`` and ``BUILTIN\\Users`` are
removed and only the current user keeps full control.

These tests run on Windows only; on POSIX the helpers are no-ops and the
assertions below are skipped.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from core.private_storage import ensure_private_directory, open_private_file

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="NTFS ACL restriction applies on Windows only",
)

# ACEs that must never survive on a private DeepCode path.
_DANGEROUS_ACES = (
    "Authenticated Users",
    "BUILTIN\\Users",
    "Everyone",
)

# System-level ACEs that icacls keeps by design and are safe to ignore.
_SAFE_ACES = (
    "NT AUTHORITY\\SYSTEM",
    "BUILTIN\\Administrators",
    "OWNER RIGHTS",
    "CREATOR OWNER",
)


def _acl_lines(path: Path) -> list[str]:
    completed = subprocess.run(
        ["icacls", os.fspath(path)],
        capture_output=True,
        text=True,
        encoding="mbcs",
        errors="replace",
        check=False,
    )
    return [
        line.strip()
        for line in (completed.stdout or "").splitlines()
        if ":" in line
    ]


def _assert_no_dangerous_aces(path: Path) -> None:
    lines = _acl_lines(path)
    assert lines, f"icacls returned no ACE lines for {path}"
    for line in lines:
        for ace in _DANGEROUS_ACES:
            assert ace not in line, (
                f"{path} still exposes dangerous ACE '{ace}': {line}"
            )


def test_windows_private_directory_is_restricted(tmp_path: Path) -> None:
    private = tmp_path / "home" / "sessions"
    ensure_private_directory(private)
    _assert_no_dangerous_aces(private)


def test_windows_private_file_is_restricted(tmp_path: Path) -> None:
    private = tmp_path / "home"
    ensure_private_directory(private)
    fd = open_private_file(private / "credentials.json", os.O_CREAT | os.O_WRONLY)
    os.close(fd)
    _assert_no_dangerous_aces(private / "credentials.json")


def test_windows_restriction_keeps_current_user(tmp_path: Path) -> None:
    private = tmp_path / "home"
    ensure_private_directory(private)
    fd = open_private_file(private / "secret.bin", os.O_CREAT | os.O_WRONLY)
    os.close(fd)
    lines = _acl_lines(private / "secret.bin")
    # icacls prints the current principal as DOMAIN\user with (F) full control;
    # the exact DOMAIN prefix varies (machine name vs. domain), so accept any
    # ACE granting full control that is not a system-level ACE.
    for line in lines:
        principal = line.split(":")[0]
        if ":(" in line and "(F)" in line and principal not in _SAFE_ACES:
            return  # found a full-control grant for a non-system principal
    pytest.fail(f"no current-user full-control grant found in: {lines!r}")
