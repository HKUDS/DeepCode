"""Session runtime cache invalidation after configuration changes."""

import asyncio
import threading

import pytest

from core.agent_runtime.injections import SubagentMessage
from core.application.errors import ConflictError
from core.application.session_runtime import SessionRuntimeRegistry
from core.domain.execution_permission import ExecutionPermissionMode
from core.sessions import SessionStore


class _Agent:
    def __init__(self) -> None:
        self.closed = False
        self.loaded = []

    @property
    def history(self):
        return list(self.loaded)

    def load_history(self, messages) -> None:
        self.loaded = list(messages)

    async def aclose(self) -> None:
        self.closed = True


class _Factory:
    def __init__(self) -> None:
        self.token = 1
        self.created: list[_Agent] = []

    def runtime_key(self, *, workspace: str, model: str | None) -> object:
        return (workspace, model, self.token)

    def create(self, *, workspace: str, model: str | None, approval_callback):
        del workspace, model, approval_callback
        agent = _Agent()
        self.created.append(agent)
        return agent


def test_idle_agent_runtime_is_rebuilt_when_configuration_token_changes(
    tmp_path,
) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create_session(
        title="runtime",
        metadata={"workspace": str(tmp_path)},
    )
    factory = _Factory()
    registry = SessionRuntimeRegistry(store, factory)

    async def exercise() -> None:
        first = await registry.acquire(
            session.session_id,
            workspace=str(tmp_path),
            model=None,
            approval_callback=lambda *_args: False,
        )
        registry.release(session.session_id)
        reused = await registry.acquire(
            session.session_id,
            workspace=str(tmp_path),
            model=None,
            approval_callback=lambda *_args: False,
        )
        assert reused is first
        registry.release(session.session_id)

        factory.token += 1
        replaced = await registry.acquire(
            session.session_id,
            workspace=str(tmp_path),
            model=None,
            approval_callback=lambda *_args: False,
        )
        assert replaced is not first
        assert first.closed is True
        await registry.close_all()

    asyncio.run(exercise())


def test_permission_snapshot_is_part_of_runtime_identity(tmp_path) -> None:
    class PermissionFactory(_Factory):
        def __init__(self) -> None:
            super().__init__()
            self.permission_modes = []

        def create(
            self,
            *,
            workspace,
            model,
            approval_callback,
            permission_mode_override,
        ):
            self.permission_modes.append(permission_mode_override)
            return super().create(
                workspace=workspace,
                model=model,
                approval_callback=approval_callback,
            )

    store = SessionStore(tmp_path / "sessions")
    session = store.create_session(
        title="runtime",
        metadata={"workspace": str(tmp_path)},
    )
    factory = PermissionFactory()
    registry = SessionRuntimeRegistry(store, factory)

    async def exercise() -> None:
        inherited = await registry.acquire(
            session.session_id,
            workspace=str(tmp_path),
            model=None,
            permission_mode_override=None,
            approval_callback=lambda *_args: False,
        )
        registry.release(session.session_id)
        explicit = await registry.acquire(
            session.session_id,
            workspace=str(tmp_path),
            model=None,
            permission_mode_override=ExecutionPermissionMode.DEFAULT,
            approval_callback=lambda *_args: False,
        )

        assert explicit is not inherited
        assert inherited.closed is True
        assert factory.permission_modes == [
            None,
            ExecutionPermissionMode.DEFAULT,
        ]
        await registry.close_all()

    asyncio.run(exercise())


