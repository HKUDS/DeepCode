"""Drop-in replacement for ``mcp_agent.agents.agent.Agent``.

The legacy code constructs an :class:`Agent` with a name, a system
``instruction`` and a list of MCP ``server_names`` to expose. It then::

    await agent.__aenter__()
    llm = await agent.attach_llm(GoogleAugmentedLLM)
    text = await llm.generate_str(message=prompt, request_params=params)
    result = await agent.call_tool("read_file", {"path": "..."})
    await agent.__aexit__(None, None, None)

Our shim opens MCP sessions on-demand from the global runtime, registers
the requested servers' tools into a fresh :class:`ToolRegistry`, and
returns an :class:`AugmentedLLM` whose ``generate_str`` runs an
:class:`AgentRunner` loop with that registry. This preserves the legacy
ergonomics while routing every LLM/tool call through the new ``core``
stack.
"""

from __future__ import annotations

import asyncio
from typing import Any, Iterable, Type

from loguru import logger

from core.agent_runtime.runner import AgentRunner, AgentRunSpec
from core.agent_runtime.tools.mcp import connect_mcp_servers
from core.agent_runtime.tools.registry import ToolRegistry
from core.compat.request_params import RequestParams
from core.compat.runtime import DeepCodeRuntime, get_runtime
from core.providers.base import LLMProvider


async def _close_registry_quietly(registry: ToolRegistry, agent_name: str) -> None:
    """Drain ``registry.aclose()`` without letting MCP stdio cleanup poison the caller.

    The MCP ``stdio_client`` uses ``anyio`` cancel scopes that are anchored
    to the task on which they were *entered* (here, the pipeline task in
    :func:`connect_mcp_servers`). Closing the stack later — even if we
    detour through a side task — cancels that anchor task. Without
    intervention the next ``await`` after ``async with agent:`` then
    re-raises the cancellation as a spurious :class:`asyncio.CancelledError`,
    aborting the pipeline mid-flight.

    Mitigation:

    1. Catch every :class:`BaseException` from ``aclose()`` (incl.
       ``CancelledError`` and ``BaseExceptionGroup``).
    2. Reset the cancellation counter on the current task with
       :meth:`asyncio.Task.uncancel` (3.11+) so the *next* ``await`` no
       longer sees the dangling cancel.

    Note this deliberately swallows :class:`asyncio.CancelledError` here.
    A real user-initiated cancel will reassert itself at the next
    cancellation check (event loop polls ``Task.cancelling()`` before each
    await), so we are not hiding a Ctrl-C from the caller — only the
    internal anyio teardown noise.
    """
    try:
        await registry.aclose()
    except asyncio.CancelledError:
        logger.debug(
            "Agent '{}' close raised CancelledError from anyio scope; absorbing",
            agent_name,
        )
    except BaseException as exc:  # noqa: BLE001 - log and swallow
        logger.warning(
            "Agent '{}' tool registry close error: {}", agent_name, exc
        )

    current = asyncio.current_task()
    if current is not None:
        # ``Task.uncancel()`` only decrements the cancel counter; the loop's
        # ``_must_cancel`` flag (set by an internal ``Task.cancel()`` call
        # from anyio's scope teardown) is what actually re-raises on the
        # next ``await``. We have to clear both.
        if hasattr(current, "uncancel"):
            try:
                while current.cancelling():  # type: ignore[attr-defined]
                    current.uncancel()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
        try:
            if getattr(current, "_must_cancel", False):
                current._must_cancel = False  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


# Keep "filter references missing server" warnings down to one per
# (agent_name, server) pair per process, so the log stays useful.
_WARNED_MISSING_FILTER_SERVERS: set[tuple[str, str]] = set()


