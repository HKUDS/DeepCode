from __future__ import annotations

import asyncio

import pytest

from cli.service_thread_client import ServiceThreadClient
from tests.app_server.support import PausedFactory, control_server
from tests.app_server.test_native_client import publish


def test_service_tui_uses_shared_task_and_preserves_catalogs(tmp_path):
    async def scenario():
        factory = PausedFactory()
        async with control_server(tmp_path, session_factory=factory) as (control, _):
            files, lease = publish(control)
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            events = []
            client = None
            try:
                client = await asyncio.to_thread(
                    ServiceThreadClient,
                    workspace=str(workspace),
                    model=None,
                    connection_id=None,
                    reasoning_effort=None,
                    max_iterations=None,
                    streaming=True,
                    trust_workspace=True,
                    database=files.database,
                    event_sink=events.append,
                )
                assert not hasattr(client, "application")
                assert await asyncio.to_thread(client.skills.list, client.project.id)
                assert await asyncio.to_thread(
                    client.mcp.list_presets, client.project.id
                )
                assert await asyncio.to_thread(client.mcp.list, client.project.id)
                assert await asyncio.to_thread(client.plugins.list)
                await asyncio.to_thread(
                    client.switch_execution,
                    connection_id=client.execution_profile.connection_id,
                    model=client.model,
                    reasoning_effort=None,
                    context_window=32000,
                )
                assert client.thread.context_window == 32000
                await client.start_domain_events(lambda _: None)
                delivered = await asyncio.to_thread(
                    client.send, "finish in the shared host"
                )
                assert await asyncio.to_thread(factory.started.wait, 3)
                thread_id = client.thread.id
                assert (
                    control.host.application.turns.read(
                        delivered.turn.id
                    ).turn.thread_id
                    == thread_id
                )
                # Disconnect while admitted work is still executing.
                await client.close()
                factory.release.set()
                assert await asyncio.to_thread(factory.finished.wait, 3)
                assert (workspace / "completed.txt").is_file()
                assert files.running()
            finally:
                factory.release.set()
                if client is not None:
                    await client.close()
                files.clear()
                lease.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["complete", "disconnect", "detach"])
def test_real_service_cli_process(tmp_path, monkeypatch, mode):
    import json
    import os
    import sys

    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path))

    async def scenario():
        factory = PausedFactory()
        async with control_server(
            tmp_path, database_name="state/deepcode.sqlite3", session_factory=factory
        ) as (control, _):
            files, lease = publish(control)
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            process = None
            try:
                if mode == "detach":
                    command = [
                        sys.executable,
                        "-m",
                        "deepcode",
                        "exec",
                        "finish after exec exits",
                        "--detach",
                        "--json",
                        "--trust",
                        "-w",
                        str(workspace),
                    ]
                else:
                    command = [
                        sys.executable,
                        "-m",
                        "cli.tui",
                        "--trust",
                        "-w",
                        str(workspace),
                    ]
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=dict(os.environ),
                )
                if mode != "detach":
                    process.stdin.write(
                        b"finish after reconnect\n/context 32k\n/model\n/skills\n/mcp presets\n/goal show\n/exit\n"
                    )
                    await process.stdin.drain()
                started = await asyncio.to_thread(factory.started.wait, 10)
                if not started:
                    if process.returncode is None:
                        process.terminate()
                    output, error = await process.communicate()
                    raise AssertionError(
                        f"CLI did not submit a Turn: {output.decode()} {error.decode()}"
                    )
                if mode == "disconnect":
                    process.terminate()
                if mode == "complete":
                    factory.release.set()
                output, error = await asyncio.wait_for(process.communicate(), 15)
                if mode != "disconnect":
                    assert process.returncode == 0, error.decode()
                if mode == "complete":
                    assert b"finished" in output
                    assert b"no Goal" in output
                    assert b"Bundled MCP presets" in output
                if mode == "detach":
                    receipt = json.loads(output.splitlines()[-1])
                    assert receipt["detached"] is True
                    assert receipt["turnId"].startswith("turn_")
                    assert not factory.finished.is_set()
                assert files.running()
                factory.release.set()
                assert await asyncio.to_thread(factory.finished.wait, 5)
                assert (workspace / "completed.txt").is_file()
            finally:
                factory.release.set()
                if process is not None and process.returncode is None:
                    process.kill()
                    await process.wait()
                files.clear()
                lease.close()

    asyncio.run(scenario())


