from __future__ import annotations

import io
import json
import os
import select
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from app_server.server import AppServer
from core.application import DeepCodeApplication
from core.domain import TrustState
from core.persistence.event_repository import EventRepository


def _request(request_id: int, method: str, params: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        ).encode()
        + b"\n"
    )


def _read(reader) -> dict[str, Any]:
    ready, _, _ = select.select([reader], [], [], 5.0)
    assert ready, "timed out waiting for App Server output"
    line = reader.readline()
    assert line
    return json.loads(line)


def _until(reader, predicate) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        message = _read(reader)
        if predicate(message):
            return message
    raise AssertionError("matching protocol message was not received")


def test_p4_file_git_and_terminal_protocol_round_trip(tmp_path: Path) -> None:
    workspace = tmp_path / "repository"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "config", "user.name", "DeepCode Test"], cwd=workspace, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "deepcode@example.test"],
        cwd=workspace,
        check=True,
    )
    (workspace / "readme.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "readme.txt"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=workspace, check=True)

    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
    thread = application.threads.start(project.id, title="P4 protocol")
    input_read, input_write = os.pipe()
    output_read, output_write = os.pipe()
    source = os.fdopen(input_read, "rb", buffering=0)
    writer = os.fdopen(input_write, "wb", buffering=0)
    reader = os.fdopen(output_read, "rb", buffering=0)
    sink = os.fdopen(output_write, "wb", buffering=0)
    server = threading.Thread(
        target=AppServer(application).serve, args=(source, sink), daemon=True
    )
    server.start()
    try:
        writer.write(
            _request(
                1,
                "initialize",
                {
                    "protocolVersion": "1.0",
                    "clientInfo": {"name": "p4-test", "version": "1.0"},
                },
            )
        )
        assert (
            _until(reader, lambda message: message.get("id") == 1)["result"][
                "protocolVersion"
            ]
            == "1.0"
        )

        writer.write(
            _request(2, "file/read", {"threadId": thread.id, "path": "readme.txt"})
        )
        file_result = _until(reader, lambda message: message.get("id") == 2)["result"][
            "file"
        ]
        assert file_result["content"] == "base\n"

        (workspace / "readme.txt").write_text("changed\n", encoding="utf-8")
        writer.write(_request(3, "git/diff", {"threadId": thread.id}))
        diff_result = _until(reader, lambda message: message.get("id") == 3)["result"]
        assert diff_result["files"][0]["path"] == "readme.txt"
        assert diff_result["files"][0]["additions"] == 1

        writer.write(
            _request(
                4,
                "git/discard",
                {
                    "threadId": thread.id,
                    "path": "readme.txt",
                    "expectedRevision": diff_result["files"][0]["revision"],
                },
            )
        )
        assert _until(reader, lambda message: message.get("id") == 4)["result"] == {
            "discarded": True,
            "path": "readme.txt",
        }
        assert (workspace / "readme.txt").read_text(encoding="utf-8") == "base\n"

        writer.write(
            _request(
                5,
                "terminal/create",
                {"threadId": thread.id, "columns": 80, "rows": 24},
            )
        )
        terminal = _until(reader, lambda message: message.get("id") == 5)["result"][
            "terminal"
        ]
        writer.write(
            _request(
                6,
                "terminal/write",
                {
                    "threadId": thread.id,
                    "terminalId": terminal["terminalId"],
                    "data": "printf 'RPC_TERMINAL_OK\\n'; exit\n",
                },
            )
        )
        saw_response = saw_output = saw_exit = False
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not (
            saw_response and saw_output and saw_exit
        ):
            message = _read(reader)
            saw_response |= message.get("id") == 6
            if message.get("method") == "terminal.output":
                assert message["params"]["threadId"] == thread.id
                saw_output |= "RPC_TERMINAL_OK" in message["params"]["data"]
            saw_exit |= message.get("method") == "terminal.exit"
        assert saw_response and saw_output and saw_exit

        writer.write(_request(7, "shutdown", {}))
        assert _until(reader, lambda message: message.get("id") == 7)["result"][
            "accepted"
        ]
        server.join(timeout=3)
        assert not server.is_alive()
    finally:
        writer.close()
        source.close()
        sink.close()
        reader.close()


def test_oversized_result_returns_a_protocol_error_without_stopping_server(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "large.txt").write_text("x" * 10_000, encoding="utf-8")
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
    thread = application.threads.start(project.id, title="Bounded response")
    source = io.BytesIO(
        _request(
            1,
            "initialize",
            {
                "protocolVersion": "1.0",
                "clientInfo": {"name": "limit-test", "version": "1.0"},
            },
        )
        + _request(
            2,
            "file/read",
            {"threadId": thread.id, "path": "large.txt", "maxBytes": 10_000},
        )
        + _request(3, "shutdown", {})
    )
    sink = io.BytesIO()

    assert AppServer(application, max_message_bytes=2_048).serve(source, sink) == 0
    messages = [json.loads(line) for line in sink.getvalue().splitlines()]
    by_id = {message.get("id"): message for message in messages if "id" in message}
    assert by_id[1]["result"]["protocolVersion"] == "1.0"
    assert by_id[2]["error"]["data"]["code"] == "RESPONSE_TOO_LARGE"
    assert by_id[3]["result"]["accepted"] is True


def test_event_replay_is_split_into_byte_bounded_cursor_pages(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
    thread = application.threads.start(project.id, title="Paged replay")
    with application.database.transaction() as connection:
        repository = EventRepository(connection)
        for index in range(12):
            repository.append(
                thread_id=thread.id,
                type="test.large",
                payload={"index": index, "text": "x" * 700},
            )
    expected = [
        event.sequence for event in application.events.replay(thread.id, limit=1000)
    ]

    input_read, input_write = os.pipe()
    output_read, output_write = os.pipe()
    source = os.fdopen(input_read, "rb", buffering=0)
    writer = os.fdopen(input_write, "wb", buffering=0)
    reader = os.fdopen(output_read, "rb", buffering=0)
    sink = os.fdopen(output_write, "wb", buffering=0)
    server = threading.Thread(
        target=AppServer(application, max_message_bytes=2_048).serve,
        args=(source, sink),
        daemon=True,
    )
    server.start()
    try:
        writer.write(
            _request(
                1,
                "initialize",
                {
                    "protocolVersion": "1.0",
                    "clientInfo": {"name": "replay-test", "version": "1.0"},
                },
            )
        )
        initialized = _until(reader, lambda message: message.get("id") == 1)
        assert initialized["result"]["capabilities"]["maxMessageBytes"] == 2_048

        collected: list[int] = []
        after = 0
        page_count = 0
        request_id = 2
        while True:
            writer.write(
                _request(
                    request_id,
                    "event/replay",
                    {"threadId": thread.id, "after": after, "limit": 1000},
                )
            )
            response = _until(
                reader, lambda message, target=request_id: message.get("id") == target
            )
            assert "error" not in response
            page = response["result"]
            page_count += 1
            collected.extend(event["sequence"] for event in page["events"])
            if not page["hasMore"]:
                assert page["nextAfter"] is None
                break
            assert page["events"]
            assert page["nextAfter"] == page["events"][-1]["sequence"]
            assert page["nextAfter"] > after
            after = page["nextAfter"]
            request_id += 1

        assert page_count > 1
        assert collected == expected
        writer.write(_request(request_id + 1, "shutdown", {}))
        assert (
            _until(reader, lambda message: message.get("id") == request_id + 1)[
                "result"
            ]["accepted"]
            is True
        )
        server.join(timeout=3)
        assert not server.is_alive()
    finally:
        writer.close()
        source.close()
        sink.close()
        reader.close()


def test_single_replay_event_larger_than_transport_limit_is_reported(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
    thread = application.threads.start(project.id, title="Single large event")
    with application.database.transaction() as connection:
        oversized = EventRepository(connection).append(
            thread_id=thread.id,
            type="test.oversized",
            payload={"text": "x" * 10_000},
        )
    source = io.BytesIO(
        _request(
            1,
            "initialize",
            {
                "protocolVersion": "1.0",
                "clientInfo": {"name": "limit-test", "version": "1.0"},
            },
        )
        + _request(
            2,
            "event/replay",
            {
                "threadId": thread.id,
                "after": oversized.sequence - 1,
                "limit": 1000,
            },
        )
        + _request(3, "shutdown", {})
    )
    sink = io.BytesIO()

    assert AppServer(application, max_message_bytes=2_048).serve(source, sink) == 0
    messages = [json.loads(line) for line in sink.getvalue().splitlines()]
    by_id = {message.get("id"): message for message in messages if "id" in message}
    assert by_id[2]["error"]["data"]["code"] == "RESPONSE_TOO_LARGE"
    assert by_id[3]["result"]["accepted"] is True