def _apply_tool_filter(
    registry: ToolRegistry,
    tool_filter: dict[str, set[str]] | None,
    *,
    agent_name: str = "<unknown>",
    agent_server_names: Iterable[str] | None = None,
) -> ToolRegistry:
    """Return a (possibly filtered) view of ``registry`` honouring ``tool_filter``.

    Semantics mirror the legacy ``mcp_agent.RequestParams.tool_filter``:

    * ``None`` or empty dict → no filtering, return the registry unchanged.
    * Non-empty dict ``{server: {tool_names}}`` → keep only MCP tools whose
      underlying server is listed; within each listed server, keep only the
      tools whose original (un-prefixed) name is in the allow-set, or *all*
      that server's tools if the allow-set is empty. Non-MCP tools are
      preserved unconditionally.

    Diagnostics: if ``tool_filter`` references a server that the agent
    actually requested (it's in ``agent_server_names``) but that server is
    *not* represented in the registry, the connection failed and a one-shot
    ``logger.warning`` is emitted. If the filter mentions a server the agent
    did not request — common with ``ParallelLLM`` where the same
    ``RequestParams`` is shared across fan-out agents with different
    ``server_names`` — the filter line is treated as a no-op and only logged
    at DEBUG level.

    The returned :class:`ToolRegistry` references the same :class:`Tool`
    objects but owns no MCP server stacks, so closing it is a no-op (the
    original registry remains responsible for stdio process cleanup).
    """
    if not tool_filter:
        return registry

    # ``None`` means "caller did not tell us which servers the agent asked for"
    # (legacy path) → fall back to the original behaviour and warn whenever
    # the filter mentions an unregistered server. An *empty* set means "caller
    # explicitly said the agent requested zero servers" → every filter entry
    # is a deliberate no-op (common when a ParallelLLM fan-out shares the
    # parent's RequestParams) and should stay silent.
    requested_servers: set[str] | None = (
        None if agent_server_names is None else {str(s) for s in agent_server_names}
    )

    registry_servers: set[str] = set()
    for tool_name in registry.tool_names:
        if not tool_name.startswith("mcp_"):
            continue
        for srv in tool_filter.keys():
            if tool_name.startswith(f"mcp_{srv}_"):
                registry_servers.add(srv)

    for srv in tool_filter.keys():
        if srv in registry_servers:
            continue
        if requested_servers is not None and srv not in requested_servers:
            logger.debug(
                "Agent '{}' tool_filter mentions server '{}' which is not in "
                "the agent's server_names; ignoring (expected under ParallelLLM).",
                agent_name,
                srv,
            )
            continue
        key = (agent_name, srv)
        if key in _WARNED_MISSING_FILTER_SERVERS:
            continue
        _WARNED_MISSING_FILTER_SERVERS.add(key)
        logger.warning(
            "Agent '{}' requested MCP server '{}' (in server_names) but no "
            "tools from that server are registered — the server most likely "
            "failed to connect. Check the corresponding mcp_agent.config.yaml "
            "entry. The filter will exclude it for now.",
            agent_name,
            srv,
        )

    filtered = ToolRegistry()
    for tool_name in registry.tool_names:
        tool = registry.get(tool_name)
        if tool is None:
            continue
        if not tool_name.startswith("mcp_"):
            filtered.register(tool)
            continue
        kept = False
        for server, allowed in tool_filter.items():
            prefix = f"mcp_{server}_"
            if not tool_name.startswith(prefix):
                continue
            if not allowed:
                kept = True
            else:
                bare = tool_name[len(prefix):]
                if bare in allowed or tool_name in allowed:
                    kept = True
            break
        if kept:
            filtered.register(tool)
    return filtered


class AugmentedLLM:
    """Compat wrapper that exposes ``generate_str`` over an :class:`AgentRunner`.

    The class subclasses (``AnthropicAugmentedLLM`` / ``OpenAIAugmentedLLM``
    / ``GoogleAugmentedLLM``) act purely as markers so the legacy callsites
    can keep passing a class to :meth:`Agent.attach_llm`. The real provider
    is selected by inspecting the marker class name and falling back to the
    runtime's configured ``llm_provider``.
    """

    PROVIDER_NAME: str | None = None
    DEFAULT_MAX_ITERATIONS: int = 8
    DEFAULT_MAX_TOOL_RESULT_CHARS: int = 60_000

    def __init__(
        self,
        agent: "Agent",
        provider: LLMProvider,
        provider_name: str,
        phase: str = "default",
    ) -> None:
        self.agent = agent
        self.provider = provider
        self.provider_name = provider_name
        self.phase = phase
        self._runner = AgentRunner(provider)

    async def generate_str(
        self,
        message: str,
        request_params: RequestParams | None = None,
    ) -> str:
        """Run a single ``user`` turn through :class:`AgentRunner` and return text.

        Mirrors ``mcp_agent.workflows.llm.augmented_llm.AugmentedLLM.generate_str``:
        the system prompt is the agent's ``instruction``; the registered
        tools come from the agent's ``server_names``.
        """
        params = request_params or RequestParams()
        tools = self.agent._tool_registry  # noqa: SLF001 - intentional shim access
        tools = _apply_tool_filter(
            tools,
            params.tool_filter,
            agent_name=self.agent.name,
            agent_server_names=self.agent.server_names,
        )

        messages: list[dict[str, Any]] = []
        if self.agent.instruction:
            messages.append({"role": "system", "content": self.agent.instruction})
        messages.append({"role": "user", "content": message})

        max_tokens = params.resolved_max_tokens()
        max_iterations = max(int(params.max_iterations or 1), 1)

        spec = AgentRunSpec(
            initial_messages=messages,
            tools=tools,
            model=params.model or self.provider.default_model,
            max_iterations=max(max_iterations, self.DEFAULT_MAX_ITERATIONS if tools.tool_names else 1),
            max_tool_result_chars=self.DEFAULT_MAX_TOOL_RESULT_CHARS,
            temperature=params.temperature,
            max_tokens=max_tokens,
            session_key=self.agent.name,
            concurrent_tools=bool(params.parallel_tool_calls),
        )

        result = await self._runner.run(spec)
        if result.error and not result.final_content:
            raise RuntimeError(f"AugmentedLLM error: {result.error}")
        return result.final_content or ""


