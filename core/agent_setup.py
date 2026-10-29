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

import inspect
from collections.abc import Awaitable, Callable
import os
from typing import Any

from loguru import logger

from core.compat import DeepCodeRuntime, get_runtime
from core.domain.execution_profile import ExecutionProfile
from core.domain.execution_security import ExecutionSecurityProfile
from core.events import AgentSession
from core.harness.permissions import PermissionMode
from core.harness.policy import (
    build_permission_engine,
    describe_security_posture,
    resolve_execution_security_profile,
)
from core.harness.sandbox import sandbox_backend
from core.harness.tools import default_coding_tools
from core.llm_runtime import get_workflow_provider
from core.providers.catalog import resolve_model_info

# The base instructions every DeepCode agent gets. The working style and the
# permission posture follow the session's mode and are appended by
# core.harness.collaboration; the memory guidance by core.harness.memory.
SYSTEM_PROMPT = """\
You are DeepCode, a coding agent working in the user's workspace directory. You work alongside the user like a careful senior engineer: you do the work, keep them informed, and tell them plainly what happened. Use the tools provided for this turn. Core tools include read, write, edit, apply_patch, bash, grep, glob, web_fetch, and update_plan; optional capabilities such as Skills, delegation, or goals appear only when enabled.

Instructions from the user in this conversation come first. Project instruction files (AGENTS.md, DEEPCODE.md, CLAUDE.md) come next, and a file in a deeper directory wins for the files under it. These defaults apply where neither says otherwise.

## Tone
- Be direct, calm, and friendly, like a teammate giving a quick update. Use plain words; avoid jargon you invented while working.
- No filler: skip praise ("Great question"), apologies, and restating the request. Do not hedge everything; say what you know, and say clearly what you do not.
- Be honest about results. Report failures, skipped steps, and uncertainty as plainly as successes.
- Reply in the language the user wrote in.

## Using tools
- Navigate with grep and glob, inspect with read, change files with edit (or write for new files), and use apply_patch when one change spans several files or must land all-or-nothing. Run commands and tests with bash.
- Do not edit files through bash (`sed -i`, inline Python, heredoc scripts): those changes skip diagnostics and the user cannot review them as diffs. Do not read files with `cat` or `sed -n` either; use read, which handles large files.
- Read large files in parts, around the lines you need, rather than all at once.
- Keep command output small: filter with `tail`, `head`, or `grep` instead of printing whole files or logs.
- Independent reads and searches can run in parallel.

## Keep the user informed
The user sees your plain-text messages and the tool calls, but not your reasoning. Write to them as you work:
- Before a group of related tool calls, write one short sentence (about 8 to 15 words) saying what you are about to do and why. Build on what you just found. Group related calls under one sentence, and skip it for a single trivial read.
- Say so when you find something important, hit a blocker, or change direction.
- On a long task, give a one-line progress update every few steps rather than working silently.

Good:
- "The config loads fine; now checking why the tests time out."
- "Found it: the save file is written before the score updates. Fixing the order."
- "Three of five criteria pass. Next: the inventory screen."

Bad:
- Running twenty commands with no message at all.
- "Let me now proceed to carefully analyze the codebase in detail." (says nothing)
- A separate message before every single file read.

## Planning
Use update_plan for work with several distinct steps, or when the order matters. Skip it for a quick, single-step task. Keep one step in_progress at a time, mark steps done as you finish them, and revise the plan when you learn something that changes it.

Good steps are concrete and checkable:
1. Reproduce the crash when saving with an empty inventory
2. Guard the save path against a missing inventory
3. Add a test for saving with an empty inventory
4. Run the save tests and the smoke run

Bad steps are vague: "Understand the code", "Implement the feature", "Test it".

## Making changes
- Fix the root cause rather than the symptom. Keep changes minimal, focused on the task, and in the existing style of the codebase. Avoid needless complexity.
- Do not fix unrelated bugs or failing tests; mention them in your final message instead.
- The workspace may hold the user's own uncommitted work. Never revert, overwrite, or discard changes you did not make. If they get in the way of your task, stop and ask.
- Never run destructive commands such as `git reset --hard`, `git checkout -- .`, `git clean -fd`, `git push --force`, or `rm -rf` on project paths unless the user explicitly asks. Do not commit, create branches, or push unless asked.
- After a write, edit, or apply_patch, check the tool result for a 'Diagnostics detected' block and fix any reported errors. Do not re-read a file only to confirm that an edit landed; a failed edit reports an error.
- Do not add comments, license headers, or dependencies that the task does not need.

## Validating your work
- After changing code, check that it works. Start with the tests or commands closest to what you changed, then widen as you gain confidence. If there are no tests, run the code or a quick check that exercises the change.
- Never claim something works unless you ran it. If you could not verify a change, say so and say why.
- Do not add a test framework or formatter that the project does not already use.

## Debugging
- Reproduce a failure before you fix it, and test one hypothesis at a time.
- If the same approach fails twice, stop and rethink: read more of the code, try a different approach, or tell the user where you are stuck. Do not keep re-running the same check with small tweaks.

## Long tasks
For a task that spans many steps or turns, such as a Goal:
- Start by turning the request into acceptance criteria you can check. If the request is open-ended ("until it is perfect"), propose concrete criteria, tell the user, and work toward them. For example, "make the game complete" might become: all tests pass; the smoke run reports no failures; a new player can finish level 1; saving and loading keep progress.
- Keep a short progress file at `.deepcode/progress.md`: the criteria, what is done, what is next, and approaches that did not work. Update it at each milestone.
- When you resume, read the progress file and `git status` / `git diff` first, so you continue from the actual state instead of exploring again.
- At each milestone, tell the user in a line or two what is done, what is next, and whether anything is blocked.
- Report the task complete only when every criterion has evidence from a command you ran.

## Final message
When the task is done, end with a message the user can read in under a minute, even if they were not watching:
- Lead with the outcome in one or two sentences: what you did or found, and whether it works.
- Then only what matters: the files changed, how you verified the change, and anything left undone, failing, or assumed. Put the most important item first.
- Size it to the task: a sentence or two for a small change, a short list for a larger one. Do not list every step you took.

Formatting:
- Use short bullets for separate points and a small table for comparisons. Do not nest bullets.
- Add a short bold heading only when a long answer has distinct parts.
- Put file paths, commands, and identifiers in backticks. Reference code as `path:line` so the user can click it.
- Do not paste large blocks of code or tool output; point to the file instead. Do not repeat what the user can already see in the tool output.

Good:
"Saving with an empty inventory no longer crashes: the save path now treats a missing inventory as empty (`src/save.js:42`). Added a test for it; the save tests and the smoke run pass. One unrelated test, `test/audio.test.js`, was already failing before this change."

Bad:
"I have completed the task. First I looked at the files. Then I opened save.js. Then I made some changes. The changes should work now. Let me know if you need anything else!"
"""


