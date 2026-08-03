"""User-private filesystem primitives for DeepCode runtime state.

DeepCode stores transcripts, credentials, execution state, and command history
under its user data directory.  Callers use these helpers instead of relying on
the process umask, which is commonly permissive on desktop systems.

POSIX permissions are repaired to ``0700`` for directories and ``0600`` for
regular files.  Windows access control is inherited from the user's profile;
the mode arguments are still supplied at creation time where supported.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


class UnsafePrivateFileError(OSError):
    """A private-state path is not a regular file owned by this path entry."""


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
        _chmod(target, PRIVATE_FILE_MODE)


def harden_private_tree(root: Path | str) -> Path:
    """Repair a DeepCode-owned tree while refusing to traverse symlinks."""

    base = ensure_private_directory(root)
    if os.name == "nt":
        return base

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
