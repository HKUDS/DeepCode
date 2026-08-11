"""spawn_agent / wait_agent — model-driven concurrent delegation (C2).

The model decides when to hand a concrete, self-contained subtask to a fresh
sub-agent. ``spawn_agent`` is NON-blocking: it starts the sub-agent in the
background and returns its id, so the model can spawn several that run
concurrently, keep working, then ``wait_agent`` to let their results arrive.
Results are delivered through the parent's mailbox (injected into the next turn).

Both tools share one :class:`~core.harness.agents.control.AgentControl`, created
per top-level session. A spawned sub-agent is built without these tools, so
delegation cannot recurse.
"""

from __future__ import annotations

from typing import Any

from core.agent_runtime.tools.base import Tool, tool_parameters
from core.harness.agents.control import AgentControl, AgentLimitError
from core.loop.guards import delegation_admission


def _parse_fork_turns(value: Any) -> str | int:
    """Normalize the fork_turns arg to 'none' | 'all' | positive int."""
    if value is None:
        return "none"
    text = str(value).strip().lower()
    if text in ("none", "all"):
        return text
    try:
        n = int(text)
    except ValueError:
        return "none"
    return n if n > 0 else "none"


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "A short stable label for this subtask (e.g. the "
                "target module name). Re-spawning the same name while it runs is "
                "refused, so name each subtask once and don't re-spawn it.",
            },
            "task": {
                "type": "string",
                "description": "A concrete, self-contained subtask for the "
                "sub-agent, with enough context to complete it on its own.",
            },
            "isolate": {
                "type": "boolean",
                "description": "Run the sub-agent in an isolated worktree and "
                "merge its result back with conflict detection (default true — "
                "parallel-safe). Set false to share the workspace directly.",
            },
            "fork_turns": {
                "type": "string",
                "description": "How much of your conversation the sub-agent "
                "inherits: 'none' (default, fresh), 'all', or a number N for the "
                "last N turns. Use it when the subtask needs your prior context.",
            },
            "backend": {
                "type": "string",
                "enum": ["native", "codex", "claude-code"],
                "description": "Which agent runs the subtask: 'native' "
                "(default, a DeepCode sub-agent), 'codex' (the Codex CLI), or "
                "'claude-code' (the Claude Code CLI) — external CLIs use their "
                "own models and policies. An external sub-agent gets ONLY the "
                "task text and the workspace — no conversation context, no "
                "send_message, and none of persona/tools/output_schema — so "
                "write it a fully self-contained task.",
            },
            "persona": {
                "type": "string",
                "description": "Optional extra system-prompt section shaping "
                "the sub-agent's role (e.g. 'You are a security reviewer; "
                "report findings, change nothing'). Native backend only.",
            },
            "tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional allowlist of tool names the "
                'sub-agent may use (e.g. ["read_file", "grep"] for a '
                "read-only reviewer). It can only narrow the standard set. "
                "Native backend only.",
            },
            "output_schema": {
                "type": "object",
                "description": "Optional object-rooted JSON Schema the "
                "sub-agent's result must satisfy. It is forced to submit a "
                "conforming object, and the RESULT you receive is that JSON — "
                "use this when you need structured data back, not prose. "
                "Native backend only.",
            },
        },
        "required": ["name", "task"],
    }
)
class SpawnAgentTool(Tool):
    """Start a sub-agent on a subtask in the background (non-blocking)."""

    def __init__(
        self,
        control: AgentControl,
        *,
        known_tools: tuple[str, ...] = (),
    ):
        self._control = control
        # The parent's tool vocabulary, used to fail a `tools` allowlist that
        # names nothing real. Verified live: an allowlist of ["read_file"]
        # (the tool is called "read") silently produced a tool-less child
        # that invented its answer instead of reading anything.
        self._known_tools = frozenset(known_tools)

    @property
    def name(self) -> str:
        return "spawn_agent"

    @property
    def description(self) -> str:
        return (
            "Delegate a concrete, self-contained subtask to a fresh sub-agent "
            "that runs in the BACKGROUND (this returns immediately with an id). "
            "Spawn several to run in parallel, keep doing your own critical-path "
            "work, then call wait_agent to receive their results. Do urgent or "
            "tightly-coupled work yourself. Sub-agents run isolated and merge "
            "back by default — their files are NOT visible until they finish, so "
            "spawn each subtask ONCE and use wait_agent/list_agents to check on "
            "it; do not re-spawn the same task."
        )

    async def execute(self, **kwargs: Any) -> Any:
        task = str(kwargs.get("task") or "").strip()
        if not task:
            return "Error: 'task' is required — describe a concrete, self-contained subtask."
        name = str(kwargs.get("name") or "").strip() or None
        isolate = bool(kwargs.get("isolate", True))
        fork_turns = _parse_fork_turns(kwargs.get("fork_turns"))
        backend = str(kwargs.get("backend") or "native").strip().lower()
        persona = kwargs.get("persona")
        tools = kwargs.get("tools")
        if tools is not None and not isinstance(tools, (list, tuple)):
            return "Error: 'tools' must be an array of tool names."
        if tools and self._known_tools:
            unknown = [str(t) for t in tools if str(t) not in self._known_tools]
            if unknown:
                return (
                    f"Error: unknown tool name(s) {', '.join(unknown)} in "
                    "'tools' — the allowlist would silently strip the "
                    "sub-agent of capabilities it needs. Available: "
                    f"{', '.join(sorted(self._known_tools))}."
                )
        output_schema = kwargs.get("output_schema")
        if output_schema is not None and not isinstance(output_schema, dict):
            return "Error: 'output_schema' must be a JSON object."
        # Delegation admission (REASONIX §1.10 delegationAdmission adaptation):
        # spawn_agent is a local concurrent delegation (C2); self-contained
        # tasks are allowed by default. Only tasks that reference the parent
        # conversation but would not inherit it are rejected (the sub-agent
        # cannot see your messages).
        decision, reason = delegation_admission(task, fork_turns=str(fork_turns))
        if decision == "deny":
            return (
                "Error: delegation denied (local_fix_no_external_need). "
                f"The subtask references the parent conversation but "
                f"fork_turns is '{fork_turns}', so the sub-agent inherits no "
                "context and cannot act on those references. Either do the "
                "work yourself, or rewrite the task to be self-contained, or "
                "pass fork_turns='all'/'<N>' to inherit the needed context. "
                f"(reason: {reason})"
            )
        try:
            agent_id = self._control.spawn(
                task,
                name=name,
                isolate=isolate,
                fork_turns=fork_turns,
                backend=backend,
                persona=str(persona) if persona is not None else None,
                tools=tools,
                output_schema=output_schema,
            )
        except AgentLimitError as exc:
            return f"Error: {exc}"
        return (
            f"Spawned {agent_id} (running in the background; its files are not "
            f"visible until it finishes). Continue other work, then call "
            f"wait_agent to receive its result. Do not re-spawn this same task."
        )


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "timeout_ms": {
                "type": "integer",
                "description": "Max time to wait for a sub-agent to post a "
                "result, in milliseconds (default 120000).",
            }
        },
        "required": [],
    }
)
class WaitAgentTool(Tool):
    """Wait until a spawned sub-agent posts a result (or timeout)."""

    _DEFAULT_TIMEOUT_MS = 120_000

    def __init__(self, control: AgentControl):
        self._control = control

    @property
    def name(self) -> str:
        return "wait_agent"

    @property
    def description(self) -> str:
        return (
            "Wait for a background sub-agent (spawned with spawn_agent) to post "
            "a result. Returns when one finishes or the timeout elapses; the "
            "actual results are delivered to you as messages on your next step."
        )

    @property
    def read_only(self) -> bool:
        # Just parks on the mailbox — no workspace / security side effects.
        return True

    async def execute(self, **kwargs: Any) -> Any:
        raw = kwargs.get("timeout_ms")
        try:
            timeout_ms = int(raw) if raw is not None else self._DEFAULT_TIMEOUT_MS
        except (TypeError, ValueError):
            timeout_ms = self._DEFAULT_TIMEOUT_MS
        timeout_ms = max(1_000, timeout_ms)
        return await self._control.wait_for_activity(timeout_ms / 1000.0)