class AnthropicAugmentedLLM(AugmentedLLM):
    """Marker subclass for legacy ``attach_llm(AnthropicAugmentedLLM)`` calls."""

    PROVIDER_NAME = "anthropic"


class OpenAIAugmentedLLM(AugmentedLLM):
    """Marker subclass for legacy ``attach_llm(OpenAIAugmentedLLM)`` calls."""

    PROVIDER_NAME = "openai"


class GoogleAugmentedLLM(AugmentedLLM):
    """Marker subclass for legacy ``attach_llm(GoogleAugmentedLLM)`` calls."""

    PROVIDER_NAME = "gemini"


class Agent:
    """Compat shim mirroring ``mcp_agent.agents.agent.Agent``."""

    def __init__(
        self,
        name: str,
        instruction: str = "",
        server_names: Iterable[str] | None = None,
        functions: Iterable[Any] | None = None,  # accepted for compat, unused
        connection_persistence: bool = True,
        human_input_callback: Any | None = None,
        request_params: RequestParams | None = None,
    ) -> None:
        self.name = name
        self.instruction = instruction
        self.server_names: list[str] = list(server_names or [])
        self.functions = list(functions or [])
        self.connection_persistence = connection_persistence
        self.human_input_callback = human_input_callback
        self.request_params = request_params

        self._runtime: DeepCodeRuntime | None = None
        self._tool_registry: ToolRegistry = ToolRegistry()
        self._connected: bool = False
        self._supervisor_task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None
        self._ready_event: asyncio.Event | None = None
        self._setup_error: BaseException | None = None

    async def __aenter__(self) -> "Agent":
        """Open MCP sessions on a dedicated supervisor task.

        Why a supervisor task? The MCP ``stdio_client`` uses anyio cancel
        scopes that are anchored to the task in which the stack was
        entered. If we open AND close the stack on the caller's pipeline
        task, anyio's teardown can inject a stray :class:`CancelledError`
        into the caller, aborting whatever ``await`` follows
        ``async with agent:``.

        Pinning the entire MCP lifecycle (connect → serve → close) to a
        dedicated task isolates anyio's cancel semantics there. The
        caller's task is never targeted by the scope, so no spurious
        cancellation can leak out.

        MCP tool *invocations* from the caller's task remain safe because
        the stdio session is just a JSON-RPC pipe — ``call_tool`` from any
        task sends a request and awaits a response future, no cancel
        scope manipulation involved.
        """
        self._runtime = get_runtime()
        self._stop_event = asyncio.Event()
        self._ready_event = asyncio.Event()
        self._setup_error = None
        loop = asyncio.get_running_loop()
        self._supervisor_task = loop.create_task(
            self._supervise_mcp(), name=f"agent-mcp[{self.name}]"
        )
        await self._ready_event.wait()
        if self._setup_error is not None:
            err = self._setup_error
            self._setup_error = None
            self._supervisor_task = None
            raise err
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            if self._stop_event is not None:
                self._stop_event.set()
            if self._supervisor_task is not None:
                # ``asyncio.wait`` never re-raises the awaited task's exception
                # or its cancellation; perfect for supervised teardown.
                await asyncio.wait({self._supervisor_task})
        finally:
            # Anyio's cancel-scope teardown sometimes leaves a stale cancel
            # request on the *current* task even when we contained the
            # supervisor. Defensively clear both the cancel counter and the
            # ``_must_cancel`` flag so the next ``await`` doesn't raise.
            current = asyncio.current_task()
            if current is not None:
                if hasattr(current, "uncancel"):
                    try:
                        while current.cancelling():  # type: ignore[attr-defined]
                            current.uncancel()  # type: ignore[attr-defined]
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    if getattr(current, "_must_cancel", False):
                        current._must_cancel = False  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    pass
            self._tool_registry = ToolRegistry()
            self._connected = False
            self._runtime = None
            self._supervisor_task = None
            self._stop_event = None
            self._ready_event = None

    async def _supervise_mcp(self) -> None:
        """Body of the supervisor task: connect → wait-for-stop → close.

        All MCP stack ``__aenter__`` and ``__aexit__`` calls happen in
        this single task, so the anyio cancel-scope contract is honoured
        and no cancellation escapes to the caller.
        """
        try:
            await self._connect_servers()
        except BaseException as exc:  # noqa: BLE001
            self._setup_error = exc
            assert self._ready_event is not None
            self._ready_event.set()
            return

        assert self._ready_event is not None
        self._ready_event.set()

        assert self._stop_event is not None
        try:
            await self._stop_event.wait()
        except BaseException:  # noqa: BLE001
            pass

        try:
            await self._tool_registry.aclose()
        except BaseException as exc:  # noqa: BLE001 - log and swallow
            logger.warning(
                "Agent '{}' MCP supervisor close error: {}", self.name, exc
            )

    async def _connect_servers(self) -> None:
        if self._connected:
            return
        if not self.server_names:
            self._connected = True
            return
        runtime = self._runtime
        if runtime is None:
            runtime = get_runtime()
            self._runtime = runtime

        configured = runtime.config.mcp_servers
        wanted: dict[str, Any] = {}
        missing: list[str] = []
        for srv in self.server_names:
            cfg = configured.get(srv)
            if cfg is None:
                missing.append(srv)
                continue
            wanted[srv] = cfg

        if missing:
            available = sorted(configured.keys())
            logger.warning(
                "Agent '{}': MCP server(s) {} not in mcp_agent.config.yaml. "
                "Available: {}. The agent will run without those tools.",
                self.name,
                missing,
                available,
            )

        if wanted:
            await connect_mcp_servers(wanted, self._tool_registry)
            connected = sorted(self._tool_registry._owned_server_stacks.keys())  # noqa: SLF001
            failed = [name for name in wanted if name not in connected]
            if failed:
                logger.warning(
                    "Agent '{}': MCP server(s) {} failed to start "
                    "(check command/args/env in mcp_agent.config.yaml).",
                    self.name,
                    failed,
                )
            logger.debug(
                "Agent '{}': {} MCP servers connected, {} tools registered total",
                self.name,
                len(connected),
                len(self._tool_registry),
            )
        self._connected = True

    async def attach_llm(
        self,
        llm_class: Type[AugmentedLLM] | None = None,
        *,
        phase: str = "default",
        model: str | None = None,
    ) -> AugmentedLLM:
        """Return an :class:`AugmentedLLM` for the configured provider.

        ``llm_class`` is honoured if it identifies a provider (e.g.
        :class:`AnthropicAugmentedLLM`); otherwise the runtime's configured
        ``llm_provider`` is used.
        """
        runtime = self._runtime or get_runtime()
        self._runtime = runtime

        provider_name: str | None = None
        if llm_class is not None and getattr(llm_class, "PROVIDER_NAME", None):
            provider_name = llm_class.PROVIDER_NAME

        provider = runtime.provider_for(
            provider_name=provider_name,
            phase=phase,
            model=model,
        )
        cls = llm_class if isinstance(llm_class, type) and issubclass(llm_class, AugmentedLLM) else AugmentedLLM
        return cls(
            agent=self,
            provider=provider,
            provider_name=provider_name or runtime.config.llm_provider,
            phase=phase,
        )

    async def list_tools(self) -> dict[str, Any]:
        """Return a ``{"tools": [...]}`` mapping mirroring the legacy interface."""
        return {"tools": list(self._tool_registry.get_definitions())}

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Invoke a registered tool by name.

        ``name`` may be the bare tool name (e.g. ``"read_file"``) or the
        wrapped MCP name (``"mcp_filesystem_read_file"``); we try both.
        """
        params = arguments or {}
        if not self._tool_registry.has(name):
            for candidate in self._tool_registry.tool_names:
                if candidate.endswith(f"_{name}") or candidate.endswith(name):
                    name = candidate
                    break
        return await self._tool_registry.execute(name, params)
