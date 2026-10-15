"""Exercise the documented module CLI, its shell source and persistent state."""

import json
import subprocess
import sys

import pytest

from core.schedule.monitor import MonitorStore


@pytest.mark.parametrize("as_json", [False, True])
def test_source_failure_returns_nonzero_without_changing_state(tmp_path, as_json):
    db = tmp_path / "monitor.sqlite3"
    store = MonitorStore(str(db))
    store.save_job("sample", "previous-hash", "previous-output")
    previous = store.get_job("sample")
    command = [
        sys.executable,
        "-m",
        "core.schedule.monitor",
        "--job-id",
        "sample",
        "--script",
        "exit 3",
        "--db",
        str(db),
    ]
    if as_json:
        command.append("--json")
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert store.get_job("sample") == previous
    if as_json:
        assert json.loads(result.stdout)["error"]
    else:
        assert "ERROR" in result.stderr
    assert result.returncode == 1


def test_json_success_and_unchanged_source_return_zero(tmp_path):
    command = [
        sys.executable,
        "-m",
        "core.schedule.monitor",
        "--job-id",
        "sample",
        "--script",
        "echo stable",
        "--db",
        str(tmp_path / "monitor.sqlite3"),
        "--json",
    ]
    for changed in (True, False):
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0
        outcome = json.loads(result.stdout)
        assert outcome["changed"] is changed
        assert not outcome["error"]
