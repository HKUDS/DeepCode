"""User-private filesystem primitives for DeepCode runtime state.

DeepCode stores transcripts, credentials, execution state, and command history
under its user data directory.  Callers use these helpers instead of relying on
the process umask, which is commonly permissive on desktop systems.

POSIX permissions are repaired to ``0700`` for directories and ``0600`` for
regular files.  Windows access control is not inherited from the user profile
by default (the profile ACL typically grants ``Authenticated Users`` modify
rights), so we remove inheritance and grant the current user exclusive access —
matching the POSIX ``0700``/``0600`` intent on NTFS.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


def _windows_identity() -> str | None:
    """Return the current Windows user (``DOMAIN\\user``) for ACL grants.

    ``%USERNAME%`` alone is ambiguous across domains, so we ask ``whoami``
    for the fully qualified principal.  Returns ``None`` when the identity
    cannot be resolved (defensive: callers fall back to no-op).
    """
    try:
        completed = subprocess.run(
            ["whoami"],
            capture_output=True,
            text=True,
            encoding="mbcs",
            errors="replace",
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    principal = (completed.stdout or "").strip()
    return principal or None


def _restrict_windows_acl(path: Path) -> None:
    """Restrict ``path`` to the current user on Windows (NTFS).

    Two steps, both idempotent and safe to run repeatedly:

    1. ``/inheritance:r`` removes all inherited ACEs so a permissive parent
       (e.g. the profile root granting ``Authenticated Users``) no longer
       applies.
    2. ``/grant:r <user>:F`` grants the current user exclusive full control
       (``:r`` replaces any existing user ACE rather than appending).

    Runs ``icacls`` out of process because the standard library has no NTFS
    ACL API.  Failures are deliberately swallowed (best-effort, like POSIX
    chmod) — callers still get the POSIX mode at creation time.
    """
    identity = _windows_identity()
    if identity is None:
        return
    for args in (
        ["icacls", os.fspath(path), "/inheritance:r"],
        ["icacls", os.fspath(path), "/grant:r", f"{identity}:F"],
    ):
        try:
            subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="mbcs",
                errors="replace",
                timeout=15,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            return  # best-effort; keep the caller moving


def ensure_private_directory(path: Path | str) -> Path:
    """Create ``path`` and make every newly created component user-private."""

    directory = Path(path)
    missing: list[Path] = []
    cursor = directory
    while not cursor.exists() and cursor != cursor.parent:
        missing.append(cursor)
        cursor = cursor.parent

    for component in reversed(missing):
        component.mkdir(mode=PRIVATE_DIRECTORY_MODE, exist_ok=True)
        _chmod(component, PRIVATE_DIRECTORY_MODE)

    directory.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIRECTORY_MODE)
    _chmod(directory, PRIVATE_DIRECTORY_MODE)
    return directory


def open_private_file(path: Path | str, flags: int) -> int:
    """Open a private regular file and return its owned file descriptor."""

    target = Path(path)
    ensure_private_directory(target.parent)
    descriptor = os.open(target, flags, PRIVATE_FILE_MODE)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, PRIVATE_FILE_MODE)
        else:
            _restrict_windows_acl(target)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def ensure_private_file(path: Path | str) -> None:
    """Repair one existing regular file without following symbolic links."""

    target = Path(path)
    try:
        metadata = target.lstat()
    except OSError:
        return
    if stat.S_ISREG(metadata.st_mode):
        _chmod(target, PRIVATE_FILE_MODE)


def harden_private_tree(root: Path | str) -> Path:
    """Repair a DeepCode-owned tree while refusing to traverse symlinks."""

    base = ensure_private_directory(root)

    for current, directories, files in os.walk(base, followlinks=False):
        current_path = Path(current)
        _chmod(current_path, PRIVATE_DIRECTORY_MODE)
        directories[:] = [
            name for name in directories if not (current_path / name).is_symlink()
        ]
        for name in directories:
            _chmod(current_path / name, PRIVATE_DIRECTORY_MODE)
        for name in files:
            ensure_private_file(current_path / name)
    return base


def _chmod(path: Path, mode: int) -> None:
    if os.name == "nt":
        _restrict_windows_acl(path)
        return
    try:
        os.chmod(path, mode, follow_symlinks=False)
    except (NotImplementedError, OSError):
        # Creation/opening will still fail naturally if the path is unusable.
        # Permission repair is best-effort for filesystems without chmod.
        pass


__all__ = [
    "PRIVATE_DIRECTORY_MODE",
    "PRIVATE_FILE_MODE",
    "ensure_private_directory",
    "ensure_private_file",
    "harden_private_tree",
    "open_private_file",
]