def test_service_reconnect_replays_rendered_tools_and_reasoning_once(tmp_path):
    import threading

    from core.events import (
        AgentMessage,
        AgentMessageDelta,
        AgentReasoningCompleted,
        AgentReasoningDelta,
        AgentReasoningStarted,
        Event,
        TaskComplete,
        ToolCompleted,
        ToolStarted,
        TurnStarted,
    )
    from core.reasoning import ReasoningChannel

    release = threading.Event()

    class Factory:
        def create(self, **_kwargs):
            class Session:
                def load_history(self, _history):
                    pass

                async def run_stream(self, _op):
                    yield Event("start", TurnStarted())
                    while not release.is_set():
                        await asyncio.sleep(0.01)
                    yield Event("r0", AgentReasoningStarted("reason", "high"))
                    yield Event(
                        "r1",
                        AgentReasoningDelta(
                            "reason", ReasoningChannel.SUMMARY, "checked"
                        ),
                    )
                    yield Event(
                        "r2", AgentReasoningCompleted("reason", summary_text="checked")
                    )
                    yield Event("t0", ToolStarted("call_write", "write", "result.txt"))
                    yield Event(
                        "t1", ToolCompleted("call_write", "write", False, "saved")
                    )
                    yield Event("m0", AgentMessageDelta("done", "message"))
                    yield Event("m1", AgentMessage("done", message_id="message"))
                    yield Event("done", TaskComplete("done", "completed"))

                async def aclose(self):
                    pass

            return Session()

    async def scenario():
        async with control_server(tmp_path, session_factory=Factory()) as (control, _):
            files, lease = publish(control)
            client = None
            try:
                events = []
                client = await asyncio.to_thread(
                    ServiceThreadClient,
                    workspace=str(tmp_path),
                    model=None,
                    connection_id=None,
                    reasoning_effort=None,
                    max_iterations=None,
                    streaming=True,
                    trust_workspace=True,
                    database=files.database,
                    event_sink=events.append,
                )
                delivery = await asyncio.to_thread(
                    client.send, "test durable rendering"
                )
                await asyncio.to_thread(client.drain_events)
                await asyncio.to_thread(
                    client.rpc.run, client.rpc.client._socket.close()
                )
                release.set()
                async with asyncio.timeout(5):
                    while not control.host.application.turns.read(
                        delivery.turn.id
                    ).turn.status.is_terminal:
                        await asyncio.sleep(0.02)
                await asyncio.to_thread(client.drain_events)
                await asyncio.to_thread(client.drain_events)
                kinds = [event.msg.type for event in events]
                assert kinds.count("tool_started") == 1
                assert kinds.count("tool_completed") == 1
                assert kinds.count("agent_reasoning_completed") == 1
                assert kinds.count("agent_message") == 1
                assert kinds.count("task_complete") == 1
                assert client._sequence == control.host.application.events.head(
                    client.thread.id
                )
                assert client.rpc.generation == 1
                assert files.running()
            finally:
                release.set()
                if client:
                    await client.close()
                files.clear()
                lease.close()

    asyncio.run(scenario())


