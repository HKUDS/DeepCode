import asyncio
import io
import json
import os
import select
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app_server.connection import ConnectionState
from app_server.dispatcher import Dispatcher, Params
from app_server.server import AppServer
from core.application import DeepCodeApplication
from core.application.errors import NoActiveTurnError, TurnNotSteerableError
from core.application.event_service import EventBroker
from core.application.goal_extension import (
    GoalContinueDisposition,
    GoalContinueResult,
)
from core.application.views import thread_goal_view
from core.domain import (
    ClientSurface,
    ThreadGoalStatus,
    TrustState,
    Turn,
)
from core.domain.thread_goal import ThreadGoal
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


def test_app_server_uses_explicit_client_surface_without_name_inference() -> None:
    class Turns:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        def start(self, thread_id: str, **kwargs):
            self.kwargs = {"thread_id": thread_id, **kwargs}
            return SimpleNamespace(
                turn=Turn(
                    id="turn_000000000000000000000001",
                    thread_id=thread_id,
                    ordinal=1,
                    prompt=str(kwargs["prompt"]),
                ),
                items=(),
                approvals=(),
            )

    turns = Turns()
    connection = ConnectionState(EventBroker())
    dispatcher = Dispatcher(
        SimpleNamespace(turns=turns),
        connection,
    )

    initialized = dispatcher._initialize(
        Params(
            {
                "protocolVersion": "1.0",
                "clientInfo": {
                    "name": "arbitrary-host-name",
                    "version": "1.0",
                    "surface": "desktop",
                },
            }
        )
    )
    dispatcher._turn_start(
        Params(
            {
                "threadId": "thread-1",
                "prompt": "Run it",
                "messageId": "message-1",
            }
        )
    )

    assert initialized["clientInfo"]["surface"] == "desktop"
    assert connection.client_surface is ClientSurface.DESKTOP
    assert turns.kwargs["client_surface"] is ClientSurface.DESKTOP


