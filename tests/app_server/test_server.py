import io
import json
import os
import select
import subprocess
import sys
import threading
import asyncio
import time
from pathlib import Path
from typing import Any

from app_server.server import AppServer
from core.application import DeepCodeApplication
from core.domain import TrustState
from core.events import (
    AgentMessage,
    Event,
    TaskComplete,
    ToolCompleted,
    ToolStarted,
    TurnStarted,
)
from core.sessions import SessionStore


def _request(request_id: int, method: str, params: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        ).encode()
        + b"\n"
    )


def _messages(sink: io.BytesIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in sink.getvalue().splitlines()]


def test_server_requires_initialize_and_emits_live_thread_event(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(str(workspace))
    source = io.BytesIO(
        _request(1, "project/list", {})
        + _request(
            2,
            "initialize",
            {
                "protocolVersion": "1.0",
                "clientInfo": {"name": "test", "version": "1.0"},
            },
        )
        + _request(
            3,
            "thread/start",
            {"projectId": project.id, "title": "RPC thread"},
        )
        + _request(4, "shutdown", {})
    )
    sink = io.BytesIO()

    assert AppServer(application).serve(source, sink) == 0
    messages = _messages(sink)
    assert messages[0]["error"]["data"]["code"] == "NOT_INITIALIZED"
    assert messages[1]["result"]["protocolVersion"] == "1.0"
    assert messages[2]["result"]["thread"]["title"] == "RPC thread"
    assert messages[3]["method"] == "thread.updated"
    assert messages[3]["params"]["type"] == "thread.created"
    assert messages[4]["result"] == {"accepted": True}


def test_notification_errors_do_not_produce_responses(tmp_path: Path) -> None:
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    source = io.BytesIO(
        json.dumps({"jsonrpc": "2.0", "method": "unknown", "params": {}}).encode()
        + b"\n"
    )
    sink = io.BytesIO()
    AppServer(application).serve(source, sink)
    assert sink.getvalue() == b""


def test_thread_list_and_resume_use_the_shared_session_store(tmp_path: Path) -> None:
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    store = SessionStore(tmp_path / "sessions")
    session_a = store.create_session(
        title="From CLI A",
        metadata={"kind": "tui", "workspace": str(workspace_a)},
    )
    store.append_message(session_a.session_id, "user", "question A")
    store.append_message(session_a.session_id, "assistant", "answer A")
    session_b = store.create_session(
        title="From CLI B",
        metadata={"kind": "tui", "workspace": str(workspace_b)},
    )
    store.append_message(session_b.session_id, "user", "question B")
    source_path = store.root / session_a.session_id / "session.jsonl"
    source_before = source_path.read_bytes()

    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_store=store,
    )
    source = io.BytesIO(
        _request(
            1,
            "initialize",
            {
                "protocolVersion": "1.0",
                "clientInfo": {"name": "session-test", "version": "1.0"},
            },
        )
        + _request(2, "thread/list", {"limit": 20})
        + _request(3, "thread/list", {"cwd": str(workspace_a)})
        + _request(4, "thread/resume", {"sessionId": session_a.session_id})
        + _request(5, "shutdown", {})
    )
    sink = io.BytesIO()

    assert AppServer(application).serve(source, sink) == 0
    responses = [message for message in _messages(sink) if "id" in message]
    assert {thread["id"] for thread in responses[1]["result"]["threads"]} == {
        session_a.session_id,
        session_b.session_id,
    }
    assert [thread["id"] for thread in responses[2]["result"]["threads"]] == [
        session_a.session_id
    ]
    assert responses[3]["result"]["thread"]["id"] == session_a.session_id
    assert source_path.read_bytes() == source_before


