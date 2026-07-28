from __future__ import annotations

from collections import deque

import pytest

from core.application.errors import (
    ExpectedTurnMismatchError,
    NoActiveTurnError,
    TurnAlreadyRunningError,
    TurnNotSteerableError,
)
from core.application.interactive_turn_router import (
    InteractiveDelivery,
    InteractiveTurnRouter,
)
from core.application.turn_input_service import TurnInputReceipt
from core.application.turn_service import TurnSnapshot
from core.domain.turn import Turn


class ScriptedTurns:
    def __init__(self, outcomes, *, terminal: Turn | None = None) -> None:
        self.outcomes = deque(outcomes)
        self.calls: list[tuple[str, dict]] = []
        self.terminal = terminal

    def start(self, thread_id: str, **kwargs):
        self.calls.append(("start", {"thread_id": thread_id, **kwargs}))
        return self._next()

    def steer(self, thread_id: str, **kwargs):
        self.calls.append(("steer", {"thread_id": thread_id, **kwargs}))
        return self._next()

    def wait_until_terminal(self, turn_id: str):
        self.calls.append(("wait", {"turn_id": turn_id}))
        return self.terminal

    def _next(self):
        outcome = self.outcomes.popleft()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _turn(turn_id: str) -> Turn:
    return Turn(id=turn_id, thread_id="session-1", ordinal=1, prompt="task")


def _started(turn_id: str) -> TurnSnapshot:
    return TurnSnapshot(_turn(turn_id), (), ())


def _steered(turn_id: str, *, duplicate: bool = False) -> TurnInputReceipt:
    return TurnInputReceipt(
        message_id="message-1",
        delivery="current_turn",
        turn=_turn(turn_id),
        duplicate=duplicate,
    )


def test_cached_active_routes_plain_text_to_strict_steer() -> None:
    turns = ScriptedTurns([_steered("turn_active")])
    router = InteractiveTurnRouter(turns)  # type: ignore[arg-type]

    result = router.send(
        "session-1",
        prompt="停止使用缓存，但继续完成当前任务。",
        message_id="message-1",
        cached_active_turn_id="turn_active",
    )

    assert result.delivery is InteractiveDelivery.STEERED
    assert [name for name, _kwargs in turns.calls] == ["steer"]
    assert turns.calls[0][1]["expected_turn_id"] == "turn_active"
    assert turns.calls[0][1]["prompt"] == "停止使用缓存，但继续完成当前任务。"


def test_no_active_race_starts_once_with_the_same_message_id() -> None:
    turns = ScriptedTurns(
        [
            NoActiveTurnError("ended"),
            _started("turn_new"),
        ]
    )
    router = InteractiveTurnRouter(turns)  # type: ignore[arg-type]

    result = router.send(
        "session-1",
        prompt="continue",
        message_id="message-1",
        cached_active_turn_id="turn_old",
        skill_ids=("sk_0123456789abcdef01234567",),
    )

    assert result.delivery is InteractiveDelivery.STARTED
    assert [name for name, _kwargs in turns.calls] == ["steer", "start"]
    assert {call[1]["message_id"] for call in turns.calls} == {"message-1"}
    assert turns.calls[1][1]["skill_ids"] == ("sk_0123456789abcdef01234567",)


def test_no_active_then_continuation_race_steers_the_winning_turn_once() -> None:
    turns = ScriptedTurns(
        [
            NoActiveTurnError("ended"),
            TurnAlreadyRunningError(
                "goal continuation won",
                details={"actualTurnId": "turn_continuation"},
            ),
            _steered("turn_continuation"),
        ]
    )
    router = InteractiveTurnRouter(turns)  # type: ignore[arg-type]

    result = router.send(
        "session-1",
        prompt="Apply this to the continuation.",
        message_id="message-1",
        cached_active_turn_id="turn_old",
    )

    assert result.delivery is InteractiveDelivery.STEERED
    assert [name for name, _kwargs in turns.calls] == [
        "steer",
        "start",
        "steer",
    ]
    assert turns.calls[-1][1]["expected_turn_id"] == "turn_continuation"
    assert {
        call[1]["message_id"]
        for call in turns.calls
        if "message_id" in call[1]
    } == {"message-1"}


