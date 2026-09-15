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
import threading
from functools import lru_cache
from pathlib import Path

PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


class UnsafePrivateFileError(OSError):
    """A private-state path is not a regular file owned by this path entry."""


_WINDOWS_BROAD_ACCESS_SIDS = (
    "*S-1-1-0",  # Everyone
    "*S-1-5-11",  # Authenticated Users
    "*S-1-5-32-545",  # BUILTIN\\Users
)


@lru_cache(maxsize=1)
def _windows_identity() -> str | None:
    """Return the fully-qualified current user (``DOMAIN\\user``) on Windows."""

    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        get_name = ctypes.WinDLL("secur32", use_last_error=True).GetUserNameExW
        get_name.argtypes = (
            wintypes.ULONG,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.ULONG),
        )
        get_name.restype = wintypes.BOOL
        size = wintypes.ULONG(0)
        # NameSamCompatible (2) yields DOMAIN\user, the form icacls accepts.
        get_name(2, None, ctypes.byref(size))
        if size.value <= 1:
            return None
        buffer = ctypes.create_unicode_buffer(size.value)
        if not get_name(2, buffer, ctypes.byref(size)):
            return None
        principal = buffer.value.strip()
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    return principal or None


@lru_cache(maxsize=1)
def _windows_icacls() -> str | None:
    """Resolve the trusted system icacls executable without consulting PATH."""

    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        get_system_directory = ctypes.WinDLL(
            "kernel32", use_last_error=True
        ).GetSystemDirectoryW
        get_system_directory.argtypes = (wintypes.LPWSTR, wintypes.UINT)
        get_system_directory.restype = wintypes.UINT
        buffer = ctypes.create_unicode_buffer(32_768)
        length = get_system_directory(buffer, len(buffer))
        if length <= 0 or length >= len(buffer):
            return None
        executable = Path(buffer.value) / "icacls.exe"
        return os.fspath(executable) if executable.is_file() else None
    except (AttributeError, OSError, TypeError, ValueError):
        return None


_ICACLS_TIMEOUT_SECONDS = 15.0
# How long the caller waits for the whole spawn before giving up and leaving the
# ACLs as they are.  See _run_icacls for why that wait has to exist.
_ICACLS_ABANDON_AFTER_SECONDS = _ICACLS_TIMEOUT_SECONDS + 5.0


def _run_icacls(executable: str, path: Path, *arguments: str) -> bool:
    """Run one bounded icacls operation and report whether it succeeded.

    Bounded end to end: both the child's run *and* its creation are covered, because
    the caller must never be able to hang here.  ``subprocess``'s own ``timeout``
    only starts once the process exists, and ``CreateProcess`` itself is unbounded,
    so the spawn runs on a daemon worker thread that may be abandoned mid-flight.
    """

    completed = threading.Event()
    succeeded: list[bool] = []

    def _attempt() -> None:
        try:
            subprocess.run(
                [executable, os.fspath(path), *arguments],
                capture_output=True,
                text=True,
                encoding="mbcs",
                errors="replace",
                timeout=_ICACLS_TIMEOUT_SECONDS,
                check=True,
                # A detached service has no console to inherit. Avoid allocating
                # a new console for every ACL helper it launches.
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                stdin=subprocess.DEVNULL,
            )
            succeeded.append(True)
        except (OSError, subprocess.SubprocessError):
            succeeded.append(False)
        finally:
            completed.set()

    worker = threading.Thread(target=_attempt, name="deepcode-icacls", daemon=True)
    worker.start()
    if not completed.wait(_ICACLS_ABANDON_AFTER_SECONDS):
        return False
    return bool(succeeded and succeeded[0])


def _restrict_windows_acl(path: Path) -> None:
    """Restrict ``path`` to the current user, failing safe.

    The current user is granted full control **before** inherited access
    entries are stripped.  If the grant fails (service account, transient
    timeout, ...) the inherited ACLs are left untouched so the path stays
    accessible to the caller; the previous strip-first order could leave a
    path with no usable ACE and make it unopenable.
    """

    identity = _windows_identity()
    executable = _windows_icacls()
    if identity is None or executable is None:
        return
    grant = f"{identity}:{'(OI)(CI)' if path.is_dir() else ''}F"
    if not _run_icacls(executable, path, "/grant:r", grant):
        # Fail safe: keep the inherited ACLs; the path stays accessible.
        return
    if not _run_icacls(executable, path, "/inheritance:r"):
        # Strip failed: the path is merely less restricted, still usable.
        return
    # ``/inheritance:r`` removes inherited ACEs but deliberately leaves
    # explicit entries alone. Remove the broad built-in principals as a final
    # best-effort step so hardening also repairs legacy explicit grants.
    _run_icacls(executable, path, "/remove", *_WINDOWS_BROAD_ACCESS_SIDS)


def _open_private_descriptor(target: Path, flags: int) -> tuple[int, bool]:
    """Open ``target`` and atomically report whether this call created it."""

    open_flags = flags | getattr(os, "O_NOFOLLOW", 0)
    if not flags & os.O_CREAT or flags & os.O_EXCL:
        descriptor = os.open(target, open_flags, PRIVATE_FILE_MODE)
        return descriptor, bool(flags & os.O_CREAT)

    # A pre-open exists() check races with another creator. First attempt an
    # exclusive creation; if the path already exists, open it without O_CREAT
    # so a concurrent delete is observable and can retry the decision.
    while True:
        try:
            descriptor = os.open(
                target,
                open_flags | os.O_EXCL,
                PRIVATE_FILE_MODE,
            )
            return descriptor, True
        except FileExistsError:
            try:
                descriptor = os.open(
                    target,
                    open_flags & ~os.O_CREAT,
                    PRIVATE_FILE_MODE,
                )
            except FileNotFoundError:
                continue
            return descriptor, False


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
    ensure_private_directory(target.parent)
    descriptor, created = _open_private_descriptor(target, flags)
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
    if _is_directory_link(base):
        raise UnsafePrivateFileError("private storage root must not be a link")

    for current, directories, files in os.walk(base, followlinks=False):
        current_path = Path(current)
        _chmod(current_path, PRIVATE_DIRECTORY_MODE, force=True)
        directories[:] = [
            name for name in directories if not _is_directory_link(current_path / name)
        ]
        for name in directories:
            _chmod(current_path / name, PRIVATE_DIRECTORY_MODE, force=True)
        for name in files:
            ensure_private_file(current_path / name)
    return base


def _is_directory_link(path: Path) -> bool:
    """Treat symlinks and Windows junctions as traversal boundaries."""

    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(callable(is_junction) and is_junction())
    except OSError:
        # An unreadable/replaced entry is unsafe to traverse.
        return True


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


def atomic_write_private_json(path: Path, value: object) -> None:
    """Publish private JSON durably; callers own any cross-process mutation lock."""
    import json
    import uuid

    ensure_private_directory(path.parent)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    payload = (
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode()
    descriptor = open_private_file(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
