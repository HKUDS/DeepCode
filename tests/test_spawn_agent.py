"""Tests for C2 model-driven delegation — AgentControl + spawn/wait tools.

The LLM sub-agent (``_run_subagent``) is stubbed so we exercise the real
orchestration: non-blocking concurrent spawn, the concurrency limit, the result
mailbox, and isolated-worktree merge-back.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_runtime.injections import SubagentMessage
from core.harness.agents.control import (
    AgentControl,
    AgentLimitError,
    DuplicateAgentError,
    SubAgent,
)
from core.harness.tools.spawn_agent import (
    InterruptAgentTool,
    ListAgentsTool,
    SendMessageTool,
    SpawnAgentTool,
    WaitAgentTool,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git required")


class _HeldControl(AgentControl):
    """Sub-agents block on ``release`` so we can inspect them mid-flight."""

    def __init__(self, workspace, **kw):
        super().__init__(workspace, **kw)
        self.release = asyncio.Event()
        self.last_seed = None

    async def _run_subagent(self, sub, workspace):
        self.last_seed = sub.seed_history or None
        await self.release.wait()
        return f"did: {sub.task}"


class _WritingControl(AgentControl):
    """Sub-agent writes a file (to exercise isolate + merge)."""

    async def _run_subagent(self, sub, workspace):
        (Path(workspace) / "feature.py").write_text("VALUE = 1\n")
        return "wrote feature.py"


def test_spawn_is_non_blocking_and_concurrent(tmp_path):
    async def scenario():
        ctrl = _HeldControl(str(tmp_path), max_threads=2)
        a = ctrl.spawn("t1", isolate=False)
        b = ctrl.spawn("t2", isolate=False)
        assert a == "agent-1" and b == "agent-2"
        await asyncio.sleep(0)  # let both tasks reach `release.wait()`
        assert ctrl.active_count() == 2
        # over the limit → refused, not blocked
        with pytest.raises(AgentLimitError):
            ctrl.spawn("t3", isolate=False)
        ctrl.release.set()
        outcome = await ctrl.wait_for_activity(5.0)
        assert "result" in outcome.lower() or "finished" in outcome.lower()
        injections = await ctrl.drain_injections()
        assert len(injections) == 2
        assert all(isinstance(item, SubagentMessage) for item in injections)
        assert all(
            item.target_turn_id.startswith("local-agent-runtime-")
            for item in injections
        )
        assert all("Message Type: RESULT" in item.payload for item in injections)

    asyncio.run(scenario())


def test_application_backed_result_uses_the_active_turn_sink(tmp_path):
    async def scenario():
        active_turn_id: str | None = "turn-parent"
        delivered: list[SubagentMessage] = []

        def target() -> str | None:
            return active_turn_id

        def sink(message: SubagentMessage) -> bool:
            delivered.append(message)
            return True

        ctrl = _HeldControl(
            str(tmp_path),
            active_turn_id_provider=target,
            runtime_input_sink=sink,
        )
        ctrl.spawn("t1", isolate=False)
        await asyncio.sleep(0)
        ctrl.release.set()
        await ctrl.wait_for_activity(5.0)

        assert len(delivered) == 1
        assert delivered[0].target_turn_id == "turn-parent"
        assert await ctrl.drain_injections() == []

        active_turn_id = None
        second_id = ctrl.spawn("t2", isolate=False)
        await ctrl.get(second_id).handle
        assert len(delivered) == 1
        assert await ctrl.drain_injections() == []

    asyncio.run(scenario())


def test_duplicate_named_subtask_running_or_done_is_refused(tmp_path):
    async def scenario():
        ctrl = _HeldControl(str(tmp_path))
        ctrl.spawn("Create parser.py that splits lines", name="parser", isolate=False)
        await asyncio.sleep(0)  # running
        # RUNNING + reworded task → refused (name is the dedup key)
        with pytest.raises(DuplicateAgentError):
            ctrl.spawn("build the parsing logic module", name="parser", isolate=False)
        assert ctrl.active_count() == 1
        # let it finish → done
        ctrl.release.set()
        await ctrl.get("parser").handle
        assert ctrl.get("parser").status == "done"
        # DONE → still refused (re-spawning completed work is the real over-spawn)
        with pytest.raises(DuplicateAgentError):
            ctrl.spawn("redo the parser", name="parser", isolate=False)
        # a different name is fine
        ctrl.spawn("Create lexer.py", name="lexer", isolate=False)
        assert ctrl.get("lexer") is not None

    asyncio.run(scenario())


def test_failed_subtask_can_be_retried(tmp_path):
    class _FailControl(AgentControl):
        async def _run_subagent(self, sub, workspace):
            raise RuntimeError("boom")

    async def scenario():
        ctrl = _FailControl(str(tmp_path))
        aid = ctrl.spawn("do X", name="x", isolate=False)
        await ctrl.get(aid).handle
        assert ctrl.get(aid).status == "failed"
        # a failed subtask may be retried (not refused)
        ctrl.spawn("do X again", name="x", isolate=False)
        assert sum(1 for a in ctrl.all() if a.dedup_key == "x") == 2

    asyncio.run(scenario())


def test_duplicate_task_text_refused_without_name(tmp_path):
    async def scenario():
        ctrl = _HeldControl(str(tmp_path))
        ctrl.spawn("build the parser", isolate=False)  # no name → text key
        await asyncio.sleep(0)
        with pytest.raises(DuplicateAgentError):
            ctrl.spawn("  BUILD   the PARSER ", isolate=False)  # normalized match
        assert ctrl.active_count() == 1

    asyncio.run(scenario())


def test_wait_short_circuits_when_nothing_running(tmp_path):
    async def scenario():
        ctrl = _HeldControl(str(tmp_path))
        return await ctrl.wait_for_activity(5.0)

    assert "no sub-agents" in asyncio.run(scenario()).lower()


def test_isolated_subagent_merges_back(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()

    async def scenario():
        ctrl = _WritingControl(str(ws))
        aid = ctrl.spawn("build feature", isolate=True)
        await ctrl.get(aid).handle  # let it finish
        return ctrl.get(aid)

    sub = asyncio.run(scenario())
    assert sub.status == "done"
    assert "merged cleanly" in sub.result
    assert (ws / "feature.py").read_text() == "VALUE = 1\n"  # landed in the base


def test_close_cancels_running_subagents(tmp_path):
    async def scenario():
        ctrl = _HeldControl(str(tmp_path))
        ctrl.spawn("t1", isolate=False)
        await asyncio.sleep(0)
        assert ctrl.active_count() == 1
        await ctrl.close()
        await asyncio.sleep(0)
        return ctrl.active_count()

    assert asyncio.run(scenario()) == 0


def test_cancel_running_waits_for_child_cleanup(tmp_path):
    async def scenario():
        ctrl = _HeldControl(str(tmp_path))
        agent_id = ctrl.spawn("t1", isolate=False)
        await asyncio.sleep(0)
        handle = ctrl.get(agent_id).handle

        await ctrl.cancel_running()

        assert handle.done()
        assert ctrl.get(agent_id).status == "failed"
        assert ctrl.get(agent_id).result == "cancelled"
        assert ctrl.active_count() == 0

    asyncio.run(scenario())


def test_spawn_tool_reports_limit(tmp_path):
    async def scenario():
        ctrl = _HeldControl(str(tmp_path), max_threads=1)
        tool = SpawnAgentTool(ctrl)
        first = await tool.execute(name="one", task="t1", isolate=False)
        await asyncio.sleep(0)
        second = await tool.execute(name="two", task="t2", isolate=False)
        return first, second

    first, second = asyncio.run(scenario())
    assert "Spawned one" in first
    assert "Error:" in second and "wait_agent" in second


def test_wait_tool_returns_outcome(tmp_path):
    ctrl = _HeldControl(str(tmp_path))
    out = asyncio.run(WaitAgentTool(ctrl).execute(timeout_ms=2000))
    assert "no sub-agents" in out.lower()
    assert WaitAgentTool(ctrl).read_only is True


def test_fork_history_filter(tmp_path):
    ctrl = _HeldControl(str(tmp_path))
    history = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "…", "tool_calls": [{"id": "1"}]},  # drop
        {"role": "tool", "content": "result"},  # drop
        {"role": "assistant", "content": "a1"},  # keep (final answer)
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},  # keep
    ]
    ctrl.set_history_provider(lambda: history)
    assert ctrl._fork_history("none") == []
    assert [m["content"] for m in ctrl._fork_history("all")] == ["u1", "a1", "u2", "a2"]
    assert [m["content"] for m in ctrl._fork_history(1)] == ["u2", "a2"]  # last 1 turn


def test_fork_seed_reaches_subagent(tmp_path):
    async def scenario():
        ctrl = _HeldControl(str(tmp_path))
        ctrl.set_history_provider(
            lambda: [
                {"role": "user", "content": "prior"},
                {"role": "assistant", "content": "ans"},
            ]
        )
        aid = ctrl.spawn("t", isolate=False, fork_turns="all")
        await asyncio.sleep(0)  # sub-agent records its seed, then blocks
        seed = ctrl.last_seed
        ctrl.release.set()
        await ctrl.get(aid).handle
        return seed

    assert asyncio.run(scenario()) == [
        {"role": "user", "content": "prior"},
        {"role": "assistant", "content": "ans"},
    ]


def test_parse_fork_turns():
    from core.harness.tools.spawn_agent import _parse_fork_turns

    assert _parse_fork_turns(None) == "none"
    assert _parse_fork_turns("all") == "all"
    assert _parse_fork_turns("3") == 3
    assert _parse_fork_turns("0") == "none"
    assert _parse_fork_turns("garbage") == "none"


def test_list_agents_tool(tmp_path):
    async def scenario():
        ctrl = _HeldControl(str(tmp_path))
        ctrl.spawn("build widget", isolate=False)
        await asyncio.sleep(0)
        return await ListAgentsTool(ctrl).execute()

    out = asyncio.run(scenario())
    assert "agent-1" in out and "running" in out and "build widget" in out
    assert asyncio.run(
        ListAgentsTool(_HeldControl(str(tmp_path))).execute()
    ).startswith("No sub-agents")


def test_interrupt_agent_tool(tmp_path):
    async def scenario():
        ctrl = _HeldControl(str(tmp_path))
        aid = ctrl.spawn("t", isolate=False)
        await asyncio.sleep(0)
        assert ctrl.active_count() == 1
        handle = ctrl.get(aid).handle
        out = await InterruptAgentTool(ctrl).execute(agent=aid)
        await asyncio.gather(handle, return_exceptions=True)  # let cancel settle
        return out, ctrl.get(aid).status

    out, status = asyncio.run(scenario())
    assert "interrupt requested" in out
    assert status == "failed"
    # unknown agent is reported, not crashed
    assert "no such agent" in asyncio.run(
        InterruptAgentTool(_HeldControl(str(tmp_path))).execute(agent="nope")
    )


def test_send_message_to_running_and_finished(tmp_path):
    async def scenario():
        ctrl = _HeldControl(str(tmp_path))
        aid = ctrl.spawn("t", isolate=False)
        await asyncio.sleep(0)  # sub-agent is now running (blocked)
        # deliver to a running sub-agent
        out = await SendMessageTool(ctrl).execute(agent=aid, message="use value 42")
        sub = ctrl.get(aid)
        drained_before = list(sub.inbox)
        # its own inbox drainer would inject the message on its next step
        drainer = ctrl._make_inbox_drainer(sub)
        injected = await drainer()
        # let it finish, then a second send must be refused
        ctrl.release.set()
        await sub.handle
        finished = await SendMessageTool(ctrl).execute(agent=aid, message="late")
        return out, drained_before, injected, finished

    out, inbox, injected, finished = asyncio.run(scenario())
    assert "delivered" in out
    assert inbox == [("parent:agent-1:1", "use value 42")]
    assert injected == [
        SubagentMessage(
            message_id="parent:agent-1:1",
            target_turn_id="agent-1",
            agent_id="parent",
            payload="use value 42",
        )
    ]
    assert "already finished" in finished


def test_send_message_unknown_and_empty(tmp_path):
    ctrl = _HeldControl(str(tmp_path))
    assert "no such agent" in asyncio.run(
        SendMessageTool(ctrl).execute(agent="nope", message="x")
    )


def test_build_wires_callable_history_provider(tmp_path, monkeypatch):
    # Regression: session.history is a @property (a list), so build_agent_session
    # must wrap it in a callable. Passing the value made fork_turns call a list
    # → "TypeError: 'list' object is not callable" on every forked spawn.
    from core import agent_setup

    profile = type("Profile", (), {"model": "test-model"})()
    monkeypatch.setattr(
        agent_setup,
        "get_workflow_provider",
        lambda **_kwargs: (object(), profile),
    )
    monkeypatch.setattr(
        agent_setup,
        "get_runtime",
        lambda: type("Runtime", (), {"config": object()})(),
    )

    session, _m, _e = agent_setup.build_agent_session(
        workspace=str(tmp_path),
        allow_spawn=True,
    )
    ctrl = session._agent_control
    assert callable(ctrl._history_provider)
    assert ctrl._history_provider() == session.history
    assert isinstance(ctrl._fork_history("all"), list)  # would raise before the fix


def test_build_wires_one_atomic_profile_into_engine_tools_and_control(
    tmp_path, monkeypatch
):
    from core import agent_setup
    from core.domain.execution_security import (
        ApprovalPolicy,
        ExecutionAccessPreset,
        ExecutionSecurityProfile,
    )
    from core.harness.permissions import PermissionMode

    llm_profile = type("Profile", (), {"model": "test-model"})()
    monkeypatch.setattr(
        agent_setup,
        "get_workflow_provider",
        lambda **_kwargs: (object(), llm_profile),
    )
    monkeypatch.setattr(
        agent_setup,
        "get_runtime",
        lambda: type("Runtime", (), {"config": object()})(),
    )
    monkeypatch.setenv("DEEPCODE_SANDBOX", "1")
    security = ExecutionSecurityProfile.for_preset(ExecutionAccessPreset.FULL_ACCESS)

    session, _model, engine = agent_setup.build_agent_session(
        workspace=str(tmp_path),
        allow_spawn=True,
        execution_security_profile=security,
    )

    assert engine.mode is PermissionMode.FULL_AUTO
    assert engine.approval_policy is ApprovalPolicy.NEVER
    assert session._tools.get("bash")._sandbox_enabled is False
    assert session._tools.get("write")._allow_outside_workspace is True
    assert session._tools.get("edit")._allow_outside_workspace is True
    assert session._tools.get("apply_patch")._allow_outside_workspace is True
    assert session._tools.get("code")._sandbox_enabled is False
    assert session._agent_control._execution_security_profile is security


def test_subagent_inherits_parent_permission_and_approval(tmp_path, monkeypatch):
    from core.events import Event, TaskComplete
    from core.harness.permissions import PermissionMode

    captured = {}

    class Session:
        async def aclose(self):
            captured["closed"] = True

        def load_history(self, _messages):
            return None

        async def run_stream(self, _op):
            yield Event("1", TaskComplete("done", "completed"))

    def build(**kwargs):
        captured.update(kwargs)
        return Session(), "model", object()

    monkeypatch.setattr("core.agent_setup.build_agent_session", build)
    approval = object()
    control = AgentControl(
        str(tmp_path),
        permission_mode=PermissionMode.DEFAULT,
        approval_callback=approval,
    )

    sub = SubAgent(id="child", task="task", isolate=False)
    result = asyncio.run(control._run_subagent(sub, str(tmp_path)))

    assert result == "done"
    assert captured["permission_mode_override"] is PermissionMode.DEFAULT
    assert captured["approval_callback"] is approval
    assert captured["closed"] is True


def test_subagent_inherits_atomic_execution_security_profile(tmp_path, monkeypatch):
    from core.domain.execution_security import (
        ExecutionAccessPreset,
        ExecutionSecurityProfile,
    )
    from core.events import Event, TaskComplete

    captured = {}

    class Session:
        async def aclose(self):
            captured["closed"] = True

        def load_history(self, _messages):
            return None

        async def run_stream(self, _op):
            yield Event("1", TaskComplete("done", "completed"))

    def build(**kwargs):
        captured.update(kwargs)
        return Session(), "model", object()

    monkeypatch.setattr("core.agent_setup.build_agent_session", build)
    profile = ExecutionSecurityProfile.for_preset(ExecutionAccessPreset.FULL_ACCESS)
    approval = object()
    control = AgentControl(
        str(tmp_path),
        execution_security_profile=profile,
        approval_callback=approval,
    )

    sub = SubAgent(id="child", task="task", isolate=False)
    result = asyncio.run(control._run_subagent(sub, str(tmp_path)))

    assert result == "done"
    assert captured["execution_security_profile"] is profile
    assert captured["permission_mode_override"] is None
    assert captured["approval_callback"] is approval
    assert captured["closed"] is True


def test_subagent_closes_session_when_stream_fails(tmp_path, monkeypatch):
    class Session:
        def __init__(self):
            self.closed = False

        async def run_stream(self, _op):
            if False:  # pragma: no cover - keeps this an async generator
                yield
            raise RuntimeError("stream failed")

        async def aclose(self):
            self.closed = True

    session = Session()
    monkeypatch.setattr(
        "core.agent_setup.build_agent_session",
        lambda **_kwargs: (session, "model", object()),
    )
    control = AgentControl(str(tmp_path))
    sub = SubAgent(id="x", task="task", isolate=False)

    with pytest.raises(RuntimeError, match="stream failed"):
        asyncio.run(control._run_subagent(sub, str(tmp_path)))

    assert session.closed is True


def test_subagent_closes_session_when_cancelled(tmp_path, monkeypatch):
    async def scenario():
        started = asyncio.Event()

        class Session:
            def __init__(self):
                self.closed = False

            async def run_stream(self, _op):
                started.set()
                await asyncio.Event().wait()
                if False:  # pragma: no cover - keeps this an async generator
                    yield

            async def aclose(self):
                self.closed = True

        session = Session()
        monkeypatch.setattr(
            "core.agent_setup.build_agent_session",
            lambda **_kwargs: (session, "model", object()),
        )
        control = AgentControl(str(tmp_path))
        sub = SubAgent(id="x", task="task", isolate=False)
        task = asyncio.create_task(control._run_subagent(sub, str(tmp_path)))
        await started.wait()

        task.cancel()
        outcome = await asyncio.gather(task, return_exceptions=True)

        assert isinstance(outcome[0], asyncio.CancelledError)
        assert session.closed is True

    asyncio.run(scenario())


def test_wiring_and_depth_cap():
    from core.harness.tools import default_coding_tools

    delegation = {
        "spawn_agent",
        "wait_agent",
        "list_agents",
        "interrupt_agent",
        "send_message",
    }
    # no control -> no delegation tools (a spawned sub-agent's case)
    assert not (delegation & set(default_coding_tools("/tmp").tool_names))
    # with a control -> all five delegation tools present
    reg = default_coding_tools("/tmp", agent_control=AgentControl("/tmp"))
    assert delegation <= set(reg.tool_names)
