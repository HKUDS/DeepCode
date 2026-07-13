"""AgentControl — the coordination state for model-driven delegation (C2).

Faithful to the reference agent's multi-agent design, adapted to DeepCode:

- **spawn is non-blocking** — it starts a sub-agent as a background task and
  returns its id immediately; several run *concurrently*, bounded by a per-session
  limit (``max_threads``);
- **results flow through a mailbox** — when a sub-agent finishes it posts a
  result message to the parent's mailbox and fires an activity event; the parent
  drains the mailbox into its next turn via :meth:`drain_injections` (wired as
  the run's ``injection_callback``);
- **wait_agent parks on mailbox activity** (:meth:`wait_for_activity`) rather
  than joining a future — so the parent can keep working and collect results as
  they arrive.

Isolation is DeepCode's own guarantee: each sub-agent may run in its own git
worktree whose result is merged back with 3-way-merge conflict detection; base
git ops are serialised (``_git_lock``) while the sub-agents build in parallel.

Depth is capped at one: a sub-agent session is built with ``allow_spawn=False``
so it gets no delegation tools and cannot recurse.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

# Max sub-agents running at once. A small fan-out (3-5 independent subtasks) is
# the common case, so the default fits it without forcing a wait-and-retry,
# while still bounding concurrent model sessions.
MAX_CONCURRENT_SUBAGENTS = 5


def _slug(name: str) -> str:
    """A stable id/dedup key from a subtask name (like the reference task_name)."""
    return re.sub(r"[^a-z0-9_-]+", "-", name.strip().lower()).strip("-") or "agent"


_RUNNING = "running"
_DONE = "done"
_FAILED = "failed"


class AgentLimitError(RuntimeError):
    """Too many sub-agents are already running concurrently."""


class DuplicateAgentError(AgentLimitError):
    """A sub-agent is already running this exact task (a re-spawn is redundant)."""


@dataclass
class SubAgent:
    id: str
    task: str
    isolate: bool = True
    status: str = _RUNNING
    result: str = ""
    seed_history: list = field(default_factory=list, repr=False)
    inbox: list = field(default_factory=list, repr=False)  # send_message queue
    dedup_key: str = ""  # stable key so a re-worded re-spawn is caught
    handle: asyncio.Task | None = field(default=None, repr=False)

    @property
    def running(self) -> bool:
        return self.status == _RUNNING


def _format_result_message(sub: SubAgent) -> str:
    """The mailbox envelope a finished sub-agent posts to the parent."""
    return (
        f"Message Type: RESULT\n"
        f"Agent: {sub.id}\n"
        f"Status: {sub.status}\n"
        f"Payload:\n{sub.result}"
    )


class AgentControl:
    """Per-parent-session registry, concurrency limit, and result mailbox."""

    def __init__(
        self,
        workspace: str,
        model: str | None = None,
        *,
        max_threads: int = MAX_CONCURRENT_SUBAGENTS,
    ) -> None:
        self._workspace = workspace
        self._model = model
        self._max_threads = max(1, max_threads)
        self._agents: dict[str, SubAgent] = {}
        self._seq = 0
        self._mailbox: list[str] = []
        self._activity = asyncio.Event()
        self._git_lock = asyncio.Lock()
        self._history_provider = None  # set to the parent session's history()

    def set_history_provider(self, provider) -> None:
        """Wire the parent session's ``history()`` so fork_turns can inherit
        the parent's context. Called once, after the session is built."""
        self._history_provider = provider

    # -- introspection ---------------------------------------------------------

    def active_count(self) -> int:
        return sum(1 for a in self._agents.values() if a.running)

    def get(self, agent_id: str) -> SubAgent | None:
        return self._agents.get(agent_id)

    def all(self) -> list[SubAgent]:
        return list(self._agents.values())

    def interrupt(self, agent_id: str) -> str:
        """Cancel a running sub-agent. Its cancelled result reaches the mailbox
        like any other completion."""
        sub = self._agents.get(agent_id)
        if sub is None:
            return f"no such agent: {agent_id}"
        if not sub.running:
            return f"{agent_id} already finished ({sub.status})"
        if sub.handle is not None:
            sub.handle.cancel()
        return f"interrupt requested for {agent_id}"

    def send_message(self, agent_id: str, message: str) -> str:
        """Queue a message to a RUNNING sub-agent; it is injected into that
        sub-agent's turn at its next step. Only works while it is still running
        (a finished sub-agent cannot receive one)."""
        sub = self._agents.get(agent_id)
        if sub is None:
            return f"no such agent: {agent_id}"
        if not sub.running:
            return f"{agent_id} already finished ({sub.status}); cannot deliver"
        if not message.strip():
            return "Error: message is empty."
        sub.inbox.append(f"Message Type: MESSAGE\nFrom: parent\nPayload:\n{message}")
        return f"delivered to {agent_id}"

    def _make_inbox_drainer(self, sub: SubAgent):
        """The injection_callback for a sub-agent: drains its send_message inbox
        into its own turn (mirrors the parent's drain_injections)."""

        async def drain(limit: int | None = None) -> list[dict[str, str]]:
            if not sub.inbox:
                return []
            take = sub.inbox if limit is None else sub.inbox[:limit]
            sub.inbox = sub.inbox[len(take) :]
            return [{"role": "user", "content": m} for m in take]

        return drain

    # -- spawn -----------------------------------------------------------------

    def spawn(
        self,
        task: str,
        *,
        name: str | None = None,
        isolate: bool = True,
        fork_turns: str | int = "none",
    ) -> str:
        """Start a sub-agent in the background and return its id (non-blocking).

        ``name`` is a short stable label for the subtask; it is the dedup key, so
        re-spawning the same-named subtask while it runs is refused even if the
        task text was reworded. Without a name the (normalized) task text is the
        key. ``fork_turns`` inherits the parent's context: ``"none"`` (fresh),
        ``"all"`` (the whole conversation), or an int N (the last N turns) — only
        user messages and the parent's final answers carry over. Raises
        :class:`DuplicateAgentError` when a matching subtask is already running,
        :class:`AgentLimitError` when the concurrency limit is reached.
        """
        # Dedup against any prior subtask with this key that is still running OR
        # already succeeded — re-spawning finished work is the exact waste a real
        # model produced (it re-spawned modules it had already built). A FAILED
        # subtask may be retried.
        key = _slug(name) if name else " ".join(task.split()).lower()
        for existing in self._agents.values():
            if existing.dedup_key == key and existing.status != _FAILED:
                verb = "is already handling" if existing.running else "already handled"
                tail = (
                    "its result will come back on its own; call wait_agent"
                    if existing.running
                    else "its output is already in the workspace; read it — do not re-spawn"
                )
                raise DuplicateAgentError(
                    f"{existing.id} {verb} this subtask — {tail}."
                )
        if self.active_count() >= self._max_threads:
            raise AgentLimitError(
                f"at most {self._max_threads} sub-agents can run at once; call "
                "wait_agent to collect a finished one before spawning more"
            )
        self._seq += 1
        base = _slug(name) if name else f"agent-{self._seq}"
        agent_id = base if base not in self._agents else f"{base}-{self._seq}"
        sub = SubAgent(
            id=agent_id,
            task=task,
            isolate=isolate,
            seed_history=self._fork_history(fork_turns),
            dedup_key=key,
        )
        self._agents[agent_id] = sub
        sub.handle = asyncio.ensure_future(self._run(sub))
        return agent_id

    def _fork_history(self, fork_turns: str | int) -> list:
        """The filtered slice of parent history a forked sub-agent inherits."""
        if fork_turns == "none" or self._history_provider is None:
            return []
        history = self._history_provider() or []
        kept = [
            dict(m)
            for m in history
            if m.get("role") == "user"
            or (
                m.get("role") == "assistant"
                and m.get("content")
                and not m.get("tool_calls")
            )
        ]
        if fork_turns == "all":
            return kept
        n = int(fork_turns)
        boundaries = [i for i, m in enumerate(kept) if m.get("role") == "user"]
        if n <= 0 or len(boundaries) <= n:
            return kept
        return kept[boundaries[len(boundaries) - n] :]

    async def _run(self, sub: SubAgent) -> None:
        try:
            if sub.isolate:
                sub.result = await self._run_isolated(sub)
            else:
                sub.result = await self._run_subagent(
                    sub.task,
                    self._workspace,
                    seed_history=sub.seed_history,
                    inbox_drainer=self._make_inbox_drainer(sub),
                )
            sub.status = _DONE
        except asyncio.CancelledError:
            sub.status = _FAILED
            sub.result = "cancelled"
            raise
        except Exception as exc:  # noqa: BLE001 - a sub-agent failure is data
            sub.status = _FAILED
            sub.result = f"error: {exc}"
        finally:
            if sub.status != _RUNNING:
                self._post(_format_result_message(sub))

    async def _run_isolated(self, sub: SubAgent) -> str:
        from core.team.worktree import WorktreeManager

        wt = WorktreeManager(self._workspace)
        async with self._git_lock:
            wt.ensure_base()
            tree = wt.create(sub.id)
        try:
            summary = await self._run_subagent(
                sub.task,
                tree,
                seed_history=sub.seed_history,
                inbox_drainer=self._make_inbox_drainer(sub),
            )
            async with self._git_lock:
                merge = wt.merge(sub.id)
        finally:
            async with self._git_lock:
                wt.cleanup(sub.id)
                # Last isolated agent out removes the now-empty shared dir.
                try:
                    root = wt.worktrees_root
                    if root.is_dir() and not any(root.iterdir()):
                        root.rmdir()
                except OSError:
                    pass
        if merge.clean:
            return f"(isolated, merged cleanly)\n{summary}"
        if merge.conflicts:
            return (
                f"(isolated, NOT merged — conflicts in {', '.join(merge.conflicts)}; "
                f"reconcile manually)\n{summary}"
            )
        return f"(isolated, merge blocked: {merge.detail})\n{summary}"

    async def _run_subagent(
        self,
        task: str,
        workspace: str,
        *,
        seed_history: list | None = None,
        inbox_drainer=None,
    ) -> str:
        from core.agent_setup import build_agent_session
        from core.events import UserInput

        session, _model, _engine = build_agent_session(
            workspace=workspace,
            model=self._model,
            allow_spawn=False,  # depth cap: sub-agents cannot spawn again
            injection_callback=inbox_drainer,  # receives parent's send_message
        )
        if seed_history:
            session.load_history(seed_history)  # fork_turns: inherit parent context
        final = ""
        async for event in session.run_stream(UserInput(text=task)):
            if event.msg.type == "task_complete":
                final = event.msg.final_text or ""
        return final.strip() or "(sub-agent produced no summary)"

    # -- mailbox ---------------------------------------------------------------

    def _post(self, message: str) -> None:
        self._mailbox.append(message)
        self._activity.set()

    async def wait_for_activity(self, timeout: float | None) -> str:
        """Park until a sub-agent posts to the mailbox, or timeout. Returns a
        short outcome; the messages themselves reach the model via injection."""
        if self._mailbox:
            return "One or more sub-agents have results waiting."
        if self.active_count() == 0:
            return "No sub-agents are running."
        self._activity.clear()
        # Re-check after clearing: a result posted in the check→clear window set
        # the event we just reset, but its message is safe in the mailbox.
        if self._mailbox:
            return "One or more sub-agents have results waiting."
        try:
            await asyncio.wait_for(self._activity.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return "Wait timed out; sub-agents are still running."
        return "One or more sub-agents finished."

    async def drain_injections(self, limit: int | None = None) -> list[dict[str, str]]:
        """Pop pending mailbox messages as user-role injections for the parent's
        next turn. Wired as the run's ``injection_callback``."""
        if not self._mailbox:
            return []
        take = self._mailbox if limit is None else self._mailbox[:limit]
        self._mailbox = self._mailbox[len(take) :]
        if not self._mailbox:
            self._activity.clear()
        return [{"role": "user", "content": msg} for msg in take]

    async def close(self) -> None:
        """Cancel any still-running sub-agents (best-effort session teardown)."""
        for sub in self._agents.values():
            if sub.handle is not None and not sub.handle.done():
                sub.handle.cancel()
