"""The config watcher fires on real file changes and stays quiet otherwise."""

import time
from pathlib import Path

from app_server.config_watch import ConfigFileWatcher
from core.application.config_store import ConfigStore


def _wait_for(condition, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_watcher_fires_once_per_content_change(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "deepcode_config.json")
    seen: list[str] = []
    watcher = ConfigFileWatcher(store, seen.append, interval_seconds=0.05)
    watcher.start()
    try:
        store.mutate(lambda current: {**current, "agents": {}})
        assert _wait_for(lambda: len(seen) == 1)
        assert seen[0] == store.revision()

        # No further change → no further notification.
        time.sleep(0.2)
        assert len(seen) == 1

        store.mutate(
            lambda current: {**current, "agents": {"defaults": {"model": "gpt-5"}}}
        )
        assert _wait_for(lambda: len(seen) == 2)
    finally:
        watcher.stop()
