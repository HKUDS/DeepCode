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

from core.domain.execution_security import ExecutionSecurityProfile, FilesystemScope
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
from core.harness.tools.web import WebFetchTool

__all__ = [
    "ApplyPatchTool",
    "BashTool",
    "DisproportionateMatchError",
    "EditTool",
    "GlobTool",
    "GrepTool",
    "MultipleMatchesError",
    "NotFoundError",
    "PatchError",
    "ReadTool",
    "WriteTool",
    "WebFetchTool",
    "default_coding_tools",
    "parse_patch",
    "replace",
]


def default_coding_tools(
    workspace,
    *,
    skills=None,
    skill_runtime=None,
    ask_user=None,
    agent_control=None,
    goal_runtime=None,
    execution_security_profile: ExecutionSecurityProfile | None = None,
):
    """Build a :class:`ToolRegistry` with the native coding tool set.

    read / write / edit / apply_patch / bash / grep / glob / memory / update_plan
    over ``workspace``. ``workspace`` is the root the tools resolve relative
    paths against and, unless an explicit Full Access profile is supplied, the
    boundary write / edit / apply_patch / bash are fenced to. ``memory``
    persists notes under ``.deepcode/memory/``;
    ``update_plan`` is the agent's self-driven TODO plan.

    ``skill_runtime`` is the preferred shared, live catalog. ``skills`` remains
    as a compatibility input for callers with a static ``SkillRegistry``.

    ``ask_user``: an optional callback that prompts a human. When provided (an
    interactive frontend), the ``request_user_input`` tool is registered so the
    agent can ask when blocked; headless runs pass none and the tool is absent.

    ``agent_control``: an :class:`~core.harness.agents.control.AgentControl`.
    When provided (a top-level session), the ``spawn_agent`` + ``wait_agent``
    delegation tools are registered; a spawned sub-agent gets no control, so
    delegation cannot recurse.
    """
    from core.agent_runtime.tools.registry import ToolRegistry
    from core.harness.memory import MemoryTool
    from core.harness.skills import SkillTool, discover_skills
    from core.harness.tools.goal import GetGoalTool, UpdateGoalTool
    from core.harness.tools.plan import UpdatePlanTool
    from core.harness.tools.spawn_agent import (
        InterruptAgentTool,
        ListAgentsTool,
        SendMessageTool,
        SpawnAgentTool,
        WaitAgentTool,
    )
    from core.harness.tools.user_input import RequestUserInputTool

    registry = ToolRegistry()
    allow_outside_workspace = bool(
        execution_security_profile is not None
        and execution_security_profile.filesystem_scope is FilesystemScope.UNRESTRICTED
    )
    command_sandbox = (
        execution_security_profile.command_sandbox
        if execution_security_profile is not None
        else None
    )
    tools = [
        ReadTool(workspace),
        WriteTool(
            workspace,
            allow_outside_workspace=allow_outside_workspace,
        ),
        EditTool(
            workspace,
            allow_outside_workspace=allow_outside_workspace,
        ),
        ApplyPatchTool(
            workspace,
            allow_outside_workspace=allow_outside_workspace,
        ),
        BashTool(workspace, sandbox_enabled=command_sandbox),
        GrepTool(workspace),
        GlobTool(workspace),
        MemoryTool(workspace),
        UpdatePlanTool(),  # the agent's self-driven TODO plan
        WebFetchTool(),
    ]
    # Standalone fallback is workspace-hermetic (no ambient ~/.claude scan);
    # build_agent_session passes the full project+user set via `skills`.
    if skill_runtime is not None:
        # Always register the dynamic tool: a Skill added after Session startup
        # must become available on the next Turn without rebuilding the Agent.
        tools.append(SkillTool(skill_runtime))
    else:
        skill_registry = (
            discover_skills(workspace, include_user=False) if skills is None else skills
        )
        if skill_registry:
            tools.append(SkillTool(skill_registry))
    if ask_user is not None:
        tools.append(RequestUserInputTool(ask_user))
    if agent_control is not None:
        # The parent's tool names are the vocabulary for spawn_agent's
        # `tools` allowlist: a child is built with (almost) the same set, so
        # validating against them at spawn catches a misspelled name before
        # it silently strips the child of the very tool it needed.
        known_tool_names = tuple(tool.name for tool in tools)
        tools.append(SpawnAgentTool(agent_control, known_tools=known_tool_names))
        tools.append(WaitAgentTool(agent_control))
        tools.append(ListAgentsTool(agent_control))
        tools.append(InterruptAgentTool(agent_control))
        tools.append(SendMessageTool(agent_control))
    if goal_runtime is not None:
        tools.append(GetGoalTool(goal_runtime))
        tools.append(UpdateGoalTool(goal_runtime))
    for tool in tools:
        registry.register(tool)
    return registry
