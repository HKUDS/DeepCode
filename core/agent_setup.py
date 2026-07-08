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

SYSTEM_PROMPT = (
    "You are a coding agent working in a workspace directory. You have tools: "
    "read, write, edit, apply_patch, bash, grep, glob. Navigate with grep/glob, "
    "inspect with read, make targeted changes with edit (or write for new "
    "files), and use apply_patch when one change spans several files or must "
    "land all-or-nothing. Run commands/tests with bash. After a write, edit, or "
    "apply_patch, check the tool result for a 'Diagnostics detected' block and "
    "fix any reported errors. When the task is done, reply with a short summary."
)


def build_agent_session(
    *,
    workspace: str,
    model: str | None = None,
    max_iterations: int = 40,
    system_prompt: str = SYSTEM_PROMPT,
    approval_callback: Any | None = None,
    streaming: bool = False,
) -> tuple[AgentSession, str, Any]:
    """Build an :class:`AgentSession` over ``workspace``.

    Returns ``(session, resolved_model, permission_engine)``. The workspace is
    created if missing; the permission engine is fenced to it.
    """
    workspace = os.path.abspath(workspace)
    os.makedirs(workspace, exist_ok=True)

    provider, profile = get_workflow_provider(phase="implementation", model=model)
    resolved_model = model or profile.model

    security_cfg = getattr(get_runtime().config, "security", None)
    engine = build_permission_engine(security_cfg, cwd=workspace)

    session = AgentSession(
        provider,
        default_coding_tools(workspace),
        model=resolved_model,
        system_prompt=system_prompt,
        max_iterations=max_iterations,
        permission_checker=engine.evaluate,
        approval_callback=approval_callback,
        streaming=streaming,
    )
    return session, resolved_model, engine
