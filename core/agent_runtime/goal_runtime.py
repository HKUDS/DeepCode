"""Turn-scoped routing for the minimal Goal tools."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Protocol


GOAL_TOOL_NAMES = frozenset({"get_goal", "update_goal"})
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


class GoalRuntimeRouter:
    """Route Goal tools only while one attributed Turn owns the runtime."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handler: GoalRuntimeHandler | None = None
        self._context: GoalRuntimeContext | None = None
        self._terminal_requested = False

    def configure(self, handler: GoalRuntimeHandler) -> None:
        with self._lock:
            self._handler = handler

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

    def request(
        self,
        *,
        status: str,
        reason: str | None,
    ) -> dict[str, Any]:
        handler, context = self._snapshot()
        result = handler.update_goal(
            context,
            status=status,
            reason=reason,
        )
        with self._lock:
            if self._context != context:
                raise GoalRuntimeError("Goal Turn changed while recording the decision")
            self._terminal_requested = True
        return result

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
