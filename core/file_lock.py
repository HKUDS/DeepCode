"""Small cross-platform advisory locks for mutations and durable leases."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Self

from core.platform_file_lock import acquire_file_lock, release_file_lock
from core.private_storage import ensure_private_directory, open_private_file


class FileLease:
    """Hold a cross-process advisory lock until :meth:`close` is called.

    Shared leases let multiple readers keep a Session open while a permanent
    deletion requests an exclusive lease.  Both POSIX and Windows preserve
    those semantics.
    """

    def __init__(self, descriptor: int) -> None:
        self._descriptor = descriptor
        self._closed = False
        self._lock = threading.Lock()

    @classmethod
    def acquire(
        cls,
        path: Path,
        *,
        shared: bool,
        blocking: bool = True,
    ) -> FileLease | None:
        ensure_private_directory(path.parent)
        descriptor = open_private_file(path, os.O_RDWR | os.O_CREAT)
        try:
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            acquired = acquire_file_lock(
                descriptor,
                shared=shared,
                blocking=blocking,
            )
            if not acquired:
                os.close(descriptor)
                return None
            return cls(descriptor)
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
                release_file_lock(self._descriptor)
            finally:
                os.close(self._descriptor)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc_info) -> None:
        self.close()


@contextmanager
def exclusive_file_lock(path: Path) -> Iterator[None]:
    """Serialize a bounded mutation across DeepCode processes."""

    ensure_private_directory(path.parent)
    descriptor = open_private_file(path, os.O_RDWR | os.O_CREAT)
    try:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        acquire_file_lock(descriptor, shared=False)
        try:
            yield
        finally:
            release_file_lock(descriptor)
    finally:
        os.close(descriptor)


__all__ = ["FileLease", "exclusive_file_lock"]