def test_turn_steer_reports_typed_no_active_error_without_implicit_queue(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(str(workspace))
    thread = application.threads.start(project.id, title="Strict steer")
    source = io.BytesIO(
        _request(
            1,
            "initialize",
            {
                "protocolVersion": "1.0",
                "clientInfo": {"name": "strict-turn-test", "version": "1.0"},
            },
        )
        + _request(
            2,
            "turn/steer",
            {
                "threadId": thread.id,
                "expectedTurnId": "turn_missing",
                "prompt": "Continue with this correction.",
                "messageId": "strict-steer-1",
            },
        )
        + _request(3, "shutdown", {})
    )
    sink = io.BytesIO()

    assert AppServer(application).serve(source, sink) == 0
    responses = {
        message["id"]: message for message in _messages(sink) if "id" in message
    }
    assert responses[2]["error"]["data"]["code"] == "NO_ACTIVE_TURN"
    assert responses[2]["error"]["data"]["retryable"] is True
    assert responses[2]["error"]["data"]["details"]["actualTurnId"] is None
    assert application.turns.active_for_thread(thread.id) is None
    assert application.turns.conversation_count(thread.id) == 0


@pytest.mark.parametrize("boundary_state", ["closing", "closed"])
def test_turn_steer_waits_for_durable_terminal_before_reporting_no_active(
    boundary_state: str,
) -> None:
    class Turns:
        def __init__(self) -> None:
            self.waited_for: str | None = None

        def steer(self, thread_id: str, **kwargs):
            del thread_id, kwargs
            raise TurnNotSteerableError(
                "active Turn input is closed",
                details={"state": boundary_state},
            )

        def wait_until_terminal(self, turn_id: str):
            self.waited_for = turn_id
            return object()

    turns = Turns()
    dispatcher = Dispatcher(
        SimpleNamespace(turns=turns),
        SimpleNamespace(),
    )

    with pytest.raises(NoActiveTurnError) as raised:
        dispatcher._turn_steer(
            Params(
                {
                    "threadId": "thread-1",
                    "expectedTurnId": "turn-1",
                    "prompt": "Continue after finalization.",
                    "messageId": "message-1",
                }
            )
        )

    assert turns.waited_for == "turn-1"
    assert raised.value.details["actualTurnId"] is None


def test_thread_goal_continue_has_one_explicit_typed_rpc() -> None:
    goal = ThreadGoal(
        id="goal_000000000000000000000001",
        thread_id="thread-1",
        objective="Ship",
    )

    class Goals:
        def read_outcome(self, thread_id: str):
            assert thread_id == goal.thread_id

        def continue_goal(
            self,
            thread_id: str,
            *,
            expected_goal_id: str,
            **_kwargs,
        ):
            assert thread_id == goal.thread_id
            assert expected_goal_id == goal.id
            return GoalContinueResult(
                goal=goal,
                disposition=GoalContinueDisposition.STARTED,
                turn_id="turn_000000000000000000000001",
            )

    dispatcher = Dispatcher(
        SimpleNamespace(goals=Goals()),
        SimpleNamespace(),
    )

    result = dispatcher._thread_goal_continue(
        Params(
            {
                "threadId": goal.thread_id,
                "expectedGoalId": goal.id,
            }
        )
    )

    assert result == {
        "goal": thread_goal_view(goal),
        "outcome": None,
        "disposition": "started",
        "turnId": "turn_000000000000000000000001",
    }


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


def test_thread_delete_removes_the_shared_session_through_json_rpc(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sessions = SessionStore(tmp_path / "sessions")
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_store=sessions,
    )
    project = application.projects.add(str(workspace))
    thread = application.threads.start(project.id, title="Delete through Desktop")
    sessions.append_message(thread.id, "user", "temporary history")
    source = io.BytesIO(
        _request(
            1,
            "initialize",
            {
                "protocolVersion": "1.0",
                "clientInfo": {"name": "deletion-test", "version": "1.0"},
            },
        )
        + _request(2, "thread/delete", {"threadId": thread.id})
        + _request(3, "thread/read", {"threadId": thread.id})
        + _request(4, "shutdown", {})
    )
    sink = io.BytesIO()

    try:
        assert AppServer(application).serve(source, sink) == 0
        responses = {
            message["id"]: message for message in _messages(sink) if "id" in message
        }
        assert "thread/delete" in responses[1]["result"]["capabilities"]["methods"]
        assert responses[2]["result"] == {
            "threadId": thread.id,
            "cleanupPending": False,
        }
        assert responses[3]["error"]["data"]["code"] == "THREAD_NOT_FOUND"
        assert sessions.get_session(thread.id) is None
    finally:
        application.close()


def test_thread_goal_protocol_uses_the_canonical_session_ledger(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sessions = SessionStore(tmp_path / "sessions")
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_store=sessions,
    )
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    thread = application.threads.start(project.id, title="Goal protocol")
    source = io.BytesIO(
        _request(
            1,
            "initialize",
            {
                "protocolVersion": "1.0",
                "clientInfo": {"name": "goal-test", "version": "1.0"},
            },
        )
        + _request(
            2,
            "thread/goal/set",
            {
                "threadId": thread.id,
                "objective": "Implement the feature",
                "tokenBudget": 12_000,
                "start": False,
            },
        )
        + _request(3, "thread/goal/get", {"threadId": thread.id})
        + _request(4, "shutdown", {})
    )
    sink = io.BytesIO()

    assert AppServer(application).serve(source, sink) == 0
    responses = {
        message["id"]: message["result"]
        for message in _messages(sink)
        if "id" in message and "result" in message
    }
    assert responses[2]["goal"]["objective"] == "Implement the feature"
    assert responses[2]["goal"]["tokenBudget"] == 12_000
    assert responses[3]["goal"] == responses[2]["goal"]
    goal_id = responses[2]["goal"]["id"]

    follow_up = io.BytesIO(
        _request(
            5,
            "initialize",
            {
                "protocolVersion": "1.0",
                "clientInfo": {"name": "goal-test", "version": "1.0"},
            },
        )
        + _request(
            6,
            "thread/goal/pause",
            {"threadId": thread.id, "expectedGoalId": goal_id},
        )
        + _request(
            7,
            "thread/goal/clear",
            {"threadId": thread.id, "expectedGoalId": goal_id},
        )
        + _request(8, "shutdown", {})
    )
    follow_up_sink = io.BytesIO()
    assert AppServer(application).serve(follow_up, follow_up_sink) == 0
    follow_up_responses = {
        message["id"]: message["result"]
        for message in _messages(follow_up_sink)
        if "id" in message and "result" in message
    }
    assert follow_up_responses[6]["goal"]["status"] == "paused"
    assert follow_up_responses[7] == {"goal": None, "outcome": None}
    assert (sessions.root / thread.id / "goal.jsonl").exists()


def test_thread_goal_protocol_edits_and_resumes_the_same_identity(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    try:
        project = application.projects.add(
            str(workspace),
            trust_state=TrustState.TRUSTED,
        )
        thread = application.threads.start(project.id, title="Reopen Goal")
        created = application.goals.create(
            thread.id,
            objective="Ship version one",
            start=False,
        )
        completed = application.thread_goal_store.update(
            thread.id,
            expected_goal_id=created.id,
            transform=lambda current: current.agent_transition(
                ThreadGoalStatus.COMPLETE
            ),
            reason="Version one is complete.",
            source="agent",
        )
        source = io.BytesIO(
            _request(
                1,
                "initialize",
                {
                    "protocolVersion": "1.0",
                    "clientInfo": {"name": "reopen-test", "version": "1.0"},
                },
            )
            + _request(
                2,
                "thread/goal/set",
                {
                    "threadId": thread.id,
                    "objective": "Ship version two",
                    "expectedGoalId": completed.id,
                    "start": False,
                },
            )
            + _request(
                3,
                "thread/goal/resume",
                {"threadId": thread.id, "expectedGoalId": completed.id},
            )
            + _request(4, "thread/goal/get", {"threadId": thread.id})
            + _request(5, "shutdown", {})
        )
        sink = io.BytesIO()

        assert AppServer(application).serve(source, sink) == 0
        responses = {
            message["id"]: message["result"]
            for message in _messages(sink)
            if "id" in message and "result" in message
        }
        edited = responses[2]["goal"]
        assert edited["id"] == created.id
        assert edited["status"] == ThreadGoalStatus.COMPLETE.value
        assert edited["objective"] == "Ship version two"
        assert responses[2]["outcome"]["status"] == "complete"
        assert responses[2]["outcome"]["reason"] == "Version one is complete."
        assert responses[3]["goal"]["id"] == created.id
        assert responses[3]["goal"]["status"] == ThreadGoalStatus.ACTIVE.value
        assert responses[3]["outcome"] is None
        assert responses[4]["goal"] == responses[3]["goal"]
        assert responses[4]["outcome"] is None
    finally:
        application.close()


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


def test_connection_and_model_protocol_is_shared_secret_safe_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "deepcode-home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(str(workspace))
    thread = application.threads.start(project.id, title="Connection selection")
    secret = "rpc-secret-that-must-not-be-returned"
    source = io.BytesIO(
        _request(
            1,
            "initialize",
            {
                "protocolVersion": "1.0",
                "clientInfo": {"name": "connection-test", "version": "1.0"},
            },
        )
        + _request(
            2,
            "provider/upsert",
            {
                "connection": {
                    "id": "router-rpc",
                    "label": "Router RPC",
                    "template": "openrouter",
                    "apiKey": secret,
                    "modelCatalog": "manual",
                    "manualModels": ["moonshotai/kimi-k3"],
                }
            },
        )
        + _request(3, "provider/list", {})
        + _request(
            4,
            "model/list",
            {"connectionId": "router-rpc", "refresh": True},
        )
        + _request(
            5,
            "thread/execution/update",
            {
                "threadId": thread.id,
                "connectionId": "router-rpc",
                "model": "moonshotai/kimi-k3",
                "reasoningEffort": "high",
            },
        )
        + _request(6, "provider/remove", {"connectionId": "router-rpc"})
        + _request(7, "shutdown", {})
    )
    sink = io.BytesIO()

    assert AppServer(application).serve(source, sink) == 0
    assert secret.encode() not in sink.getvalue()
    responses = {
        message["id"]: message["result"]
        for message in _messages(sink)
        if "id" in message and "result" in message
    }
    upserted = next(
        connection
        for connection in responses[2]["connections"]
        if connection["id"] == "router-rpc"
    )
    assert upserted["configured"] is True
    assert upserted["credentialSource"] == "credential_store"
    assert (
        next(
            connection
            for connection in responses[3]["connections"]
            if connection["id"] == "router-rpc"
        )
        == upserted
    )
    assert [model["id"] for model in responses[4]["models"]] == ["moonshotai/kimi-k3"]
    assert responses[4]["models"][0]["reasoning"]["supportedEfforts"] == [
        "low",
        "high",
        "max",
    ]
    assert responses[4]["source"] == "manual"
    assert responses[5]["thread"]["connectionId"] == "router-rpc"
    assert responses[5]["thread"]["model"] == "moonshotai/kimi-k3"
    assert responses[5]["thread"]["reasoningEffort"] == "high"
    assert responses[6]["removed"] is True

    persisted = json.loads((home / "deepcode_config.json").read_text())
    assert secret not in json.dumps(persisted)
    assert "router-rpc" not in persisted.get("providers", {}).get("profiles", {})
    canonical = application.session_store.get_session(thread.id)
    assert canonical is not None
    assert canonical.metadata["connection_id"] == "router-rpc"
    assert canonical.metadata["model"] == "moonshotai/kimi-k3"
    assert canonical.metadata["reasoning_effort"] == "high"


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
    review_id = next(
        skill.id
        for skill in application.skills.list(project.id).skills
        if skill.name == "review"
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
        + _request(
            10,
            "skills/set-enabled",
            {
                "projectId": project.id,
                "skillId": review_id,
                "scope": "project",
            },
        )
        + _request(
            11,
            "skills/set-enabled",
            {
                "projectId": project.id,
                "skillId": review_id,
                "enabled": False,
                "scope": "project",
            },
        )
        + _request(
            12,
            "skills/set-enabled",
            {
                "projectId": project.id,
                "skillId": review_id,
                "enabled": True,
                "scope": "project",
            },
        )
        + _request(13, "skills/reload", {"projectId": project.id})
        + _request(14, "shutdown", {})
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
        messages = {
            message["id"]: message for message in _messages(sink) if "id" in message
        }
        assert messages[10]["error"]["data"]["code"] == "INVALID_REQUEST"
        disabled = next(
            skill for skill in responses[11]["skills"] if skill["id"] == review_id
        )
        assert disabled["status"] == "disabled"
        enabled = next(
            skill for skill in responses[12]["skills"] if skill["id"] == review_id
        )
        assert enabled["status"] == "active"
        assert responses[13]["catalogRevision"].startswith("sha256:")
        serialized = sink.getvalue().decode()
        assert "never-return-this" not in serialized
        assert "mcp-secret" not in serialized
    finally:
        application.close()


class _AutomationSession:
    def __init__(self, goal_runtime) -> None:
        self.goal_runtime = goal_runtime

    def load_history(self, _messages) -> None:
        return None

    async def run_stream(self, _op):
        yield Event("1", TurnStarted())
        self.goal_runtime.request(
            status="complete",
            reason="Scheduled repository work and verification completed.",
        )
        yield Event("2", AgentMessage("scheduled work complete"))
        yield Event("3", TaskComplete("scheduled work complete", "completed"))

    async def aclose(self) -> None:
        return None


class _AutomationFactory:
    def create(self, *, workspace, model, approval_callback, goal_runtime):
        return _AutomationSession(goal_runtime)


def test_json_rpc_automation_lifecycle_uses_a_real_goal_thread(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=_AutomationFactory(),
        run_automation_scheduler=True,
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
                19,
                "automation/create",
                {
                    "projectId": project.id,
                    "name": "Invalid paused manual",
                    "prompt": "This must be rejected",
                    "scheduleKind": "manual",
                    "enabled": False,
                },
            )
        )
        rejected_create = _read_until(
            reader,
            lambda message: message.get("id") == 19,
        )
        assert rejected_create["error"]["data"]["code"] == "INVALID_REQUEST"
        assert (
            "manual automations are always enabled"
            in rejected_create["error"]["message"]
        )

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

        writer.write(
            _request(
                20,
                "automation/update",
                {
                    "automationId": automation_id,
                    "status": "paused",
                },
            )
        )
        rejected_update = _read_until(
            reader,
            lambda message: message.get("id") == 20,
        )
        assert rejected_update["error"]["data"]["code"] == "INVALID_REQUEST"
        assert (
            "manual automations are always enabled"
            in rejected_update["error"]["message"]
        )

        writer.write(_request(3, "automation/list", {"projectId": project.id}))
        inventory = _read_until(reader, lambda message: message.get("id") == 3)[
            "result"
        ]
        assert inventory["executionMode"] == "requires_live_runtime"
        assert inventory["schedulerActive"] is True
        assert inventory["hasMore"] is False
        assert inventory["nextOffset"] is None
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
        run_page = _read_until(reader, lambda message: message.get("id") == 5)["result"]
        runs = run_page["runs"]
        assert runs[0]["status"] == "completed"
        assert run_page["hasMore"] is False
        assert run_page["nextOffset"] is None

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

        writer.write(
            _request(
                2,
                "turn/start",
                {
                    "threadId": thread.id,
                    "prompt": "go",
                    "messageId": "server-start-1",
                },
            )
        )
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
