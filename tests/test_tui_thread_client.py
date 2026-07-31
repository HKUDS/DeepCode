from __future__ import annotations

import asyncio
import io
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from cli.tui.app import TuiApp
from cli.tui.thread_client import TuiThreadClient
from core import agent_setup
from core.application.application import DeepCodeApplication
from core.domain.turn import TurnStatus
from core.events import Event
from core.providers.base import LLMResponse, ToolCallRequest
from core.sessions import SessionStore


class _Profile:
    model = "fake-model"


class _BlockingProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.first_call_started = threading.Event()
        self.release_first_call = threading.Event()
        self.requests: list[dict[str, Any]] = []

    def get_default_model(self) -> str:
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        self.requests.append(kwargs)
        if self.calls == 1:
            self.first_call_started.set()
            await asyncio.to_thread(self.release_first_call.wait)
            return LLMResponse(content="first pass", finish_reason="stop")
        return LLMResponse(
            content=f"follow-up pass {self.calls}",
            finish_reason="stop",
        )


class _ToolProvider:
    def __init__(self) -> None:
        self.calls = 0

    def get_default_model(self) -> str:
        return "fake-model"

    async def chat_with_retry(self, **_kwargs: Any) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="slow-tool",
                        name="bash",
                        arguments={
                            "command": (
                                'python3 -c "from pathlib import Path; '
                                "import time; Path('tool-running').write_text('yes'); "
                                'time.sleep(0.5)"'
                            )
                        },
                    )
                ],
                finish_reason="tool_calls",
            )
        return LLMResponse(content="finished after steering", finish_reason="stop")


def _patch_provider(monkeypatch: pytest.MonkeyPatch, provider: Any) -> None:
    monkeypatch.setattr(
        agent_setup,
        "get_workflow_provider",
        lambda **_kwargs: (provider, _Profile()),
    )
    monkeypatch.setattr(
        agent_setup,
        "get_runtime",
        lambda: type("R", (), {"config": type("C", (), {"security": None})()})(),
    )


def _make_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    provider: Any,
    *,
    event_sink: Callable[[Event], None] | None = None,
) -> TuiThreadClient:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DEEPCODE_SESSIONS_DIR", str(tmp_path / "sessions"))
    _patch_provider(monkeypatch, provider)
    client = TuiThreadClient(
        workspace=str(workspace),
        model=None,
        connection_id=None,
        reasoning_effort=None,
        max_iterations=20,
        streaming=False,
        store=SessionStore(tmp_path / "sessions"),
        event_sink=event_sink,
    )
    client.set_event_loop(asyncio.get_running_loop())
    return client


@pytest.mark.asyncio
async def test_tui_client_does_not_start_resident_automation_scheduler(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = _BlockingProvider()
    client = _make_client(monkeypatch, tmp_path, provider)
    try:
        assert client.application.run_automation_scheduler is False
        assert client.application.automation_scheduler.active is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cli_event_subscription_follows_session_switches_without_duplicates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = _BlockingProvider()
    provider.release_first_call.set()
    events: list[Event] = []
    client = _make_client(
        monkeypatch,
        tmp_path,
        provider,
        event_sink=events.append,
    )
    original_session_id = client.session_id
    try:
        first = client.send("first")
        await asyncio.wait_for(client.wait_until_idle(), timeout=5)
        await asyncio.sleep(0)

        client.new_thread()
        second = client.send("second")
        await asyncio.wait_for(client.wait_until_idle(), timeout=5)
        await asyncio.sleep(0)

        client.resume(original_session_id)
        third = client.send("third")
        await asyncio.wait_for(client.wait_until_idle(), timeout=5)
        await asyncio.sleep(0)

        assert len({first.turn.id, second.turn.id, third.turn.id}) == 3
        assert [event.msg.type for event in events].count("turn_started") == 3
        assert [event.msg.type for event in events].count("task_complete") == 3
    finally:
        await client.close()

    assert client.application.turns._thread_event_observers == {}


async def _wait_for_first_call(provider: _BlockingProvider) -> None:
    accepted = await asyncio.to_thread(provider.first_call_started.wait, 2)
    assert accepted, "the first model call did not begin"


async def _wait_for_path(path: Path) -> None:
    for _ in range(200):
        if path.exists():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"path did not appear before timeout: {path}")


