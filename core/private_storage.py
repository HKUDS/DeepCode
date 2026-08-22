"""User-private filesystem primitives for DeepCode runtime state.

DeepCode stores transcripts, credentials, execution state, and command history
under its user data directory.  Callers use these helpers instead of relying on
the process umask, which is commonly permissive on desktop systems.

POSIX permissions are repaired to ``0700`` for directories and ``0600`` for
regular files.  On Windows the current user is granted full control and the
inherited access entries are then stripped; the restriction is applied in a
fail-safe order so a failed grant leaves the inherited ACLs untouched and the
path stays accessible.
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
    """Return the fully-qualified current user (``DOMAIN\\user``) on Windows."""

    if os.name != "nt":
        return None
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
    """Restrict ``path`` to the current user, failing safe.

    The current user is granted full control **before** inherited access
    entries are stripped.  If the grant fails (service account, transient
    timeout, ...) the inherited ACLs are left untouched so the path stays
    accessible to the caller; the previous strip-first order could leave a
    path with no usable ACE and make it unopenable.
    """

    identity = _windows_identity()
    if identity is None:
        return
    try:
        subprocess.run(
            ["icacls", os.fspath(path), "/grant:r", f"{identity}:F"],
            capture_output=True,
            text=True,
            encoding="mbcs",
            errors="replace",
            timeout=15,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        # Fail safe: keep the inherited ACLs; the path stays accessible.
        return
    try:
        subprocess.run(
            ["icacls", os.fspath(path), "/inheritance:r"],
            capture_output=True,
            text=True,
            encoding="mbcs",
            errors="replace",
            timeout=15,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        # Strip failed: the path is merely less restricted, still usable.
        pass


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
        _chmod(component, PRIVATE_DIRECTORY_MODE, force=True)

    was_missing = not directory.exists()
    directory.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIRECTORY_MODE)
    # Restrict only what this call actually created. An existing directory was
    # restricted at its own creation; re-running icacls on it on every open
    # costs two subprocesses without changing the ACL.
    _chmod(directory, PRIVATE_DIRECTORY_MODE, force=was_missing)
    return directory


def open_private_file(path: Path | str, flags: int) -> int:
    """Open a private regular file without following a final symlink."""

    target = Path(path)
    # Only restrict a *newly created* file. An existing file was already
    # restricted at creation; re-running icacls on every open costs two
    # subprocesses per call (and a full tree walk many times over) without
    # changing the ACL (maintainer feedback on the earlier ACL PR).
    created = not target.exists()
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
        if created:
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
        _chmod(target, PRIVATE_FILE_MODE, force=True)


def harden_private_tree(root: Path | str) -> Path:
    """Repair a DeepCode-owned tree while refusing to traverse symlinks."""

    base = ensure_private_directory(root)

    for current, directories, files in os.walk(base, followlinks=False):
        current_path = Path(current)
        _chmod(current_path, PRIVATE_DIRECTORY_MODE, force=True)
        directories[:] = [
            name for name in directories if not (current_path / name).is_symlink()
        ]
        for name in directories:
            _chmod(current_path / name, PRIVATE_DIRECTORY_MODE, force=True)
        for name in files:
            ensure_private_file(current_path / name)
    return base


def _chmod(path: Path, mode: int, *, force: bool = False) -> None:
    if os.name == "nt":
        # harden_private_tree (force=True) deliberately re-applies the
        # restriction even to existing paths (it repairs legacy trees whose
        # ACLs may be absent or permissive). Default callers pass force=False
        # so an already-restricted path is not re-churned on every open.
        if force:
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
