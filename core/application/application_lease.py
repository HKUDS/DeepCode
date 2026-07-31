"""Cross-process startup/recovery coordination for one application database."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from core.platform_file_lock import acquire_file_lock, release_file_lock

_ACQUIRE_RETRY_INTERVAL = 0.01


class ApplicationLease:
    """Hold a shared lifetime lease and serialize crash recovery.

    The first process opening a database obtains an exclusive startup lease and
    may recover orphaned executions. It then downgrades to a shared lifetime
    lease. Later CLI/Desktop processes join with a shared lease and must not
    reinterpret the first process's live work as crash residue.
    """

    def __init__(
        self,
        path: Path,
        transition_path: Path,
        descriptor: int,
        *,
        recovery_owner: bool,
    ) -> None:
        self.path = path
        self.transition_path = transition_path
        self._descriptor = descriptor
        self._exclusive = recovery_owner
        self.recovery_owner = recovery_owner
        self._lock = threading.Lock()
        self._closed = False

    @classmethod
    def acquire(cls, database_path: Path) -> ApplicationLease:
        path = database_path.with_name(f"{database_path.name}.application.lock")
        transition_path = database_path.with_name(
            f"{database_path.name}.application-transition.lock"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            while True:
                with _transition_gate(transition_path):
                    recovery_owner = acquire_file_lock(
                        descriptor,
                        shared=False,
                        blocking=False,
                    )
                    if recovery_owner:
                        return cls(
                            path,
                            transition_path,
                            descriptor,
                            recovery_owner=True,
                        )
                    joined = acquire_file_lock(
                        descriptor,
                        shared=True,
                        blocking=False,
                    )
                    if joined:
                        return cls(
                            path,
                            transition_path,
                            descriptor,
                            recovery_owner=False,
                        )
                time.sleep(_ACQUIRE_RETRY_INTERVAL)
        except BaseException:
            os.close(descriptor)
            raise

    def downgrade(self) -> None:
        """Convert the exclusive startup lease into a shared lifetime lease."""

        with self._lock:
            if self._closed or not self._exclusive:
                return
            with _transition_gate(self.transition_path):
                if os.name == "nt":
                    release_file_lock(self._descriptor)
                try:
                    acquire_file_lock(self._descriptor, shared=True)
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
                release_file_lock(self._descriptor)
            finally:
                os.close(self._descriptor)


@contextmanager
def _transition_gate(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
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
