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
from core.harness.tools.patch import ApplyPatchTool, PatchError, parse_patch
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
    "ApplyPatchTool",
    "BashTool",
    "GrepTool",
    "GlobTool",
    "replace",
    "parse_patch",
    "PatchError",
    "NotFoundError",
    "MultipleMatchesError",
    "DisproportionateMatchError",
    "default_coding_tools",
]


def default_coding_tools(workspace, *, skills=None, ask_user=None):
    """Build a :class:`ToolRegistry` with the native coding tool set.

    read / write / edit / apply_patch / bash / grep / glob / memory / update_plan
    over ``workspace``. ``workspace`` is the root the tools resolve relative
    paths against and, for write / edit / apply_patch / bash, the boundary they
    are fenced to. ``memory`` persists notes under ``.deepcode/memory/``;
    ``update_plan`` is the agent's self-driven TODO plan.

    ``skills``: an optional pre-discovered :class:`~core.harness.skills.
    SkillRegistry`. When omitted it is discovered from the workspace. The
    ``skill`` tool is added only when at least one skill exists, so a workspace
    with no skills keeps the exact base tool set.

    ``ask_user``: an optional callback that prompts a human. When provided (an
    interactive frontend), the ``request_user_input`` tool is registered so the
    agent can ask when blocked; headless runs pass none and the tool is absent.
    """
    from core.agent_runtime.tools.registry import ToolRegistry
    from core.harness.memory import MemoryTool
    from core.harness.skills import SkillTool, discover_skills
    from core.harness.tools.plan import UpdatePlanTool
    from core.harness.tools.user_input import RequestUserInputTool

    registry = ToolRegistry()
    tools = [
        ReadTool(workspace),
        WriteTool(workspace),
        EditTool(workspace),
        ApplyPatchTool(workspace),
        BashTool(workspace),
        GrepTool(workspace),
        GlobTool(workspace),
        MemoryTool(workspace),
        UpdatePlanTool(),  # the agent's self-driven TODO plan
    ]
    # Standalone fallback is workspace-hermetic (no ambient ~/.claude scan);
    # build_agent_session passes the full project+user set via `skills`.
    skill_registry = (
        discover_skills(workspace, include_user=False) if skills is None else skills
    )
    if skill_registry:
        tools.append(SkillTool(skill_registry))
    if ask_user is not None:
        tools.append(RequestUserInputTool(ask_user))
    for tool in tools:
        registry.register(tool)
    return registry
