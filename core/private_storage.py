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
    "ensure_private_directory",
    "ensure_private_file",
    "harden_private_tree",
    "open_private_file",
]
