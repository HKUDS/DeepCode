"""Small cross-platform advisory locks for mutations and durable leases."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class FileLease:
    """Hold a cross-process advisory lock until :meth:`close` is called.

    POSIX supports shared leases, which lets multiple readers keep a Session
    open while a permanent deletion requests an exclusive lease.  ``msvcrt``
    has no shared byte-range lock, so Windows deliberately serialises those
    uncommon lifetime leases; correctness is more important than parallel
    Session ownership on that platform.
    """

    def __init__(self, descriptor: int, *, windows: bool) -> None:
        self._descriptor = descriptor
        self._windows = windows
        self._closed = False
        self._lock = threading.Lock()

    @classmethod
    def acquire(
        cls,
        path: Path,
        *,
        shared: bool,
        blocking: bool = True,
    ) -> "FileLease | None":
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            if os.name == "nt":
                import msvcrt

                if os.path.getsize(path) == 0:
                    os.write(descriptor, b"\0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
                try:
                    msvcrt.locking(descriptor, mode, 1)
                except OSError:
                    if not blocking:
                        os.close(descriptor)
                        return None
                    raise
                return cls(descriptor, windows=True)

            import fcntl

            operation = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
            if not blocking:
                operation |= fcntl.LOCK_NB
            try:
                fcntl.flock(descriptor, operation)
            except BlockingIOError:
                os.close(descriptor)
                return None
            return cls(descriptor, windows=False)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                if self._windows:
                    import msvcrt

                    os.lseek(self._descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(self._descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._descriptor, fcntl.LOCK_UN)
            finally:
                os.close(self._descriptor)

    def __enter__(self) -> "FileLease":
        return self

    def __exit__(self, *_exc_info) -> None:
        self.close()


@contextmanager
def exclusive_file_lock(path: Path) -> Iterator[None]:
    """Serialize a bounded mutation across DeepCode processes."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        if os.name == "nt":
            import msvcrt

            if os.path.getsize(path) == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


__all__ = ["FileLease", "exclusive_file_lock"]