@pytest.mark.asyncio
async def test_cli_steers_an_ordinary_running_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = _BlockingProvider()
    client = _make_client(monkeypatch, tmp_path, provider)
    try:
        started = client.send("inspect the implementation")
        await _wait_for_first_call(provider)

        steered = client.send("preserve the public API")
        assert started.kind == "started"
        assert steered.kind == "steered"
        assert steered.turn.id == started.turn.id

        provider.release_first_call.set()
        await asyncio.wait_for(client.wait_until_idle(), timeout=5)
        stored = client.store.get_session(client.session_id)
        assert stored is not None
        assert [
            message.content for message in stored.messages if message.role == "user"
        ] == [
            "inspect the implementation",
            "preserve the public API",
        ]
        user_messages = [
            message for message in stored.messages if message.role == "user"
        ]
        assert [message.metadata["client"] for message in user_messages] == [
            "cli",
            "cli",
        ]
        assert [message.metadata["source"] for message in user_messages] == [
            "start",
            "steer",
        ]
        assert stored.messages[-1].metadata["client"] == "cli"
        assert provider.calls == 2
    finally:
        provider.release_first_call.set()
        await client.close()


@pytest.mark.asyncio
async def test_cli_steers_while_a_tool_is_running(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = _ToolProvider()
    client = _make_client(monkeypatch, tmp_path, provider)
    try:
        current = client.send("run the slow verification")
        await _wait_for_path(Path(client.workspace) / "tool-running")

        steered = client.send("after it finishes, preserve compatibility")
        assert steered.kind == "steered"
        assert steered.turn.id == current.turn.id

        await asyncio.wait_for(client.wait_until_idle(), timeout=5)
        stored = client.store.get_session(client.session_id)
        assert stored is not None
        assert [
            message.content for message in stored.messages if message.role == "user"
        ] == [
            "run the slow verification",
            "after it finishes, preserve compatibility",
        ]
        assert provider.calls == 2
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cli_stop_then_continues_in_the_same_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = _BlockingProvider()
    client = _make_client(monkeypatch, tmp_path, provider)
    session_id = client.session_id
    try:
        first = client.send("begin the long task")
        await _wait_for_first_call(provider)
        interrupted = client.interrupt()
        assert interrupted is not None
        assert interrupted[0] is True
        assert interrupted[1].id == first.turn.id
        assert interrupted[1].status is TurnStatus.INTERRUPTED

        provider.release_first_call.set()
        second = client.send("continue with the corrected direction")
        assert client.session_id == session_id
        assert second.kind == "started"
        assert second.turn.id != first.turn.id
        await asyncio.wait_for(client.wait_until_idle(), timeout=5)

        stored = client.store.get_session(session_id)
        assert stored is not None
        user_messages = [
            message.content for message in stored.messages if message.role == "user"
        ]
        assert user_messages[0] == "begin the long task"
        assert user_messages[-1] == "continue with the corrected direction"
    finally:
        provider.release_first_call.set()
        await client.close()


@pytest.mark.asyncio
async def test_cli_explicit_queue_runs_after_the_active_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = _BlockingProvider()
    client = _make_client(monkeypatch, tmp_path, provider)
    try:
        current = client.send("run the current task")
        await _wait_for_first_call(provider)
        queued = client.queue("run this next")

        assert current.kind == "started"
        assert queued.kind == "queued"
        assert queued.turn.id != current.turn.id
        assert queued.turn.status is TurnStatus.QUEUED

        provider.release_first_call.set()
        await asyncio.wait_for(client.wait_until_idle(), timeout=5)
        assert client.application.turns.read(queued.turn.id).turn.status is (
            TurnStatus.COMPLETED
        )
        assert provider.calls == 2
    finally:
        provider.release_first_call.set()
        await client.close()


@pytest.mark.asyncio
async def test_desktop_application_resumes_the_exact_cli_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = _BlockingProvider()
    provider.release_first_call.set()
    client = _make_client(monkeypatch, tmp_path, provider)
    session_id = client.session_id
    workspace = client.workspace
    store = client.store
    try:
        client.send("persist this shared history")
        await asyncio.wait_for(client.wait_until_idle(), timeout=5)
    finally:
        await client.close()

    desktop_application = DeepCodeApplication.open(session_store=store)
    try:
        resumed = desktop_application.threads.resume(
            session_id,
            workspace_path=workspace,
        )
        assert resumed.id == session_id
        canonical = store.get_session(session_id)
        assert canonical is not None
        assert canonical.metadata["kind"] == "tui"
        assert [message.content for message in canonical.messages] == [
            "persist this shared history",
            "first pass",
        ]
        assert desktop_application.turns.conversation_count(session_id) == 2
    finally:
        desktop_application.close()


@pytest.mark.asyncio
async def test_cli_and_desktop_share_one_goal_in_both_directions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = _BlockingProvider()
    provider.release_first_call.set()
    cli = _make_client(monkeypatch, tmp_path, provider)
    session_id = cli.session_id
    workspace = cli.workspace
    session_root = tmp_path / "sessions"
    created = cli.application.goals.create(
        session_id,
        objective="Created from CLI",
        token_budget=2_000,
        start=False,
    )
    await cli.close()

    desktop = DeepCodeApplication.open(
        session_store=SessionStore(session_root),
    )
    try:
        resumed = desktop.threads.resume(
            session_id,
            workspace_path=workspace,
        )
        assert resumed.id == session_id
        observed = desktop.goals.read(session_id)
        assert observed is not None
        assert observed.id == created.id
        edited_from_desktop = desktop.goals.edit(
            session_id,
            expected_goal_id=created.id,
            objective="Edited from Desktop",
            token_budget=2_500,
            skill_ids=(),
            continue_work=False,
        )
        assert edited_from_desktop.id == created.id
    finally:
        desktop.close()

    resumed_cli = TuiThreadClient(
        workspace=workspace,
        model=None,
        connection_id=None,
        reasoning_effort=None,
        max_iterations=20,
        streaming=False,
        resume_id=session_id,
        store=SessionStore(session_root),
    )
    try:
        observed = resumed_cli.application.goals.read(session_id)
        assert observed is not None
        assert observed.id == created.id
        assert observed.objective == "Edited from Desktop"
        edited_from_cli = resumed_cli.application.goals.edit(
            session_id,
            expected_goal_id=created.id,
            objective="Edited again from CLI",
            token_budget=observed.token_budget,
            skill_ids=observed.skill_ids,
            continue_work=False,
        )
        assert edited_from_cli.id == created.id
    finally:
        await resumed_cli.close()

    desktop_again = DeepCodeApplication.open(
        session_store=SessionStore(session_root),
    )
    try:
        final = desktop_again.goals.read(session_id)
        assert final is not None
        assert final.id == created.id
        assert final.objective == "Edited again from CLI"
    finally:
        desktop_again.close()


@pytest.mark.asyncio
async def test_goal_commands_reuse_the_cli_application(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = _BlockingProvider()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DEEPCODE_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    _patch_provider(monkeypatch, provider)

    original_open = DeepCodeApplication.open.__func__
    opened = 0

    def counted_open(cls, *args: Any, **kwargs: Any):
        nonlocal opened
        opened += 1
        return original_open(cls, *args, **kwargs)

    monkeypatch.setattr(DeepCodeApplication, "open", classmethod(counted_open))
    app = TuiApp(
        workspace=str(workspace),
        model=None,
        max_iterations=20,
    )
    try:
        assert opened == 1
        result = await app.run_goal_command("show")
        assert "no Goal" in result
        assert opened == 1
        assert (
            app.goal_controller.owner.thread_client.application
            is app.thread_client.application
        )
    finally:
        app.goal_controller.close()
        await app.thread_client.close()
        if app._session_activity is not None:
            app._session_activity.close()
