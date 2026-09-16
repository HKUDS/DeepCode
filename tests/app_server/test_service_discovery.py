"""Discovery records stay consistent while another process reads or updates them."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from app_server import service_state
from app_server.service_state import ServiceFiles, ServiceRecord


# Pause with a real discovery file open, or between the two publication writes.
# The parent releases stdin only after checking the competing operation blocks.
_PAUSED_PROCESS = """
import json, os, sys
from pathlib import Path
from app_server import service_state as state
files = state.ServiceFiles(Path(sys.argv[1]))
def pause():
    print('paused', flush=True)
    sys.stdin.readline()
if sys.argv[2] == 'read':
    original = state._read
    def read(path):
        if path != files.record:
            return original(path)
        with os.fdopen(state.open_existing_private_file(path), 'r', encoding='utf-8') as stream:
            pause()
            return stream.read(16385)
    state._read = read
    record, token = files.read()
    print(json.dumps([record.instance_id, token]), flush=True)
else:
    original = state._write
    def write(path, content):
        original(path, content)
        if path == files.token:
            pause()
    state._write = write
    files.publish(state.ServiceRecord('c' * 32, str(files.database), os.getpid(), 3081), 'd' * 64)
"""


@pytest.mark.parametrize("operation", ["clear", "publish", "read"])
def test_discovery_operations_serialize_across_processes(tmp_path, operation):
    files = ServiceFiles(tmp_path / "state.sqlite3")
    old = ServiceRecord("a" * 32, str(files.database), os.getpid(), 3081)
    new = ServiceRecord("c" * 32, str(files.database), os.getpid(), 3081)
    files.publish(old, "b" * 64)
    started = threading.Event()

    def compete():
        started.set()
        if operation == "publish":
            return files.publish(new, "d" * 64)
        return getattr(files, operation)()

    with ThreadPoolExecutor(max_workers=2) as pool:
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                _PAUSED_PROCESS,
                str(files.database),
                "publish" if operation == "read" else "read",
            ],
            cwd=Path(__file__).resolve().parents[2],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert pool.submit(process.stdout.readline).result(10).strip() == "paused"
            pending = pool.submit(compete)
            assert started.wait(5)
            with pytest.raises(TimeoutError):
                pending.result(timeout=0.2)
            stdout, stderr = process.communicate(input="\n", timeout=10)
            assert process.returncode == 0, stderr
            result = pending.result(timeout=10)
            if operation == "read":
                assert (result[0].instance_id, result[1]) == ("c" * 32, "d" * 64)
            else:
                assert json.loads(stdout) == [old.instance_id, "b" * 64]
                assert files.read() == (
                    (new, "d" * 64) if operation == "publish" else None
                )
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)


def test_discovery_permission_errors_remain_visible_and_release_the_lock(
    tmp_path, monkeypatch
):
    files = ServiceFiles(tmp_path / "state.sqlite3")
    record = ServiceRecord("a" * 32, str(files.database), os.getpid(), 3081)
    files.publish(record, "b" * 64)

    def denied(_path):
        raise PermissionError("Discovery access denied")

    with monkeypatch.context() as patch:
        patch.setattr(service_state, "_read", denied)
        with pytest.raises(PermissionError, match="Discovery access denied"):
            files.read()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(files.clear).result(timeout=5)
    assert files.read() is None
