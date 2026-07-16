"""Cross-process startup/recovery coordination for one application database."""

from __future__ import annotations

import errno
import os
import threading
from pathlib import Path


class ApplicationLease:
    """Hold a shared lifetime lease and serialize crash recovery.

    The first process opening a database obtains an exclusive startup lease and
    may recover orphaned executions. It then downgrades to a shared lifetime
    lease. Later CLI/Desktop processes join with a shared lease and must not
    reinterpret the first process's live work as crash residue.
    """

    def __init__(self, path: Path, descriptor: int, *, recovery_owner: bool) -> None:
        self.path = path
        self._descriptor = descriptor
        self._exclusive = recovery_owner
        self.recovery_owner = recovery_owner
        self._lock = threading.Lock()
        self._closed = False

    @classmethod
    def acquire(cls, database_path: Path) -> "ApplicationLease":
        path = database_path.with_name(f"{database_path.name}.application.lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            recovery_owner = _try_lock(descriptor, exclusive=True)
            if not recovery_owner:
                _lock(descriptor, exclusive=False)
            return cls(path, descriptor, recovery_owner=recovery_owner)
        except BaseException:
            os.close(descriptor)
            raise

    def downgrade(self) -> None:
        """Convert the exclusive startup lease into a shared lifetime lease."""

        with self._lock:
            if self._closed or not self._exclusive:
                return
            _unlock(self._descriptor)
            try:
                _lock(self._descriptor, exclusive=False)
            except BaseException:
                self._closed = True
                os.close(self._descriptor)
                raise
            self._exclusive = False

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                _unlock(self._descriptor)
            finally:
                os.close(self._descriptor)


if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _LOCKFILE_FAIL_IMMEDIATELY = 0x00000001
    _LOCKFILE_EXCLUSIVE_LOCK = 0x00000002
    _ERROR_LOCK_VIOLATION = 33
    _ERROR_IO_PENDING = 997

    class _Overlapped(ctypes.Structure):
        _fields_ = [
            ("Internal", wintypes.ULONG_PTR),
            ("InternalHigh", wintypes.ULONG_PTR),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.LockFileEx.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_Overlapped),
    ]
    _kernel32.LockFileEx.restype = wintypes.BOOL
    _kernel32.UnlockFileEx.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_Overlapped),
    ]
    _kernel32.UnlockFileEx.restype = wintypes.BOOL
    _WINDOWS_OVERLAPPED: dict[int, _Overlapped] = {}

    def _windows_lock(
        descriptor: int,
        *,
        exclusive: bool,
        nonblocking: bool,
    ) -> bool:
        flags = _LOCKFILE_EXCLUSIVE_LOCK if exclusive else 0
        if nonblocking:
            flags |= _LOCKFILE_FAIL_IMMEDIATELY
        overlapped = _Overlapped()
        handle = wintypes.HANDLE(msvcrt.get_osfhandle(descriptor))
        if _kernel32.LockFileEx(
            handle,
            flags,
            0,
            0xFFFFFFFF,
            0xFFFFFFFF,
            ctypes.byref(overlapped),
        ):
            _WINDOWS_OVERLAPPED[descriptor] = overlapped
            return True
        error = ctypes.get_last_error()
        if nonblocking and error in {_ERROR_LOCK_VIOLATION, _ERROR_IO_PENDING}:
            return False
        raise OSError(error, os.strerror(error))

    def _try_lock(descriptor: int, *, exclusive: bool) -> bool:
        return _windows_lock(
            descriptor,
            exclusive=exclusive,
            nonblocking=True,
        )

    def _lock(descriptor: int, *, exclusive: bool) -> None:
        _windows_lock(
            descriptor,
            exclusive=exclusive,
            nonblocking=False,
        )

    def _unlock(descriptor: int) -> None:
        overlapped = _WINDOWS_OVERLAPPED.pop(descriptor, None)
        if overlapped is None:
            return
        handle = wintypes.HANDLE(msvcrt.get_osfhandle(descriptor))
        if not _kernel32.UnlockFileEx(
            handle,
            0,
            0xFFFFFFFF,
            0xFFFFFFFF,
            ctypes.byref(overlapped),
        ):
            error = ctypes.get_last_error()
            raise OSError(error, os.strerror(error))

else:
    import fcntl

    def _try_lock(descriptor: int, *, exclusive: bool) -> bool:
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        try:
            fcntl.flock(descriptor, operation | fcntl.LOCK_NB)
            return True
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                return False
            raise

    def _lock(descriptor: int, *, exclusive: bool) -> None:
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(descriptor, operation)

    def _unlock(descriptor: int) -> None:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