def test_settings_and_thread_model_are_real_shared_configuration(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "deepcode-home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(str(workspace))
    thread = application.threads.start(project.id, title="Model selection")
    source = io.BytesIO(
        _request(
            1,
            "initialize",
            {
                "protocolVersion": "1.0",
                "clientInfo": {"name": "settings-test", "version": "1.0"},
            },
        )
        + _request(2, "settings/read", {})
        + _request(
            3,
            "settings/update",
            {"patch": {"security": {"permissionMode": "plan"}}},
        )
        + _request(
            4,
            "thread/model",
            {"threadId": thread.id, "model": "gpt-5-mini"},
        )
        + _request(5, "shutdown", {})
    )
    sink = io.BytesIO()

    assert AppServer(application).serve(source, sink) == 0
    responses = {
        message["id"]: message
        for message in _messages(sink)
        if "id" in message and "result" in message
    }
    assert responses[2]["result"]["settings"]["permissionModeExplicit"] is False
    assert responses[3]["result"]["settings"]["security"]["permissionMode"] == "plan"
    assert responses[3]["result"]["settings"]["permissionModeExplicit"] is True
    assert responses[4]["result"]["thread"]["model"] == "gpt-5-mini"

    persisted = json.loads((home / "deepcode_config.json").read_text())
    assert persisted["security"]["permissionMode"] == "plan"
    canonical = application.session_store.get_session(thread.id)
    assert canonical is not None
    assert canonical.metadata["model"] == "gpt-5-mini"


def test_management_methods_round_trip_real_project_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "deepcode-home"
    workspace = tmp_path / "workspace"
    skill_dir = workspace / ".deepcode" / "skills" / "review"
    skill_dir.mkdir(parents=True)
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: review\n"
        "description: Review a change carefully\n"
        "allowed-tools: read, grep\n"
        "---\n"
        "Report concrete evidence.\n",
        encoding="utf-8",
    )
    hooks_path = workspace / ".deepcode" / "hooks.json"
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python3 check.py",
                                    "timeout": 15,
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    home.mkdir()
    (home / "deepcode_config.json").write_text(
        json.dumps(
            {
                "providers": {"openai": {"apiKey": "never-return-this"}},
                "tools": {
                    "mcpServers": {
                        "demo": {
                            "type": "stdio",
                            "command": "python3",
                            "args": ["server.py", "--token", "mcp-secret"],
                            "env": {"API_TOKEN": "mcp-secret"},
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    source = io.BytesIO(
        _request(
            1,
            "initialize",
            {
                "protocolVersion": "1.0",
                "clientInfo": {"name": "management-test", "version": "1.0"},
            },
        )
        + _request(2, "skills/list", {"projectId": project.id})
        + _request(
            3,
            "skill/read",
            {"projectId": project.id, "name": "review"},
        )
        + _request(4, "hooks/list", {"projectId": project.id})
        + _request(5, "mcp/list", {"projectId": project.id})
        + _request(6, "diagnostics/read", {"projectId": project.id})
        + _request(
            7,
            "settings/update",
            {
                "projectId": project.id,
                "scope": "project",
                "patch": {"security": {"sandbox": False}},
            },
        )
        + _request(
            8,
            "mcp/upsert",
            {
                "projectId": project.id,
                "scope": "project",
                "name": "project-tools",
                "server": {"type": "stdio", "command": "python3"},
            },
        )
        + _request(
            9,
            "mcp/remove",
            {
                "projectId": project.id,
                "scope": "project",
                "name": "project-tools",
            },
        )
        + _request(10, "shutdown", {})
    )
    sink = io.BytesIO()
    try:
        assert AppServer(application).serve(source, sink) == 0
        responses = {
            message["id"]: message["result"]
            for message in _messages(sink)
            if "id" in message and "result" in message
        }
        assert any(skill["name"] == "review" for skill in responses[2]["skills"])
        assert "concrete evidence" in responses[3]["skill"]["instructions"]
        assert any(
            hook["eventName"] == "PreToolUse" and hook["command"] == "python3 check.py"
            for hook in responses[4]["hooks"]
        )
        demo = next(
            server for server in responses[5]["servers"] if server["name"] == "demo"
        )
        assert demo["args"][-1] == "••••••"
        assert responses[6]["diagnostics"]["projectPath"] == str(workspace)
        assert responses[7]["settings"]["security"]["sandbox"] is False
        assert {server["name"] for server in responses[8]["servers"]} == {
            "demo",
            "project-tools",
        }
        assert "project-tools" not in {
            server["name"] for server in responses[9]["servers"]
        }
        assert "demo" in {server["name"] for server in responses[9]["servers"]}
        serialized = sink.getvalue().decode()
        assert "never-return-this" not in serialized
        assert "mcp-secret" not in serialized
    finally:
        application.close()


class _AutomationSession:
    def load_history(self, _messages) -> None:
        return None

    async def run_stream(self, _op):
        yield Event("1", TurnStarted())
        yield Event("2", AgentMessage("scheduled work complete"))
        yield Event("3", TaskComplete("scheduled work complete", "completed"))

    async def aclose(self) -> None:
        return None


class _AutomationFactory:
    def create(self, *, workspace, model, approval_callback):
        return _AutomationSession()


def test_json_rpc_automation_lifecycle_uses_a_real_goal_thread(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=_AutomationFactory(),
    )
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    input_read_fd, input_write_fd = os.pipe()
    output_read_fd, output_write_fd = os.pipe()
    source = os.fdopen(input_read_fd, "rb", buffering=0)
    writer = os.fdopen(input_write_fd, "wb", buffering=0)
    reader = os.fdopen(output_read_fd, "rb", buffering=0)
    sink = os.fdopen(output_write_fd, "wb", buffering=0)
    server_thread = threading.Thread(
        target=AppServer(application).serve,
        args=(source, sink),
        daemon=True,
    )
    server_thread.start()
    try:
        writer.write(
            _request(
                1,
                "initialize",
                {
                    "protocolVersion": "1.0",
                    "clientInfo": {
                        "name": "automation-test",
                        "version": "1.0",
                    },
                },
            )
        )
        initialized = _read_until(reader, lambda message: message.get("id") == 1)
        assert "automation/create" in initialized["result"]["capabilities"]["methods"]

        writer.write(
            _request(
                2,
                "automation/create",
                {
                    "projectId": project.id,
                    "name": "Repository caretaker",
                    "prompt": "Review and maintain the repository",
                    "scheduleKind": "manual",
                },
            )
        )
        created = _read_until(reader, lambda message: message.get("id") == 2)["result"]
        automation_id = created["automation"]["id"]
        thread_id = created["thread"]["id"]
        assert created["thread"]["mode"] == "goal"
        assert created["automation"]["threadId"] == thread_id

        writer.write(_request(3, "automation/list", {"projectId": project.id}))
        inventory = _read_until(reader, lambda message: message.get("id") == 3)[
            "result"
        ]
        assert inventory["executionMode"] == "while_app_running"
        assert inventory["schedulerActive"] is True
        assert inventory["automations"][0]["id"] == automation_id

        writer.write(_request(4, "automation/run", {"automationId": automation_id}))
        execution = _read_until(reader, lambda message: message.get("id") == 4)[
            "result"
        ]
        assert execution["run"]["turnId"] == execution["turn"]["id"]
        _read_until(
            reader,
            lambda message: (
                message.get("method") == "turn.completed"
                and message["params"]["threadId"] == thread_id
            ),
        )

        writer.write(
            _request(
                5,
                "automation/runs",
                {"automationId": automation_id, "limit": 10},
            )
        )
        runs = _read_until(reader, lambda message: message.get("id") == 5)["result"][
            "runs"
        ]
        assert runs[0]["status"] == "completed"

        writer.write(_request(6, "automation/remove", {"automationId": automation_id}))
        assert _read_until(reader, lambda message: message.get("id") == 6)[
            "result"
        ] == {"removed": True}
        assert application.session_store.get_session(thread_id) is not None

        writer.write(_request(7, "shutdown", {}))
        assert _read_until(reader, lambda message: message.get("id") == 7)[
            "result"
        ] == {"accepted": True}
        server_thread.join(timeout=2)
        assert not server_thread.is_alive()
    finally:
        writer.close()
        source.close()
        sink.close()
        reader.close()


def test_live_event_is_pushed_while_server_waits_for_input(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(str(workspace))
    input_read_fd, input_write_fd = os.pipe()
    output_read_fd, output_write_fd = os.pipe()
    source = os.fdopen(input_read_fd, "rb", buffering=0)
    input_writer = os.fdopen(input_write_fd, "wb", buffering=0)
    output_reader = os.fdopen(output_read_fd, "rb", buffering=0)
    sink = os.fdopen(output_write_fd, "wb", buffering=0)
    server_thread = threading.Thread(
        target=AppServer(application).serve,
        args=(source, sink),
        daemon=True,
    )
    server_thread.start()
    try:
        input_writer.write(
            _request(
                1,
                "initialize",
                {
                    "protocolVersion": "1.0",
                    "clientInfo": {"name": "pipe-test", "version": "1.0"},
                },
            )
        )
        assert _read_pipe(output_reader)["result"]["protocolVersion"] == "1.0"

        application.threads.start(project.id, title="Background event")
        pushed = _read_pipe(output_reader)
        assert pushed["method"] == "thread.updated"
        assert pushed["params"]["payload"]["thread"]["title"] == "Background event"

        input_writer.write(_request(2, "shutdown", {}))
        assert _read_pipe(output_reader)["result"]["accepted"] is True
        server_thread.join(timeout=2)
        assert not server_thread.is_alive()
    finally:
        input_writer.close()
        source.close()
        sink.close()
        output_reader.close()


def test_stdio_process_reopens_the_same_state(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = tmp_path / "state.sqlite3"

    first = _start_server(database)
    try:
        _send(
            first,
            1,
            "initialize",
            {
                "protocolVersion": "1.0",
                "clientInfo": {"name": "pytest", "version": "1.0"},
            },
        )
        assert _read(first)["result"]["protocolVersion"] == "1.0"
        _send(first, 2, "project/add", {"path": str(workspace)})
        project = _read(first)["result"]["project"]
        _send(
            first,
            3,
            "thread/start",
            {"projectId": project["id"], "title": "Across restart"},
        )
        thread = _read(first)["result"]["thread"]
        assert _read(first)["method"] == "thread.updated"
        _send(first, 4, "shutdown", {})
        assert _read(first)["result"]["accepted"] is True
        assert first.wait(timeout=5) == 0
    finally:
        if first.poll() is None:
            first.kill()

    second = _start_server(database)
    try:
        _send(
            second,
            1,
            "initialize",
            {
                "protocolVersion": "1.0",
                "clientInfo": {"name": "pytest", "version": "1.0"},
            },
        )
        _read(second)
        _send(second, 2, "thread/read", {"threadId": thread["id"]})
        assert _read(second)["result"]["thread"]["title"] == "Across restart"
        _send(second, 3, "event/replay", {"threadId": thread["id"]})
        replay = _read(second)["result"]["events"]
        assert [event["sequence"] for event in replay] == [1]
        _send(second, 4, "shutdown", {})
        _read(second)
        assert second.wait(timeout=5) == 0
    finally:
        if second.poll() is None:
            second.kill()


class _ApprovalSession:
    def __init__(self, workspace: str, approval_callback) -> None:
        self.workspace = Path(workspace)
        self.approval_callback = approval_callback

    def load_history(self, _messages) -> None:
        pass

    async def run_stream(self, _op):
        yield Event("1", TurnStarted())
        yield Event("2", ToolStarted("rpc-call", "write", "write rpc.txt"))
        approved = await self.approval_callback(
            "write", {"file_path": "rpc.txt", "content": "ok"}, "write access"
        )
        if approved:
            (self.workspace / "rpc.txt").write_text("ok")
        yield Event("3", ToolCompleted("rpc-call", "write", not approved, "ok"))
        yield Event("4", AgentMessage("finished"))
        yield Event("5", TaskComplete("finished", "completed"))

    async def aclose(self) -> None:
        await asyncio.sleep(0)


class _ApprovalFactory:
    def create(self, *, workspace, model, approval_callback):
        return _ApprovalSession(workspace, approval_callback)


def test_json_rpc_turn_approval_and_terminal_replay(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3", session_factory=_ApprovalFactory()
    )
    project = application.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
    thread = application.threads.start(project.id, title="RPC execution")
    input_read_fd, input_write_fd = os.pipe()
    output_read_fd, output_write_fd = os.pipe()
    source = os.fdopen(input_read_fd, "rb", buffering=0)
    writer = os.fdopen(input_write_fd, "wb", buffering=0)
    reader = os.fdopen(output_read_fd, "rb", buffering=0)
    sink = os.fdopen(output_write_fd, "wb", buffering=0)
    server_thread = threading.Thread(
        target=AppServer(application).serve,
        args=(source, sink),
        daemon=True,
    )
    server_thread.start()
    try:
        writer.write(
            _request(
                1,
                "initialize",
                {
                    "protocolVersion": "1.0",
                    "clientInfo": {"name": "p2-test", "version": "1.0"},
                },
            )
        )
        assert _read_pipe(reader)["id"] == 1

        writer.write(_request(2, "turn/start", {"threadId": thread.id, "prompt": "go"}))
        started = _read_until(reader, lambda message: message.get("id") == 2)
        turn_id = started["result"]["turn"]["id"]
        requested = _read_until(
            reader, lambda message: message.get("method") == "approval.requested"
        )
        approval_id = requested["params"]["payload"]["approval"]["id"]

        writer.write(
            _request(
                3,
                "approval/respond",
                {"approvalId": approval_id, "decision": "approved_once"},
            )
        )
        approved = _read_until(reader, lambda message: message.get("id") == 3)
        assert approved["result"]["approval"]["status"] == "approved_once"
        _read_until(
            reader,
            lambda message: message.get("method") == "turn.completed",
        )

        writer.write(_request(4, "turn/read", {"turnId": turn_id}))
        completed = _read_until(reader, lambda message: message.get("id") == 4)
        assert completed["result"]["turn"]["status"] == "completed"
        assert completed["result"]["approvals"][0]["status"] == "approved_once"
        assert (workspace / "rpc.txt").read_text() == "ok"

        writer.write(_request(5, "shutdown", {}))
        assert _read_until(reader, lambda message: message.get("id") == 5)["result"][
            "accepted"
        ]
        server_thread.join(timeout=2)
        assert not server_thread.is_alive()
    finally:
        writer.close()
        source.close()
        sink.close()
        reader.close()


def _start_server(database: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-m", "app_server", "--database", str(database)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )


def _send(
    process: subprocess.Popen[str],
    request_id: int,
    method: str,
    params: dict[str, Any],
) -> None:
    assert process.stdin is not None
    process.stdin.write(
        json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        )
        + "\n"
    )
    process.stdin.flush()


def _read(process: subprocess.Popen[str]) -> dict[str, Any]:
    assert process.stdout is not None
    line = process.stdout.readline()
    assert line, _stderr(process)
    return json.loads(line)


def _stderr(process: subprocess.Popen[str]) -> str:
    if process.poll() is None or process.stderr is None:
        return "App Server closed stdout unexpectedly"
    return process.stderr.read()


def _read_pipe(reader) -> dict[str, Any]:
    ready, _, _ = select.select([reader], [], [], 2.0)
    assert ready, "timed out waiting for an App Server protocol message"
    line = reader.readline()
    assert line
    return json.loads(line)


def _read_until(reader, predicate) -> dict[str, Any]:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        message = _read_pipe(reader)
        if predicate(message):
            return message
    raise AssertionError("timed out waiting for matching App Server message")
