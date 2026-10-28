"""Turn-scoped routing for the minimal Goal tools."""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from core.agent_runtime.goal_verifier import (
        GoalCompletionRequest,
        GoalCompletionVerifier,
        GoalVerdict,
    )


GOAL_TOOL_NAMES = frozenset({"get_goal", "update_goal"})
# The Goal ledger's reason limit (GOAL_OUTCOME_REASON_MAX_CHARS); a longer
# auto-block reason would fail the very update that ends the retries.
_AUTO_BLOCK_REASON_MAX_CHARS = 2_000
_GOAL_CLOSURE_PROMPT = """\
Before ending this Goal-associated Turn, call get_goal and compare the latest
objective with the current workspace and evidence.

- If the complete Goal is satisfied, call update_goal(status="complete") and
  give a concise reason grounded in evidence from this Turn.
- If a persistent external blocker leaves no safe action, call
  update_goal(status="blocked") and explain it.
- Otherwise continue concrete work toward the Goal.

Do not mark the Goal complete merely because one intermediate check passed."""


class GoalRuntimeError(RuntimeError):
    """Raised when a Goal tool is unavailable or its active Turn changed."""


@dataclass(frozen=True, slots=True)
class GoalRuntimeContext:
    thread_id: str
    goal_id: str
    turn_id: str


class GoalRuntimeHandler(Protocol):
    """Application-owned durable operations exposed to the runtime router."""

    def read_goal(self, context: GoalRuntimeContext) -> dict[str, Any]: ...

    def update_goal(
        self,
        context: GoalRuntimeContext,
        *,
        status: str,
        reason: str | None,
    ) -> dict[str, Any]: ...

    def completion_request(
        self,
        context: GoalRuntimeContext,
        *,
        reason: str | None,
    ) -> GoalCompletionRequest:
        """Build the claim a configured verifier judges.

        Only consulted while a completion verifier is configured; carries the
        real objective and this Turn's existing evidence references.
        """
        ...