def test_the_tui_renders_pushed_events_and_stays_quiet_while_idle(tmp_path):
    from core.events import AgentMessage, AgentMessageDelta, TaskComplete, TurnStarted
    from core.events import Event as AgentEvent

    class Factory:
        def create(self, **_kwargs):
            class Session:
                def load_history(self, _history):
                    pass

                async def run_stream(self, _op):
                    yield AgentEvent("start", TurnStarted())
                    yield AgentEvent("d", AgentMessageDelta("pushed ", "message"))
                    yield AgentEvent(
                        "m", AgentMessage("pushed text", message_id="message")
                    )
                    yield AgentEvent("done", TaskComplete("pushed text", "completed"))

                async def aclose(self):
                    pass

            return Session()

    async def scenario():
        async with control_server(tmp_path, session_factory=Factory()) as (control, _):
            files, lease = publish(control)
            client = None
            try:
                events = []
                client = await asyncio.to_thread(
                    ServiceThreadClient,
                    workspace=str(tmp_path),
                    model=None,
                    connection_id=None,
                    reasoning_effort=None,
                    max_iterations=None,
                    streaming=True,
                    trust_workspace=True,
                    database=files.database,
                    event_sink=events.append,
                )
                replays = []
                call = client.rpc.call

                def counting(method, params):
                    if method == "event/replay":
                        replays.append(params)
                    return call(method, params)

                client.rpc.call = counting
                await client.start_domain_events(lambda _: None)

                async def finish_turn(prompt):
                    rendered = len(events)
                    await asyncio.to_thread(client.send, prompt)
                    async with asyncio.timeout(5):
                        while not any(
                            event.msg.type == "task_complete"
                            for event in events[rendered:]
                        ):
                            await asyncio.sleep(0.01)

                # Each poll read the Session's file and took a write lock, 20
                # times a second, idle or not. Only the catch-up replays now.
                await asyncio.sleep(1)
                assert len(replays) == 1
                await finish_turn("first")
                assert len(replays) == 1

                # A dropped connection makes the pump replay, which reconnects;
                # pushes then arrive on the new connection.
                await asyncio.to_thread(
                    client.rpc.run, client.rpc.client._socket.close()
                )
                async with asyncio.timeout(5):
                    while client.rpc.generation == 0:
                        await asyncio.sleep(0.01)
                await finish_turn("second")
                kinds = [event.msg.type for event in events]
                assert kinds.count("agent_message") == 2
                assert kinds.count("task_complete") == 2
                assert client._sequence == control.host.application.events.head(
                    client.thread.id
                )
                assert len(replays) == 2
            finally:
                if client:
                    await client.close()
                files.clear()
                lease.close()

    asyncio.run(scenario())


def test_a_dropped_connection_mid_answer_neither_repeats_nor_loses_text(tmp_path):
    import threading

    from core.events import AgentMessage, AgentMessageDelta, TaskComplete, TurnStarted
    from core.events import Event as AgentEvent

    pieces = [f"part {index}, " for index in range(10)]
    halfway, disconnected, sent_while_down = (threading.Event() for _ in range(3))

    class Factory:
        def create(self, **_kwargs):
            class Session:
                def load_history(self, _history):
                    pass

                async def run_stream(self, _op):
                    yield AgentEvent("start", TurnStarted())
                    for index, piece in enumerate(pieces):
                        if index == 3:
                            halfway.set()
                            while not disconnected.is_set():
                                await asyncio.sleep(0.01)
                        if index == 6:
                            sent_while_down.set()
                        await asyncio.sleep(0.08)
                        yield AgentEvent("d", AgentMessageDelta(piece, "message"))
                    text = "".join(pieces)
                    yield AgentEvent("m", AgentMessage(text, message_id="message"))
                    yield AgentEvent("done", TaskComplete(text, "completed"))

                async def aclose(self):
                    pass

            return Session()

    async def scenario():
        async with control_server(tmp_path, session_factory=Factory()) as (control, _):
            files, lease = publish(control)
            client = None
            try:
                events = []
                client = await asyncio.to_thread(
                    ServiceThreadClient,
                    workspace=str(tmp_path),
                    model=None,
                    connection_id=None,
                    reasoning_effort=None,
                    max_iterations=None,
                    streaming=True,
                    trust_workspace=True,
                    database=files.database,
                    event_sink=events.append,
                )
                reconnect = client.rpc.reconnect

                def reconnect_late(*args, **kwargs):
                    # Parts 3-5 are sent while this client is disconnected.
                    sent_while_down.wait(5)
                    return reconnect(*args, **kwargs)

                client.rpc.reconnect = reconnect_late
                await client.start_domain_events(lambda _: None)
                await asyncio.to_thread(client.send, "stream")
                assert await asyncio.to_thread(halfway.wait, 5)
                await asyncio.to_thread(
                    client.rpc.run, client.rpc.client._socket.close()
                )
                disconnected.set()
                async with asyncio.timeout(10):
                    while not any(e.msg.type == "task_complete" for e in events):
                        await asyncio.sleep(0.01)
                streamed = "".join(
                    e.msg.delta for e in events if e.msg.type == "agent_message_delta"
                )
                assert streamed == "".join(pieces)
                assert client.rpc.generation == 1
            finally:
                for flag in (halfway, disconnected, sent_while_down):
                    flag.set()
                if client:
                    await client.close()
                files.clear()
                lease.close()

    asyncio.run(scenario())


