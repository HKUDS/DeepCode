"""Session runtime cache invalidation after configuration changes."""

import asyncio

from core.application.session_runtime import SessionRuntimeRegistry
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