def test_start_race_steers_the_reported_active_turn_once() -> None:
    turns = ScriptedTurns(
        [
            TurnAlreadyRunningError(
                "active",
                details={"actualTurnId": "turn_actual"},
            ),
            _steered("turn_actual"),
        ]
    )
    router = InteractiveTurnRouter(turns)  # type: ignore[arg-type]

    result = router.send(
        "session-1",
        prompt="Use the existing work.",
        message_id="message-1",
        cached_active_turn_id=None,
    )

    assert result.delivery is InteractiveDelivery.STEERED
    assert [name for name, _kwargs in turns.calls] == ["start", "steer"]
    assert turns.calls[1][1]["expected_turn_id"] == "turn_actual"
    assert {call[1]["message_id"] for call in turns.calls} == {"message-1"}


def test_mismatch_retries_only_once_and_surfaces_the_second_race() -> None:
    turns = ScriptedTurns(
        [
            ExpectedTurnMismatchError("turn_stale", "turn_actual"),
            NoActiveTurnError("ended again"),
        ]
    )
    router = InteractiveTurnRouter(turns)  # type: ignore[arg-type]

    with pytest.raises(NoActiveTurnError):
        router.send(
            "session-1",
            prompt="Do not lose this input.",
            message_id="message-1",
            cached_active_turn_id="turn_stale",
        )

    assert [name for name, _kwargs in turns.calls] == ["steer", "steer"]
    assert turns.calls[1][1]["expected_turn_id"] == "turn_actual"
    assert {call[1]["message_id"] for call in turns.calls} == {"message-1"}


def test_final_close_waits_for_terminal_then_starts_same_message_once() -> None:
    terminal = _turn("turn_old")
    turns = ScriptedTurns(
        [
            TurnNotSteerableError(
                "closed",
                details={"state": "closed"},
            ),
            _started("turn_new"),
        ],
        terminal=terminal,
    )
    router = InteractiveTurnRouter(turns)  # type: ignore[arg-type]

    result = router.send(
        "session-1",
        prompt="continue after the answer boundary",
        message_id="message-1",
        cached_active_turn_id="turn_old",
    )

    assert result.delivery is InteractiveDelivery.STARTED
    assert [name for name, _kwargs in turns.calls] == [
        "steer",
        "wait",
        "start",
    ]
    assert turns.calls[-1][1]["message_id"] == "message-1"


@pytest.mark.parametrize("boundary_state", ["closing", "closed"])
def test_final_input_boundary_then_start_race_steers_next_turn_once(
    boundary_state: str,
) -> None:
    terminal = _turn("turn_old")
    turns = ScriptedTurns(
        [
            TurnNotSteerableError(
                f"input is {boundary_state}",
                details={"state": boundary_state},
            ),
            TurnAlreadyRunningError(
                "goal continuation won",
                details={"actualTurnId": "turn_continuation"},
            ),
            _steered("turn_continuation"),
        ],
        terminal=terminal,
    )
    router = InteractiveTurnRouter(turns)  # type: ignore[arg-type]

    result = router.send(
        "session-1",
        prompt="Do this exactly once in the next active Turn.",
        message_id="message-1",
        cached_active_turn_id="turn_old",
    )

    assert result.delivery is InteractiveDelivery.STEERED
    assert [name for name, _kwargs in turns.calls] == [
        "steer",
        "wait",
        "start",
        "steer",
    ]
    assert turns.calls[-1][1]["expected_turn_id"] == "turn_continuation"
    assert {
        call[1]["message_id"]
        for call in turns.calls
        if "message_id" in call[1]
    } == {"message-1"}