def test_service_tui_and_second_client_share_approval_cas(tmp_path):
    from app_server.errors import RpcError
    from app_server.native_client import NativeRpcClient
    from core.domain.approval import ApprovalStatus
    from tests.app_server.test_native_client import INITIALIZE
    from tests.application.test_cross_process_approval import _ApprovalFactory

    async def scenario():
        factory = _ApprovalFactory()
        async with control_server(tmp_path, session_factory=factory) as (control, _):
            files, lease = publish(control)
            client = None
            observer = NativeRpcClient(files)
            try:
                client = await asyncio.to_thread(
                    ServiceThreadClient,
                    workspace=str(tmp_path),
                    model=None,
                    connection_id=None,
                    reasoning_effort=None,
                    max_iterations=None,
                    streaming=False,
                    trust_workspace=True,
                    database=files.database,
                )
                delivery = await asyncio.to_thread(
                    client.send, "approval shared across clients"
                )
                async with asyncio.timeout(5):
                    approval = None
                    while approval is None:
                        approval = await asyncio.to_thread(client.pending_approval)
                        await asyncio.sleep(0.01)
                await observer.connect(INITIALIZE)
                await asyncio.to_thread(
                    client.respond_to_approval,
                    approval.id,
                    ApprovalStatus.APPROVED_ONCE,
                )
                with pytest.raises(RpcError):
                    await observer.request(
                        "approval/respond",
                        {"approvalId": approval.id, "decision": "denied"},
                    )
                await client.wait_until_idle()
                assert factory.decisions == [True]
                assert (
                    control.host.application.turns.read(
                        delivery.turn.id
                    ).turn.status.value
                    == "completed"
                )
            finally:
                if client:
                    await client.close()
                await observer.close()
                files.clear()
                lease.close()

    asyncio.run(scenario())


def test_exec_interrupt_keeps_keyboard_exit_when_service_is_lost(monkeypatch, capsys):
    from types import SimpleNamespace
    from cli.thread_client import HeadlessTurnOptions
    from cli.service_turn import run_service_turn

    class LostClient:
        thread = SimpleNamespace(id="thread")
        turns = SimpleNamespace(
            start=lambda *_args, **_kwargs: SimpleNamespace(
                turn=SimpleNamespace(status=SimpleNamespace(is_terminal=False))
            )
        )

        def __init__(self, **_kwargs):
            pass

        def drain_events(self):
            raise KeyboardInterrupt()

        def interrupt(self):
            raise RuntimeError("connection lost")

        async def close(self):
            raise RuntimeError("connection lost during close")

    monkeypatch.setattr("cli.service_turn.ServiceThreadClient", LostClient)
    with pytest.raises(KeyboardInterrupt):
        run_service_turn(HeadlessTurnOptions("already admitted task"))
    assert "interruption could not be confirmed" in capsys.readouterr().err
