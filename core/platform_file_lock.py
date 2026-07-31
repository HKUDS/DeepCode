"""Lowest-level cross-platform advisory file-lock primitives.

Callers own the file descriptor and decide how long the lock lives.  Keeping
the operating-system details here ensures every DeepCode lease uses the same
shared/exclusive and contention semantics.
"""

from __future__ import annotations

import errno
import os

if os.name == "nt":
    import ctypes
    import msvcrt
    import threading
    from ctypes import wintypes

    _LOCKFILE_FAIL_IMMEDIATELY = 0x00000001
    _LOCKFILE_EXCLUSIVE_LOCK = 0x00000002
    _ERROR_LOCK_VIOLATION = 33

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

    _active_overlapped: dict[int, _Overlapped] = {}
    _active_overlapped_lock = threading.Lock()

    def acquire_file_lock(
        descriptor: int,
        *,
        shared: bool,
        blocking: bool = True,
    ) -> bool:
        """Acquire a whole-file lock, returning ``False`` only on contention."""

        flags = 0 if shared else _LOCKFILE_EXCLUSIVE_LOCK
        if not blocking:
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
            with _active_overlapped_lock:
                _active_overlapped[descriptor] = overlapped
            return True
        error = ctypes.get_last_error()
        if not blocking and error == _ERROR_LOCK_VIOLATION:
            return False
        raise OSError(error, os.strerror(error))

    def release_file_lock(descriptor: int) -> None:
        with _active_overlapped_lock:
            overlapped = _active_overlapped.get(descriptor)
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
        with _active_overlapped_lock:
            _active_overlapped.pop(descriptor, None)

else:
    import fcntl

    def acquire_file_lock(
        descriptor: int,
        *,
        shared: bool,
        blocking: bool = True,
    ) -> bool:
        """Acquire a whole-file lock, returning ``False`` only on contention."""

        operation = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        if not blocking:
            operation |= fcntl.LOCK_NB
        try:
            fcntl.flock(descriptor, operation)
            return True
        except OSError as exc:
            if not blocking and exc.errno in {errno.EACCES, errno.EAGAIN}:
                return False
            raise

    def release_file_lock(descriptor: int) -> None:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


__all__ = ["acquire_file_lock", "release_file_lock"]
