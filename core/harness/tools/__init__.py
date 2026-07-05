"""Native coding tools (P2, L2).

Until now every file/shell operation went through MCP servers. P2 adds a
small set of *native* tools built on the kernel's ``Tool`` base class, so a
general coding agent can read / write / edit files directly with the
reliability that makes Codex/Claude Code pleasant to use. The centerpiece is
:mod:`core.harness.tools.replace` — a fuzzy edit engine (nine matching
strategies) that makes exact-string edits succeed even when whitespace or
indentation drift, instead of failing on the first mismatch.

These are pure mechanism: file I/O + the permission/sandbox seams from P1.
``default_coding_tools`` assembles them into a :class:`ToolRegistry` any
:class:`~core.events.session.AgentSession` can consume.
"""

from core.harness.tools.files import EditTool, ReadTool, WriteTool
from core.harness.tools.replace import (
    DisproportionateMatchError,
    MultipleMatchesError,
    NotFoundError,
    replace,
)
from core.harness.tools.search import GlobTool, GrepTool
from core.harness.tools.shell import BashTool

__all__ = [
    "ReadTool",
    "WriteTool",
    "EditTool",
    "BashTool",
    "GrepTool",
    "GlobTool",
    "replace",
    "NotFoundError",
    "MultipleMatchesError",
    "DisproportionateMatchError",
    "default_coding_tools",
]


def default_coding_tools(workspace):
    """Build a :class:`ToolRegistry` with the native coding tool set.

    read / write / edit / bash / grep / glob over ``workspace``. ``workspace``
    is the root the tools resolve relative paths against and, for write / edit
    / bash, the boundary they are fenced to.
    """
    from core.agent_runtime.tools.registry import ToolRegistry

    registry = ToolRegistry()
    for tool in (
        ReadTool(workspace),
        WriteTool(workspace),
        EditTool(workspace),
        BashTool(workspace),
        GrepTool(workspace),
        GlobTool(workspace),
    ):
        registry.register(tool)
    return registry
