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
        },
        "required": ["name", "task"],
    }
)
class SpawnAgentTool(Tool):
    """Start a sub-agent on a subtask in the background (non-blocking)."""

    def __init__(self, control: AgentControl):
        self._control = control

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
        try:
            agent_id = self._control.spawn(
                task, name=name, isolate=isolate, fork_turns=fork_turns
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
        return "\n".join(f"- {a.id}: {a.status} — {a.task[:80]}" for a in agents)


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
            "Cancel a running sub-agent you spawned (e.g. it is no longer needed "
            "or is stuck). Its cancelled result is delivered like any other."
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
            "Send a message to a sub-agent you spawned while it is still "
            "running — extra context, a correction, or a follow-up. It is "
            "injected into that sub-agent's work at its next step. Only reaches "
            "a sub-agent that is still running."
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
