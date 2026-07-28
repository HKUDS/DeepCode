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

from loguru import logger

from core.compat import DeepCodeRuntime, get_runtime
from core.domain.execution_profile import ExecutionProfile
from core.events import AgentSession
from core.harness.permissions import PermissionMode
from core.harness.policy import build_permission_engine
from core.harness.tools import default_coding_tools
from core.llm_runtime import get_workflow_provider
from core.providers.catalog import resolve_model_info

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


# Tools callable from inside code mode (C5b): file / shell / search — the ones
# worth batching in a loop. Meta tools (plan, delegation, hooks, memory) are
# intentionally not exposed to the code.
_CODE_MODE_TOOLS = frozenset(
    {"read", "write", "edit", "apply_patch", "bash", "grep", "glob"}
)


def _wire_code_mode(
    tool_registry: Any, workspace: str, engine: Any, hooks_engine: Any
) -> None:
    """Register the ``code`` tool (C5b): a Python program that orchestrates the
    file/shell/search tools in one turn. Each tool call the code makes is run in
    the parent and governed exactly like a normal tool call — PreToolUse hook →
    permission → execute → PostToolUse hook — so code mode never bypasses policy.
    An ``ask`` decision has no human in the code loop, so it fails closed (deny).
    """
    from core.harness.code_mode import CodeModeTool, api_from_definitions

    api = api_from_definitions(tool_registry.get_definitions(), _CODE_MODE_TOOLS)
    if not api:
        return

    async def _execute(tool_name: str, arguments: dict[str, Any]) -> str:
        args = dict(arguments)
        if hooks_engine is not None and hooks_engine.has_event("PreToolUse"):
            pre = await hooks_engine.run_pre_tool_use(tool_name, args)
            if pre.block:
                return f"Error: blocked by PreToolUse hook: {pre.block_reason}"
            if pre.updated_input:
                args = pre.updated_input
        decision, reason = engine.evaluate(tool_name, args)
        if getattr(decision, "value", decision) != "allow":
            return f"Error: permission denied: {reason}"
        result = await tool_registry.execute(tool_name, args)
        if hooks_engine is not None and hooks_engine.has_event("PostToolUse"):
            post = await hooks_engine.run_post_tool_use(tool_name, args, result)
            if post.additional_contexts:
                joined = "\n".join(f"[hook] {c}" for c in post.additional_contexts)
                result = f"{result}\n\n{joined}"
        return result

    tool_registry.register(CodeModeTool(workspace, _execute, api))


def _wire_tool_permissions(tool_registry: Any, engine: Any) -> None:
    """Make tool-declared read-only metadata authoritative for this session."""
    engine.read_only_tools = engine.read_only_tools.union(
        tool_registry.read_only_tool_names
    )


def _legacy_execution_profile(
    *,
    runtime: Any,
    provider: Any,
    profile: Any,
    connection_id: str | None,
    requested_model: str | None,
) -> ExecutionProfile:
    """Adapt an old runtime/profile pair to the immutable execution contract.

    ``build_agent_session`` has long been a public integration seam. A number
    of embedders and offline tests provide a deliberately tiny runtime object
    and replace ``get_workflow_provider``. Requiring those callers to grow the
    new resolver API would break the stable CLI assembly contract, so this
    adapter derives a complete, secret-free snapshot from the already-resolved
    provider without changing how that provider was selected.
    """

    resolved_model = str(
        requested_model
        or getattr(profile, "model", None)
        or provider.get_default_model()
    )
    model_info = resolve_model_info(resolved_model)
    runtime_config = getattr(runtime, "config", None)

    provider_name = getattr(profile, "provider_name", None)
    if not provider_name and runtime_config is not None:
        get_provider_name = getattr(runtime_config, "get_provider_name", None)
        if callable(get_provider_name):
            provider_name = get_provider_name(resolved_model)
        provider_name = provider_name or getattr(runtime_config, "llm_provider", None)
    provider_name = str(provider_name or "legacy")

    generation = getattr(provider, "generation", None)
    max_tokens = int(
        getattr(profile, "max_tokens", None)
        or getattr(generation, "max_tokens", None)
        or model_info.max_output_tokens
    )
    context_window = int(
        getattr(profile, "context_window", None) or model_info.context_window
    )
    max_output_tokens = max(max_tokens, int(model_info.max_output_tokens))

    temperature = getattr(profile, "temperature", None)
    if temperature is None:
        temperature = getattr(generation, "temperature", None)
    if temperature is None:
        temperature = 0.7

    return ExecutionProfile(
        connection_id=str(
            getattr(profile, "connection_id", None) or connection_id or provider_name
        ),
        provider_name=provider_name,
        adapter=provider_name,
        model_id=resolved_model,
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        max_tokens=min(max_tokens, max_output_tokens),
        temperature=float(temperature),
        reasoning_effort=(
            getattr(profile, "reasoning_effort", None)
            or getattr(generation, "reasoning_effort", None)
        ),
        config_revision="legacy-runtime",
    )


