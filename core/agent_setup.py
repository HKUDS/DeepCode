"""Shared assembly of a coding :class:`AgentSession` for every frontend.

The TUI (``cli.tui``), the headless entry (``cli.exec_cli``), and the web
backend (``agent_chat_service``) all need the same wiring: resolve a
provider, build the native tool set, attach the P1 permission engine, and
construct an :class:`AgentSession` armed with the model catalog's context
window. One assembly function keeps them in lockstep.

Lives in ``core`` (not ``cli``) deliberately: it is frontend-neutral, and
the web backend must not import through the top-level ``cli`` package —
that name is generic enough to be shadowed by third-party ``cli``
distributions on some interpreters (observed in the conda env), which is
exactly the module-shadowing failure mode the backend's path setup warns
about.
"""

from __future__ import annotations

import os
from typing import Any

from core.compat import get_runtime
from core.events import AgentSession
from core.harness.policy import build_permission_engine
from core.harness.tools import default_coding_tools
from core.llm_runtime import get_workflow_provider

# A general coding task can legitimately need many tool-call turns. This is a
# runaway backstop, not a task budget — the reference agents run effectively
# unbounded, relying on the model to stop and on context compaction to stay in
# window; DeepCode's per-turn history snipping already prevents overflow, so we
# set a high ceiling instead of cutting real work off early.
DEFAULT_MAX_ITERATIONS = 200

SYSTEM_PROMPT = (
    "You are a coding agent working in a workspace directory. You have tools: "
    "read, write, edit, apply_patch, bash, grep, glob, update_plan. Navigate "
    "with grep/glob, inspect with read, make targeted changes with edit (or "
    "write for new files), and use apply_patch when one change spans several "
    "files or must land all-or-nothing. Run commands/tests with bash. For a "
    "multi-step task, use update_plan to lay out and track your steps (one "
    "in_progress at a time), and keep it current as you go. After a write, "
    "edit, or apply_patch, check the tool result for a 'Diagnostics detected' "
    "block and fix any reported errors. When the task is done, reply with a "
    "short summary."
)


def build_agent_session(
    *,
    workspace: str,
    model: str | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    system_prompt: str = SYSTEM_PROMPT,
    approval_callback: Any | None = None,
    ask_user_callback: Any | None = None,
    allow_spawn: bool = True,
    injection_callback: Any | None = None,
    streaming: bool = False,
) -> tuple[AgentSession, str, Any]:
    """Build an :class:`AgentSession` over ``workspace``.

    Returns ``(session, resolved_model, permission_engine)``. The workspace is
    created if missing; the permission engine is fenced to it. Pass
    ``ask_user_callback`` from an interactive frontend to give the agent the
    ``request_user_input`` tool; headless callers omit it. ``allow_spawn`` gives
    the agent the ``spawn_agent`` delegation tool (a spawned sub-agent is built
    with it False so delegation cannot recurse).
    """
    workspace = os.path.abspath(workspace)
    os.makedirs(workspace, exist_ok=True)

    provider, profile = get_workflow_provider(phase="implementation", model=model)
    resolved_model = model or profile.model

    security_cfg = getattr(get_runtime().config, "security", None)
    engine = build_permission_engine(security_cfg, cwd=workspace)

    # The system prompt is assembled once, here, so every frontend gets the
    # same behavior: working-style (collaboration mode, keyed off the resolved
    # permission mode) + memory (AGENTS.md + persistent index) + skills (SKILL.md
    # playbooks). Skills are discovered once and shared by the preamble and the
    # `skill` tool so the two never drift.
    from core.harness.collaboration import collaboration_preamble
    from core.harness.memory import system_preamble
    from core.harness.skills import discover_skills, skills_preamble

    skills = discover_skills(workspace)
    addenda = [
        collaboration_preamble(engine.mode),
        system_preamble(workspace),
        skills_preamble(skills),
    ]
    addendum = "\n\n".join(a for a in addenda if a)
    full_system_prompt = f"{system_prompt}\n\n{addendum}" if addendum else system_prompt

    # Delegation: one AgentControl per top-level session drives spawn_agent /
    # wait_agent and feeds sub-agent results back through the injection seam.
    # A spawned sub-agent is built with allow_spawn=False (no control), capping
    # delegation depth at one.
    control = None
    if allow_spawn:
        from core.harness.agents import AgentControl

        control = AgentControl(workspace, resolved_model)

    session = AgentSession(
        provider,
        default_coding_tools(
            workspace,
            skills=skills,
            ask_user=ask_user_callback,
            agent_control=control,
        ),
        model=resolved_model,
        system_prompt=full_system_prompt,
        max_iterations=max_iterations,
        permission_checker=engine.evaluate,
        approval_callback=approval_callback,
        # Top-level session: results from its own sub-agents. Sub-agent session
        # (no control of its own): an explicit inbox drainer for send_message.
        injection_callback=control.drain_injections if control else injection_callback,
        streaming=streaming,
    )
    if control is not None:
        # `session.history` is a @property (a list), so it must be wrapped in a
        # callable — passing its value would make fork_turns call a list.
        control.set_history_provider(lambda: session.history)
        session._agent_control = control  # for fork_turns wiring + teardown
    return session, resolved_model, engine