# Tools callable from inside code mode (C5b): file / shell / search — the ones
# worth batching in a loop. Meta tools (plan, delegation, hooks, memory) are
# intentionally not exposed to the code.
_CODE_MODE_TOOLS = frozenset(
    {"read", "write", "edit", "apply_patch", "bash", "grep", "glob"}
)


def _wire_code_mode(
    tool_registry: Any,
    workspace: str,
    engine: Any,
    hooks_engine: Any,
    execution_security_profile: ExecutionSecurityProfile | None = None,
    approval_callback: Any | None = None,
) -> None:
    """Register the ``code`` tool (C5b): a Python program that orchestrates the
    file/shell/search tools in one turn. Each tool call the code makes is run in
    the parent and governed exactly like a normal tool call — PreToolUse hook →
    permission → execute → PostToolUse hook — so code mode never bypasses policy.
    An ``ask`` decision is resolved by the same frontend approval callback as a
    direct tool call; missing, rejected, or failed approval is fail-closed.
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
        decision_value = getattr(decision, "value", decision)
        if decision_value == "deny":
            return f"Error: permission denied: {reason}"
        if decision_value == "ask":
            if approval_callback is None:
                return (
                    f"Error: permission denied: {reason} — no approver attached "
                    "(non-interactive run)"
                )
            try:
                approved = approval_callback(tool_name, args, reason)
                if inspect.isawaitable(approved):
                    approved = await approved
            except Exception:  # noqa: BLE001 - host callback boundary
                logger.exception("code-mode approval_callback failed for {}", tool_name)
                return (
                    "Error: permission denied: approval request errored (fail-closed)"
                )
            if not approved:
                return f"Error: permission denied: user rejected: {reason}"
        elif decision_value != "allow":
            return "Error: permission denied: unknown permission decision (fail-closed)"
        result = await tool_registry.execute(tool_name, args)
        if hooks_engine is not None and hooks_engine.has_event("PostToolUse"):
            post = await hooks_engine.run_post_tool_use(tool_name, args, result)
            if post.additional_contexts:
                joined = "\n".join(f"[hook] {c}" for c in post.additional_contexts)
                result = f"{result}\n\n{joined}"
        return result

    tool_registry.register(
        CodeModeTool(
            workspace,
            _execute,
            api,
            sandbox_enabled=(
                execution_security_profile.command_sandbox
                if execution_security_profile is not None
                else None
            ),
        )
    )


def _compose_tool_filters(*filters: Any) -> Any:
    """Chain narrowing filters; each sees the previous one's survivors.

    ``None`` entries are skipped, and zero or one live filter avoids the
    wrapper entirely. Filters follow the AgentRunSpec contract: a callable
    over the visible name tuple whose ``None`` return means "unchanged".
    """
    live = [f for f in filters if f is not None]
    if not live:
        return None
    if len(live) == 1:
        return live[0]

    def chained(names: tuple[str, ...]) -> tuple[str, ...]:
        for tool_filter in live:
            value = tool_filter(names)
            if value is not None:
                names = tuple(str(name) for name in value)
        return names

    return chained


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
    max_iterations: int | None = None,
    system_prompt: str = SYSTEM_PROMPT,
    approval_callback: Any | None = None,
    ask_user_callback: Any | None = None,
    allow_spawn: bool = True,
    injection_callback: Any | None = None,
    context_note_sink: Any | None = None,
    history_sink: Any | None = None,
    subagent_transcript_sink: Any | None = None,
    active_turn_id_provider: Any | None = None,
    runtime_input_sink: Any | None = None,
    goal_runtime: Any | None = None,
    agent_context: tuple[str, str] | None = None,
    streaming: bool = False,
    streaming_transport: bool = True,
    default_permission_mode: PermissionMode = PermissionMode.FULL_AUTO,
    permission_mode_override: PermissionMode | None = None,
    execution_security_profile: ExecutionSecurityProfile | None = None,
    runtime: DeepCodeRuntime | None = None,
    provider_cleanup: Callable[[], Awaitable[None]] | None = None,
    skill_runtime: Any | None = None,
    project_trusted: bool = False,
    plugin_mcp_servers: tuple[Any, ...] = (),
    mcp_status_observer: Any | None = None,
    # Caller-supplied tools registered after the standard set — e.g. a
    # sub-agent's structured-result capture tool. Not a replacement channel:
    # a duplicate name would shadow in the registry, so callers own novelty.
    extra_tools: tuple[Any, ...] = (),
    # Optional narrowing filter over exposed tool names, composed with the
    # Goal runtime's own filter (both only ever narrow; see AgentRunSpec).
    tool_filter: Any | None = None,
) -> tuple[AgentSession, str, Any]:
    """Build an :class:`AgentSession` over ``workspace``.

    Returns ``(session, resolved_model, permission_engine)``. The workspace is
    created if missing; Ask and Read only stay workspace-scoped while an
    explicit Full Access profile is unrestricted. Pass
    ``ask_user_callback`` from an interactive frontend to give the agent the
    ``request_user_input`` tool; headless callers omit it. ``allow_spawn`` gives
    the agent the ``spawn_agent`` delegation tool (a spawned sub-agent is built
    with it False so delegation cannot recurse). ``default_permission_mode`` is
    a legacy client default only; environment and explicit config still win
    unless an immutable ``execution_security_profile`` was captured for this
    Turn.
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
    resolved_security_profile = resolve_execution_security_profile(
        security_cfg,
        default_mode=default_permission_mode,
        mode_override=permission_mode_override,
        profile_override=execution_security_profile,
    )
    engine = build_permission_engine(
        security_cfg,
        cwd=workspace,
        default_mode=default_permission_mode,
        mode_override=permission_mode_override,
        execution_security_profile=resolved_security_profile,
    )

    # Record what is actually enforcing for this session. The four knobs
    # interact, and an unattended `full_auto` run looks identical to an
    # approval-gated one in the transcript — which is precisely the fact a
    # reader needs when asking whether a rewritten tool call could have run.
    security_posture = describe_security_posture(
        resolved_security_profile,
        sandbox_backend=sandbox_backend(),
    )
    logger.info("security posture: {}", security_posture)

    # Stable system context is assembled once here. Skills are intentionally
    # not flattened into this prompt: AgentSession resolves a fresh immutable
    # Skill snapshot at each turn, giving every frontend hot reload without
    # allowing files to change halfway through one model execution.
    from core.agent_runtime.injections import compose_injection_callbacks
    from core.harness.collaboration import collaboration_preamble
    from core.harness.memory import system_preamble
    from core.skills.runtime import SkillRuntime

    if skill_runtime is None:
        skill_runtime = SkillRuntime(workspace)
    addenda = [
        collaboration_preamble(
            engine.mode, unattended=bool(security_posture["unattended"])
        ),
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
            execution_security_profile=resolved_security_profile,
            approval_callback=approval_callback,
            runtime=active_runtime,
            active_turn_id_provider=active_turn_id_provider,
            runtime_input_sink=runtime_input_sink,
            context_note_sink=context_note_sink,
            transcript_sink=subagent_transcript_sink,
            project_trusted=project_trusted,
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
        execution_security_profile=resolved_security_profile,
    )
    for extra_tool in extra_tools:
        tool_registry.register(extra_tool)
    _wire_code_mode(
        tool_registry,
        workspace,
        engine,
        hooks_engine,
        resolved_security_profile,
        approval_callback,
    )
    _wire_tool_permissions(tool_registry, engine)

    from core.mcp import (
        McpConfigResolver,
        McpSessionRuntime,
        create_mcp_oauth_provider,
    )
    from core.mcp.tools import McpToolAdapter

    mcp_plan = McpConfigResolver().resolve(
        workspace,
        project_trusted=project_trusted,
        plugin_servers=plugin_mcp_servers,
    )

    def _mcp_credential(connection_id: str) -> str | None:
        try:
            return active_runtime.connection_resolver.resolve_connection(
                connection_id
            ).api_key
        except (AttributeError, ValueError):
            return None

    mcp_runtime = McpSessionRuntime(
        mcp_plan,
        tool_registry,
        credential_resolver=_mcp_credential,
        oauth_provider_factory=create_mcp_oauth_provider,
        status_observer=(
            (lambda statuses: mcp_status_observer(workspace, statuses))
            if mcp_status_observer is not None
            else None
        ),
    )

    def _permission_checker(name: str, arguments: dict[str, Any]):
        tool = tool_registry.get(name)
        if isinstance(tool, McpToolAdapter):
            return engine.evaluate_tool(
                name,
                arguments,
                read_only=tool.read_only,
                approval_mode=tool.approval_mode,
            )
        return engine.evaluate(name, arguments)

    # User-facing microcompact switch (agents.defaults.microcompact): arms the
    # model-free stale-tool-result clear in the runner's pressure gate. Read
    # defensively so a config without the field still constructs the session.
    agents_cfg = getattr(getattr(active_runtime, "config", None), "agents", None)
    microcompact_enabled = bool(
        getattr(getattr(agents_cfg, "defaults", None), "microcompact", False)
    )

    session = AgentSession(
        provider,
        tool_registry,
        model=resolved_model,
        system_prompt=full_system_prompt,
        max_iterations=max_iterations,
        permission_checker=_permission_checker,
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
        context_note_sink=context_note_sink,
        history_sink=history_sink,
        hooks_engine=hooks_engine,
        agent_context=agent_context,
        streaming=streaming,
        streaming_transport=streaming_transport,
        skill_runtime=skill_runtime,
        context_window_tokens=resolved_execution.context_window,
        microcompact=microcompact_enabled,
        workspace=workspace,
        execution_profile=resolved_execution,
        tool_filter=_compose_tool_filters(
            tool_filter,
            goal_runtime.visible_tool_names if goal_runtime is not None else None,
        ),
        closure_callback=(
            goal_runtime.closure_prompt if goal_runtime is not None else None
        ),
        mcp_runtime=mcp_runtime,
        provider_cleanup=provider_cleanup,
    )
    if goal_runtime is not None:
        # The Goal router outlives agent rebuilds, so every build sets the
        # completion gate from the current config: ``off`` disarms it.
        goal_cfg = getattr(active_runtime.config, "goal", None)
        if goal_cfg is not None and goal_cfg.completion_verifier == "rule":
            from core.agent_runtime.goal_verifier import RuleBasedVerifier

            goal_runtime.configure_completion_verifier(
                RuleBasedVerifier(),
                max_rejections=goal_cfg.max_rejections,
                settle=session.settle_events,
            )
        else:
            goal_runtime.configure_completion_verifier(None)
    if control is not None:
        # `session.history` is a @property (a list), so it must be wrapped in a
        # callable — passing its value would make fork_turns call a list.
        control.set_history_provider(lambda: session.history)
        session._agent_control = control  # for fork_turns wiring + teardown
    return session, resolved_model, engine
