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


class UnsafePrivateFileError(OSError):
    """A private-state path is not a regular file owned by this path entry."""


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
    """Open a private regular file without following a final symlink."""

    target = Path(path)
    ensure_private_directory(target.parent)
    descriptor = os.open(
        target,
        flags | getattr(os, "O_NOFOLLOW", 0),
        PRIVATE_FILE_MODE,
    )
    try:
        metadata = target.lstat()
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or not stat.S_ISREG(opened.st_mode):
            raise UnsafePrivateFileError("private storage path must be a regular file")
        if not os.path.samestat(metadata, opened):
            raise UnsafePrivateFileError(
                "private storage path changed while it was being opened"
            )
        if os.name != "nt":
            os.fchmod(descriptor, PRIVATE_FILE_MODE)
        else:
            _restrict_windows_acl(target)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def open_existing_private_file(
    path: Path | str,
    flags: int = os.O_RDONLY,
) -> int:
    """Open one existing regular file without following symbolic links.

    The pre-open ``lstat`` gives callers a clear fail-closed result for links
    and other special files. ``O_NOFOLLOW`` closes the common replacement
    race where the platform supports it, while the descriptor identity check
    covers platforms that do not expose that flag. Historical POSIX modes are
    repaired on the already-open descriptor before any bytes are read.
    """

    target = Path(path)
    metadata = target.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise UnsafePrivateFileError("private storage path must be a regular file")

    open_flags = flags | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(target, open_flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(metadata, opened):
            raise UnsafePrivateFileError(
                "private storage path changed while it was being opened"
            )
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
    "UnsafePrivateFileError",
    "ensure_private_directory",
    "ensure_private_file",
    "harden_private_tree",
    "open_existing_private_file",
    "open_private_file",
]
