"""Turn-scoped routing for the minimal Goal tools."""

from __future__ import annotations

import asyncio
import logging
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


logger = logging.getLogger(__name__)

GOAL_TOOL_NAMES = frozenset({"get_goal", "update_goal"})
# The Goal ledger's reason limit (GOAL_OUTCOME_REASON_MAX_CHARS); a longer
# auto-block reason would fail the very update that ends the retries.
_AUTO_BLOCK_REASON_MAX_CHARS = 2_000
# Rejected claims per Goal before it stops even while each one followed new
# changes: every claim may start a review that costs real tokens.
MAX_TOTAL_REJECTIONS = 10


@dataclass(slots=True)
class _Rejections:
    """Rejected claims of one Goal: in a row without new changes, and in all."""

    stalled: int = 0
    total: int = 0
    marker: str | None = None


_GOAL_CLOSURE_PROMPT = """\
Before ending this Goal-associated Turn, call get_goal and compare the latest
objective with the current workspace and evidence.

- If every requirement of the Goal is proved by current evidence (the code,
  commands you ran, tests you checked cover it), call
  update_goal(status="complete") with a concise reason naming that evidence.
- If the same external blocker has stopped all progress across several turns
  and no safe action remains, call update_goal(status="blocked") and explain it.
- Otherwise continue concrete work toward the Goal.

Do not mark the Goal complete merely because one intermediate check passed or
because no remaining work is obvious."""


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
        expected_objective: str | None = None,
    ) -> dict[str, Any]:
        """Record the decision.

        ``expected_objective``: the objective a verifier judged. When the Goal
        no longer has it (the user edited it meanwhile), refuse instead of
        recording a verdict about another objective.
        """
        ...

    def record_verification_tokens(
        self,
        context: GoalRuntimeContext,
        tokens: int,
    ) -> None:
        """Charge the tokens a completion review spent to the Goal."""
        ...

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
    the goal is not updated. After ``max_rejections`` rejected claims in a
    row with no new changes in between, after ``MAX_TOTAL_REJECTIONS`` in
    all, or at once when the verifier finds the Goal impossible, the router
    moves the goal to ``blocked`` itself, so a model that keeps re-claiming
    completion cannot burn Turns forever. A verifier that cannot reach a
    verdict (:class:`VerificationUnavailable`) also stops the Goal as blocked,
    with that reason, but counts no rejection: the work may well be fine.
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
        self._rejections: dict[str, _Rejections] = {}

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

        verdict = None
        if status == "complete" and verifier is not None:
            from core.agent_runtime.goal_verifier import (
                GoalVerdict,
                VerificationUnavailable,
            )

            judged_objective = None
            progress_marker = ""
            try:
                if settle is not None:
                    await settle()
                claim = handler.completion_request(context, reason=reason)
                judged_objective = claim.objective
                progress_marker = claim.progress_marker
                verdict = await verifier(claim)
            except asyncio.CancelledError as exc:
                # Stopped mid-review: what it spent so far is still spent.
                self._charge(handler, context, getattr(exc, "tokens", 0))
                raise
            except VerificationUnavailable as exc:
                self._charge(handler, context, exc.tokens)
                return self._stop_unverified(
                    handler, context, str(exc), judged_objective
                )
            except Exception as exc:  # noqa: BLE001 - fail closed, report to the model
                verdict = GoalVerdict(
                    confirmed=False,
                    justification=f"Completion verification failed: {exc}",
                    tokens=getattr(exc, "tokens", 0),
                )
            if not verdict.confirmed:
                # Charged first, so the rejection can say when the review
                # used up the budget.
                self._charge(handler, context, verdict.tokens)
                return self._reject_completion(
                    handler, context, verdict, judged_objective, progress_marker
                )
            try:
                result = handler.update_goal(
                    context,
                    status=status,
                    reason=reason,
                    expected_objective=judged_objective,
                )
            finally:
                # After the decision: charging first could end the budget
                # and refuse a completion the review just confirmed.
                self._charge(handler, context, verdict.tokens)
        else:
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
        if verdict is not None:
            result = {
                **result,
                "verdict": {
                    "confirmed": True,
                    "justification": verdict.justification,
                },
            }
        return result

    @staticmethod
    def _charge(
        handler: GoalRuntimeHandler,
        context: GoalRuntimeContext,
        tokens: int,
    ) -> None:
        """Charge a review's tokens; best effort, never hides the decision."""

        if tokens <= 0:
            return
        try:
            handler.record_verification_tokens(context, tokens)
        except Exception:  # noqa: BLE001 - accounting must not change the outcome
            logger.exception("completion review tokens were not charged")

    def _goal_still_open(
        self,
        handler: GoalRuntimeHandler,
        context: GoalRuntimeContext,
    ) -> bool:
        try:
            return bool(handler.read_goal(context).get("canUpdate", True))
        except Exception:  # noqa: BLE001 - the answer itself must still arrive
            logger.exception("Goal state could not be read after a verdict")
            return True

    def _stop_unverified(
        self,
        handler: GoalRuntimeHandler,
        context: GoalRuntimeContext,
        problem: str,
        judged_objective: str | None,
    ) -> dict[str, Any]:
        """Stop the Goal when its claim could not be verified at all.

        Blocked with the reason, so the user sees why and can resume it; no
        rejection is counted against the agent.
        """

        if not self._goal_still_open(handler, context):
            # The review used up the token budget, or the Goal was paused or
            # replaced meanwhile: nothing is left to stop.
            return {
                "goalId": context.goal_id,
                "updated": False,
                "verdict": {
                    "confirmed": False,
                    "justification": (
                        f"The completion claim could not be verified ({problem}). "
                        "This Goal is no longer active."
                    ),
                },
            }
        reason = (
            f"The completion claim could not be verified ({problem}). "
            "Resume the Goal to try again."
        )[:_AUTO_BLOCK_REASON_MAX_CHARS]
        result = handler.update_goal(
            context,
            status="blocked",
            reason=reason,
            expected_objective=judged_objective,
        )
        with self._lock:
            if self._context != context:
                raise GoalRuntimeError("Goal Turn changed while recording the decision")
            self._terminal_requested = True
        return {
            **result,
            "updated": True,
            "unverified": True,
            "verdict": {"confirmed": False, "justification": reason},
        }

    def _reject_completion(
        self,
        handler: GoalRuntimeHandler,
        context: GoalRuntimeContext,
        verdict: GoalVerdict,
        judged_objective: str | None = None,
        progress_marker: str = "",
    ) -> dict[str, Any]:
        """Return a rejection to the model; auto-block when it goes nowhere."""

        with self._lock:
            if self._context != context:
                raise GoalRuntimeError("Goal Turn changed while recording the decision")
            counted = self._rejections.setdefault(context.goal_id, _Rejections())
            # A file change the last rejection had not seen: the agent is
            # getting on. No change in the claim's Turn is no progress.
            progressed = (
                counted.total > 0
                and bool(progress_marker)
                and progress_marker != counted.marker
            )
            counted.stalled = 1 if progressed else counted.stalled + 1
            counted.total += 1
            if progress_marker:
                counted.marker = progress_marker
            count = counted.stalled
            total = counted.total
            max_rejections = self._max_rejections
            exhausted = (
                verdict.impossible
                or count >= max_rejections
                or total >= MAX_TOTAL_REJECTIONS
            )

        rejection: dict[str, Any] = {
            "goalId": context.goal_id,
            "updated": False,
            "verdict": {
                "confirmed": False,
                "justification": verdict.justification,
            },
            "rejections": count,
            "maxRejections": max_rejections,
            "totalRejections": total,
        }
        if not self._goal_still_open(handler, context):
            # The review used up the token budget, or the Goal was paused or
            # replaced: no later claim in this Turn can be recorded, and
            # nothing is left to block.
            rejection["verdict"]["justification"] += (
                "\nThis Goal is no longer active (for example, its token "
                "budget is used up), so it cannot be claimed complete in this "
                "Turn."
            )
            return rejection
        if not exhausted:
            return rejection

        # The claim is exhausted: move the Goal to blocked (a terminal
        # decision the model asked for by never producing evidence) instead
        # of letting it burn Turns on the same rejected completion claim.
        result = handler.update_goal(
            context,
            status="blocked",
            reason=(
                verdict.justification
                if verdict.impossible
                else (
                    f"Blocked by the completion verifier after {count} rejected "
                    "completion claims with no new changes in between. "
                    f"Last rejection: {verdict.justification}"
                )
                if count >= max_rejections
                else (
                    f"Blocked by the completion verifier after {total} rejected "
                    f"completion claims. Last rejection: {verdict.justification}"
                )
            )[:_AUTO_BLOCK_REASON_MAX_CHARS],
            # A verdict about an objective the user has since edited must
            # not block the edited Goal either.
            expected_objective=judged_objective,
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
            "totalRejections": total,
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