class GoalRuntimeRouter:
    """Route Goal tools only while one attributed Turn owns the runtime.

    When a completion verifier is configured (see
    :meth:`configure_completion_verifier`), a ``complete`` request is judged
    exactly once, asynchronously, before any durable update. A rejection is
    returned to the model as data — with the verifier's justification — and
    the goal is not updated. After ``max_rejections`` rejected claims the
    router moves the goal to ``blocked`` itself, so a model that keeps
    re-claiming completion cannot burn Turns forever.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handler: GoalRuntimeHandler | None = None
        self._context: GoalRuntimeContext | None = None
        self._terminal_requested = False
        self._verifier: GoalCompletionVerifier | None = None
        self._settle: Callable[[], Awaitable[None]] | None = None
        self._max_rejections = 3
        # Rejected completion claims per goal, so a model cannot burn Turns
        # retrying the same claim; cleared on success, auto-block, and every
        # (re)configuration. Process memory only: a restart starts over.
        self._rejections: dict[str, int] = {}

    def configure(self, handler: GoalRuntimeHandler) -> None:
        with self._lock:
            self._handler = handler

    def configure_completion_verifier(
        self,
        verifier: GoalCompletionVerifier | None,
        *,
        max_rejections: int = 3,
        settle: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Arm the single asynchronous completion gate, or disarm it with None.

        There is deliberately no way to pass a pre-computed verdict into
        :meth:`request`; every ``complete`` claim goes through ``verifier``
        exactly once. ``settle`` runs first, so the Turn record the verifier
        reads includes every step the agent has already reported.
        """

        if max_rejections < 1:
            raise ValueError("max_rejections must be positive")
        with self._lock:
            self._verifier = verifier
            self._settle = settle if verifier is not None else None
            self._max_rejections = max_rejections
            self._rejections.clear()

    def activate(self, context: GoalRuntimeContext) -> None:
        with self._lock:
            if self._context is not None and self._context.turn_id != context.turn_id:
                raise GoalRuntimeError(
                    f"Goal runtime is already active for Turn {self._context.turn_id}"
                )
            self._context = context
            self._terminal_requested = False

    def deactivate(self, turn_id: str) -> None:
        with self._lock:
            if self._context is None or self._context.turn_id != turn_id:
                return
            self._context = None
            self._terminal_requested = False

    def read(self) -> dict[str, Any]:
        handler, context = self._snapshot()
        return handler.read_goal(context)

    async def request(
        self,
        *,
        status: str,
        reason: str | None,
    ) -> dict[str, Any]:
        """Record one terminal decision, verifying a ``complete`` claim once."""

        handler, context = self._snapshot()
        with self._lock:
            verifier = self._verifier
            settle = self._settle

        if status == "complete" and verifier is not None:
            from core.agent_runtime.goal_verifier import GoalVerdict

            try:
                if settle is not None:
                    await settle()
                verdict = await verifier(
                    handler.completion_request(context, reason=reason)
                )
            except Exception as exc:  # noqa: BLE001 - fail closed, report to the model
                verdict = GoalVerdict(
                    confirmed=False,
                    justification=f"Completion verification failed: {exc}",
                )
            if not verdict.confirmed:
                return self._reject_completion(handler, context, verdict)

        result = handler.update_goal(
            context,
            status=status,
            reason=reason,
        )
        with self._lock:
            if self._context != context:
                raise GoalRuntimeError("Goal Turn changed while recording the decision")
            self._terminal_requested = True
            self._rejections.pop(context.goal_id, None)
        return result

    def _reject_completion(
        self,
        handler: GoalRuntimeHandler,
        context: GoalRuntimeContext,
        verdict: GoalVerdict,
    ) -> dict[str, Any]:
        """Return a rejection to the model; auto-block after N rejections."""

        with self._lock:
            if self._context != context:
                raise GoalRuntimeError("Goal Turn changed while recording the decision")
            count = self._rejections.get(context.goal_id, 0) + 1
            self._rejections[context.goal_id] = count
            max_rejections = self._max_rejections
            exhausted = count >= max_rejections

        rejection: dict[str, Any] = {
            "goalId": context.goal_id,
            "updated": False,
            "verdict": {
                "confirmed": False,
                "justification": verdict.justification,
            },
            "rejections": count,
            "maxRejections": max_rejections,
        }
        if not exhausted:
            return rejection

        # The claim is exhausted: move the Goal to blocked (a terminal
        # decision the model asked for by never producing evidence) instead
        # of letting it burn Turns on the same rejected completion claim.
        result = handler.update_goal(
            context,
            status="blocked",
            reason=(
                f"Blocked by the completion verifier after {count} rejected "
                f"completion claims. Last rejection: {verdict.justification}"
            )[:_AUTO_BLOCK_REASON_MAX_CHARS],
        )
        with self._lock:
            if self._context != context:
                raise GoalRuntimeError("Goal Turn changed while recording the decision")
            self._terminal_requested = True
            self._rejections.pop(context.goal_id, None)
        return {
            **result,
            "updated": True,
            "verdict": rejection["verdict"],
            "rejections": count,
            "maxRejections": max_rejections,
            "autoBlocked": True,
        }

    def visible_tool_names(self, names: tuple[str, ...]) -> tuple[str, ...]:
        """Narrow tools dynamically without ever adding a capability."""

        with self._lock:
            active = self._context is not None and self._handler is not None
            terminal_requested = self._terminal_requested
        if not active:
            return tuple(name for name in names if name not in GOAL_TOOL_NAMES)
        if terminal_requested:
            # The terminal request is durable.  The model gets one final
            # response pass, but no further side-effecting tools.
            return ()
        return names

    def closure_prompt(self, stop_hook_active: bool = False) -> str | None:
        """Request one model-owned Goal decision before a clean Turn exit."""

        with self._lock:
            active = self._context is not None and self._handler is not None
            terminal_requested = self._terminal_requested
        if not active or terminal_requested or stop_hook_active:
            return None
        return _GOAL_CLOSURE_PROMPT

    def _snapshot(self) -> tuple[GoalRuntimeHandler, GoalRuntimeContext]:
        with self._lock:
            handler = self._handler
            context = self._context
        if handler is None or context is None:
            raise GoalRuntimeError(
                "Goal tools are only available in a Goal-associated Turn"
            )
        return handler, context


__all__ = [
    "GOAL_TOOL_NAMES",
    "GoalRuntimeContext",
    "GoalRuntimeError",
    "GoalRuntimeHandler",
    "GoalRuntimeRouter",
]
