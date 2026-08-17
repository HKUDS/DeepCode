"""Notify a connection when the user configuration file changes on disk."""

from __future__ import annotations

import threading
from collections.abc import Callable

from core.application.config_store import ConfigStore


class ConfigFileWatcher:
    """Poll the config file's content revision and fire on change.

    Polling keeps this dependency-free; the notification only nudges a
    client to refresh a settings surface it already has open, so sub-second
    latency buys nothing. The revision is the same content fingerprint the
    optimistic-concurrency check uses, so an atomic replace, an external
    edit, and a formatting-only rewrite all count as the changes they are.
    """

    def __init__(
        self,
        store: ConfigStore,
        callback: Callable[[str], None],
        *,
        interval_seconds: float = 1.0,
    ) -> None:
        self._store = store
        self._callback = callback
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last = store.revision()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="deepcode-config-watch",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval * 2)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                current = self._store.revision()
            except OSError:
                continue
            if current != self._last:
                self._last = current
                self._callback(current)