def test_runtime_resume_rehydrates_provider_continuation_state(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create_session(
        title="runtime",
        metadata={"workspace": str(tmp_path)},
    )
    store.append_message(session.session_id, "user", "hello")
    store.append_message(
        session.session_id,
        "assistant",
        "answer",
        metadata={
            "providerState": {"encrypted": "opaque"},
            "reasoningSummary": "Safe summary",
        },
    )
    factory = _Factory()
    registry = SessionRuntimeRegistry(store, factory)

    async def exercise() -> None:
        agent = await registry.acquire(
            session.session_id,
            workspace=str(tmp_path),
            model=None,
            approval_callback=lambda *_args: False,
        )
        assert agent.history[-1] == {
            "role": "assistant",
            "content": "answer",
            "provider_state": {"encrypted": "opaque"},
            "reasoning_summary": "Safe summary",
        }
        await registry.close_all()

    asyncio.run(exercise())


def test_runtime_wires_internal_messages_into_the_active_turn_mailbox(tmp_path) -> None:
    class InputFactory(_Factory):
        def create(
            self,
            *,
            workspace,
            model,
            approval_callback,
            injection_callback,
            active_turn_id_provider,
            runtime_input_sink,
        ):
            agent = super().create(
                workspace=workspace,
                model=model,
                approval_callback=approval_callback,
            )
            self.drain = injection_callback
            self.active_turn_id = active_turn_id_provider
            self.sink = runtime_input_sink
            return agent

    store = SessionStore(tmp_path / "sessions")
    session = store.create_session(
        title="runtime",
        metadata={"workspace": str(tmp_path)},
    )
    factory = InputFactory()
    registry = SessionRuntimeRegistry(store, factory)

    async def exercise() -> None:
        await registry.acquire(
            session.session_id,
            workspace=str(tmp_path),
            model=None,
            approval_callback=lambda *_args: False,
        )
        registry.activate_inputs(session.session_id, turn_id="turn-current")
        message = SubagentMessage(
            message_id="child-result-1",
            target_turn_id="turn-current",
            agent_id="child",
            payload="Result",
        )
        assert factory.active_turn_id() == "turn-current"
        assert factory.sink(message) is True
        assert await factory.drain() == [message]

        assert await factory.drain(close_if_empty=True) == []
        assert factory.active_turn_id() is None
        assert (
            factory.sink(
                SubagentMessage(
                    message_id="child-result-late",
                    target_turn_id="turn-current",
                    agent_id="child",
                    payload="Late result",
                )
            )
            is False
        )
        await registry.close_all()

    asyncio.run(exercise())


class _CompactingAgent(_Agent):
    """Blocks inside compact() until the test lets the summary finish."""

    def __init__(self) -> None:
        super().__init__()
        self.compact_started = threading.Event()
        self.finish_compact = threading.Event()

    async def compact(self):
        self.compact_started.set()
        await asyncio.to_thread(self.finish_compact.wait, 5)
        self.loaded = [{"role": "user", "content": "summary"}]
        return {"compacted": True}


class _CompactingFactory(_Factory):
    def create(self, *, workspace: str, model: str | None, approval_callback):
        del workspace, model, approval_callback
        agent = _CompactingAgent()
        self.created.append(agent)
        return agent


def _compact_on_rpc_thread(registry, session_id) -> dict:
    """Run /compact the way the service does: its own thread and loop."""
    outcome: dict = {}

    def run() -> None:
        try:
            outcome["report"] = asyncio.run(registry.compact_live_history(session_id))
        except Exception as exc:  # noqa: BLE001 - surfaced through outcome
            outcome["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    outcome["thread"] = thread
    return outcome


def test_turn_waits_for_compaction_and_compaction_is_never_evicted(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create_session(
        title="runtime", metadata={"workspace": str(tmp_path)}
    )
    other = store.create_session(title="other", metadata={"workspace": str(tmp_path)})
    factory = _CompactingFactory()
    registry = SessionRuntimeRegistry(store, factory, max_live_sessions=1)

    def acquire(session_id):
        return registry.acquire(
            session_id,
            workspace=str(tmp_path),
            model=None,
            approval_callback=lambda *_args: False,
        )

    async def exercise() -> None:
        agent = await acquire(session.session_id)
        registry.release(session.session_id)

        compaction = _compact_on_rpc_thread(registry, session.session_id)
        assert await asyncio.to_thread(agent.compact_started.wait, 5)

        # Another Session's Turn needs the only live slot: the compacting
        # agent is busy, so it must not be evicted (closed) under /compact.
        await acquire(other.session_id)
        registry.release(other.session_id)
        assert agent.closed is False

        # A Turn on the compacting Session waits instead of running on the
        # same agent while its history is being rewritten.
        turn = asyncio.create_task(acquire(session.session_id))
        await asyncio.sleep(0.3)
        assert not turn.done()

        agent.finish_compact.set()
        assert await asyncio.wait_for(turn, 5) is agent
        await asyncio.to_thread(compaction["thread"].join, 5)
        assert compaction.get("report") == {"compacted": True}
        assert agent.history == [{"role": "user", "content": "summary"}]
        registry.release(session.session_id)
        await registry.close_all()

    asyncio.run(exercise())


def test_compaction_is_refused_while_a_turn_runs(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create_session(
        title="runtime", metadata={"workspace": str(tmp_path)}
    )
    registry = SessionRuntimeRegistry(store, _CompactingFactory())

    async def exercise() -> None:
        await registry.acquire(
            session.session_id,
            workspace=str(tmp_path),
            model=None,
            approval_callback=lambda *_args: False,
        )
        with pytest.raises(ConflictError):
            await registry.compact_live_history(session.session_id)
        registry.release(session.session_id)
        await registry.close_all()

    asyncio.run(exercise())
