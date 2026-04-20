"""Drop-in replacement for ``mcp_agent.app.MCPApp``.

Legacy callers used::

    app = MCPApp(name="paper_to_code")
    async with app.run() as agent_app:
        agent_app.context.config.mcp.servers["filesystem"].args.append(cwd)
        ...

Our replacement loads the DeepCode runtime (config + logger), exposes the
same ``context.config.mcp.servers`` namespace, and is a no-op on
``__aexit__`` because we no longer hold a single app-wide MCP connection
pool. Each :class:`core.compat.Agent` opens / closes its own MCP servers.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from core.compat.runtime import (
    DeepCodeRuntime,
    _ContextNamespace,
    get_runtime,
)


@dataclass(slots=True)
class _AgentApp:
    """Object yielded from :meth:`MCPApp.run`. Mirrors the old surface."""

    runtime: DeepCodeRuntime
    name: str

    @property
    def logger(self) -> Any:
        return self.runtime.logger

    @property
    def context(self) -> _ContextNamespace:
        return self.runtime.context


class MCPApp:
    """Compatibility shim over the legacy ``MCPApp``.

    Parameters
    ----------
    name:
        Logical name of the app. Currently informational only.
    settings:
        Optional path to ``mcp_agent.config.yaml``. When provided we re-load
        the DeepCode runtime from that file; otherwise the global runtime
        is reused.
    """

    def __init__(self, name: str = "deepcode", settings: str | None = None) -> None:
        self.name = name
        self._settings_path = settings
        self._runtime: DeepCodeRuntime | None = None
        self._agent_app: _AgentApp | None = None

    @asynccontextmanager
    async def run(self) -> AsyncIterator[_AgentApp]:
        """Async context manager that yields the legacy ``agent_app`` shape."""
        try:
            if self._settings_path is not None:
                self._runtime = DeepCodeRuntime.load(config_path=self._settings_path)
            else:
                self._runtime = get_runtime()
            self._agent_app = _AgentApp(runtime=self._runtime, name=self.name)
            yield self._agent_app
        finally:
            self._agent_app = None