@tool_parameters({"type": "object", "properties": {}, "required": []})
class ListAgentsTool(Tool):
    """List the sub-agents spawned this session and their status."""

    def __init__(self, control: AgentControl):
        self._control = control

    @property
    def name(self) -> str:
        return "list_agents"

    @property
    def description(self) -> str:
        return (
            "List the sub-agents you have spawned this session, with each one's "
            "status (running | done | failed) and its task."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> Any:
        agents = self._control.all()
        if not agents:
            return "No sub-agents have been spawned."
        # The backend is part of the row because it decides what the model may
        # do next: only native sub-agents accept send_message.
        return "\n".join(
            f"- {a.id}: {a.status}"
            + (f" [{a.backend}]" if a.backend != "native" else "")
            + f" — {a.task[:80]}"
            for a in agents
        )


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "description": "The id of the sub-agent to interrupt (from spawn_agent).",
            }
        },
        "required": ["agent"],
    }
)
class InterruptAgentTool(Tool):
    """Interrupt (cancel) a running sub-agent."""

    def __init__(self, control: AgentControl):
        self._control = control

    @property
    def name(self) -> str:
        return "interrupt_agent"

    @property
    def description(self) -> str:
        return (
            "Stop a running sub-agent's CURRENT work. A native sub-agent is "
            "not killed: it parks idle with its conversation intact, and "
            "send_message gives it a new direction. Use this to redirect a "
            "sub-agent heading the wrong way without losing its progress. "
            "External (codex/claude-code) sub-agents are cancelled outright."
        )

    async def execute(self, **kwargs: Any) -> Any:
        agent_id = str(kwargs.get("agent") or "").strip()
        if not agent_id:
            return "Error: 'agent' id is required."
        return self._control.interrupt(agent_id)


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "description": "The id of the running sub-agent (from spawn_agent).",
            },
            "message": {
                "type": "string",
                "description": "The message to deliver — extra context, a "
                "correction, or a follow-up instruction.",
            },
        },
        "required": ["agent", "message"],
    }
)
class SendMessageTool(Tool):
    """Send a message to a running sub-agent (delivered on its next step)."""

    def __init__(self, control: AgentControl):
        self._control = control

    @property
    def name(self) -> str:
        return "send_message"

    @property
    def description(self) -> str:
        return (
            "Send a message to a sub-agent you spawned. While it is RUNNING, "
            "the message is injected into its work at its next step. While it "
            "is IDLE (finished a turn, or interrupted), the message wakes it "
            "and becomes its next task — continuing the same conversation, so "
            "follow-up questions and course corrections both work."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> Any:
        agent_id = str(kwargs.get("agent") or "").strip()
        message = str(kwargs.get("message") or "")
        if not agent_id:
            return "Error: 'agent' id is required."
        return self._control.send_message(agent_id, message)