def build_agent_session(
    *,
    workspace: str,
    model: str | None = None,
    connection_id: str | None = None,
    reasoning_effort: str | None = None,
    execution_profile: ExecutionProfile | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    system_prompt: str = SYSTEM_PROMPT,
    approval_callback: Any | None = None,
    ask_user_callback: Any | None = None,
    allow_spawn: bool = True,
    injection_callback: Any | None = None,
    active_turn_id_provider: Any | None = None,
    runtime_input_sink: Any | None = None,
    goal_runtime: Any | None = None,
    agent_context: tuple[str, str] | None = None,
    streaming: bool = False,
    streaming_transport: bool = True,
    default_permission_mode: PermissionMode = PermissionMode.FULL_AUTO,
    permission_mode_override: PermissionMode | None = None,
    runtime: DeepCodeRuntime | None = None,
) -> tuple[AgentSession, str, Any]:
    """Build an :class:`AgentSession` over ``workspace``.

    Returns ``(session, resolved_model, permission_engine)``. The workspace is
    created if missing; the permission engine is fenced to it. Pass
    ``ask_user_callback`` from an interactive frontend to give the agent the
    ``request_user_input`` tool; headless callers omit it. ``allow_spawn`` gives
    the agent the ``spawn_agent`` delegation tool (a spawned sub-agent is built
    with it False so delegation cannot recurse). ``default_permission_mode`` is
    a client default only; environment and explicit config still win.
    """
    workspace = os.path.abspath(workspace)
    os.makedirs(workspace, exist_ok=True)

    active_runtime = runtime or get_runtime()
    resolver = getattr(active_runtime, "resolve_execution_profile", None)
    if execution_profile is not None:
        resolved_execution = execution_profile
        provider, profile = get_workflow_provider(
            phase="implementation",
            connection_id=resolved_execution.connection_id,
            model=resolved_execution.model_id,
            execution_profile=resolved_execution,
            runtime=active_runtime,
        )
    elif callable(resolver):
        resolved_execution = resolver(
            connection_id=connection_id,
            model=model,
            reasoning_effort=reasoning_effort,
            phase="implementation",
        )
        provider, profile = get_workflow_provider(
            phase="implementation",
            connection_id=resolved_execution.connection_id,
            model=resolved_execution.model_id,
            execution_profile=resolved_execution,
            runtime=active_runtime,
        )
    else:
        provider, profile = get_workflow_provider(
            phase="implementation",
            connection_id=connection_id,
            model=model,
            runtime=active_runtime,
        )
        resolved_execution = _legacy_execution_profile(
            runtime=active_runtime,
            provider=provider,
            profile=profile,
            connection_id=connection_id,
            requested_model=model,
        )
    resolved_model = resolved_execution.model_id

    security_cfg = getattr(active_runtime.config, "security", None)
    engine = build_permission_engine(
        security_cfg,
        cwd=workspace,
        default_mode=default_permission_mode,
        mode_override=permission_mode_override,
    )

    # Stable system context is assembled once here. Skills are intentionally
    # not flattened into this prompt: AgentSession resolves a fresh immutable
    # Skill snapshot at each turn, giving every frontend hot reload without
    # allowing files to change halfway through one model execution.
    from core.agent_runtime.injections import compose_injection_callbacks
    from core.harness.collaboration import collaboration_preamble
    from core.harness.memory import system_preamble
    from core.skills.runtime import SkillRuntime

    skill_runtime = SkillRuntime(workspace)
    addenda = [
        collaboration_preamble(engine.mode),
        system_preamble(workspace),
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

        control = AgentControl(
            workspace,
            resolved_model,
            execution_profile=resolved_execution,
            permission_mode=engine.mode,
            approval_callback=approval_callback,
            runtime=active_runtime,
            active_turn_id_provider=active_turn_id_provider,
            runtime_input_sink=runtime_input_sink,
        )

    # External-command hooks (C3): discover Claude-Code-compatible hooks.json in
    # the workspace. None when no hooks are configured (the common case), so the
    # feature stays dormant at zero cost. Warnings surface misconfigured hooks.
    import uuid

    from core.harness.hooks import HooksEngine

    hooks_engine, hook_warnings = HooksEngine.discover(
        workspace,
        session_id=uuid.uuid4().hex,
        model=resolved_model,
        permission_mode=getattr(engine.mode, "value", str(engine.mode)),
    )
    for warning in hook_warnings:
        logger.warning("hooks config: {}", warning)

    tool_registry = default_coding_tools(
        workspace,
        skill_runtime=skill_runtime,
        ask_user=ask_user_callback,
        agent_control=control,
        goal_runtime=goal_runtime,
    )
    _wire_code_mode(tool_registry, workspace, engine, hooks_engine)
    _wire_tool_permissions(tool_registry, engine)

    session = AgentSession(
        provider,
        tool_registry,
        model=resolved_model,
        system_prompt=full_system_prompt,
        max_iterations=max_iterations,
        permission_checker=engine.evaluate,
        approval_callback=approval_callback,
        # Application-backed sessions send sub-agent results directly into the
        # same atomic Turn mailbox as user steering. Direct CLI sessions retain
        # the local drain adapter because they do not own application Turn IDs.
        injection_callback=compose_injection_callbacks(
            injection_callback,
            (
                control.drain_injections
                if control is not None and runtime_input_sink is None
                else None
            ),
        ),
        hooks_engine=hooks_engine,
        agent_context=agent_context,
        streaming=streaming,
        streaming_transport=streaming_transport,
        skill_runtime=skill_runtime,
        context_window_tokens=resolved_execution.context_window,
        execution_profile=resolved_execution,
        tool_filter=(
            goal_runtime.visible_tool_names if goal_runtime is not None else None
        ),
    )
    if control is not None:
        # `session.history` is a @property (a list), so it must be wrapped in a
        # callable — passing its value would make fork_turns call a list.
        control.set_history_provider(lambda: session.history)
        session._agent_control = control  # for fork_turns wiring + teardown
    return session, resolved_model, engine
