"""Session runtime cache invalidation after configuration changes."""

import asyncio

from core.agent_runtime.injections import SubagentMessage
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
