"""Typed, bounded inputs delivered to one active Agent Turn."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import threading
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeAlias

DEFAULT_MAX_PENDING_INPUTS = 64
DEFAULT_MAX_INPUT_CHARS = 32_000
DEFAULT_MAX_PENDING_CHARS = 256_000
DEFAULT_DEDUP_HISTORY = 1_024


class TurnInputError(ValueError):
    """Base error for rejected active-Turn input."""


class TurnInputCapacityError(TurnInputError):
    """The bounded mailbox cannot accept more input."""


class TurnInputTooLargeError(TurnInputCapacityError):
    """One input exceeds the configured per-input limit."""


class TurnInputConflictError(TurnInputError):
    """A message id was reused with different content."""


class TurnInputTargetError(TurnInputError):
    """The input targets a Turn other than the mailbox owner."""

    def __init__(self, target_turn_id: str, actual_turn_id: str | None) -> None:
        super().__init__("target Turn is not the active Turn")
        self.target_turn_id = target_turn_id
        self.actual_turn_id = actual_turn_id


class TurnInputClosedError(TurnInputError):
    """The active Turn has crossed its final input boundary."""

    def __init__(self, state: MailboxState) -> None:
        super().__init__(f"active Turn input is {state.value}")
        self.state = state


class MailboxState(StrEnum):
    """Lifecycle of the input boundary for one active Turn."""

    STARTING = "starting"
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class UserSteer:
    """A durable user follow-up addressed to the active Turn."""

    message_id: str
    target_turn_id: str
    text: str


@dataclass(frozen=True, slots=True)
class GoalObjectiveUpdated:
    """A transient notification whose authority remains the Goal ledger."""

    message_id: str
    target_turn_id: str
    goal_id: str
    objective: str


@dataclass(frozen=True, slots=True)
class SubagentMessage:
    """A message from one child Agent to its owning active Turn."""

    message_id: str
    target_turn_id: str
    agent_id: str
    payload: str


TurnRuntimeInput: TypeAlias = UserSteer | GoalObjectiveUpdated | SubagentMessage
ProviderMessage: TypeAlias = dict[str, Any]
InjectionValue: TypeAlias = TurnRuntimeInput | ProviderMessage
InjectionCallback: TypeAlias = Callable[..., Awaitable[list[InjectionValue]]]
TurnInputSink: TypeAlias = Callable[[TurnRuntimeInput], bool]


def runtime_input_to_provider_message(value: InjectionValue) -> ProviderMessage:
    """Convert typed runtime input at the single provider-message boundary."""

    if isinstance(value, UserSteer):
        return {"role": "user", "content": value.text}
    if isinstance(value, GoalObjectiveUpdated):
        return {
            "role": "user",
            "content": (
                "The persistent Goal for this Thread was updated. The new "
                "objective supersedes the previous objective. Preserve useful "
                "work and adjust the current approach.\n\n"
                f"Goal ID: {value.goal_id}\n"
                f"New objective:\n{value.objective}"
            ),
        }
    if isinstance(value, SubagentMessage):
        return {
            "role": "user",
            "content": (
                "Message Type: SUBAGENT_MESSAGE\n"
                f"From: {value.agent_id}\n"
                f"Payload:\n{value.payload}"
            ),
        }
    if isinstance(value, dict):
        return value
    raise TypeError(f"unsupported runtime input: {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class TurnInputReservation:
    message_id: str
    target_turn_id: str


@dataclass(slots=True)
class _PendingInput:
    value: TurnRuntimeInput
    ready: bool = False

    @property
    def message_id(self) -> str:
        return self.value.message_id

    @property
    def target_turn_id(self) -> str:
        return self.value.target_turn_id

    @property
    def size(self) -> int:
        return len(_input_content(self.value))


class TurnInputMailbox:
    """Thread-safe input boundary owned by exactly one active Turn.

    Producers reserve capacity before durable persistence and commit afterward.
    The runner's final drain closes this mailbox under the same lock used by
    reservations. Therefore a Steer racing with final completion has only two
    outcomes:

    * it reserves first and is returned for another model sample; or
    * final close wins and the producer receives :class:`TurnInputClosedError`.

    Inputs are never carried into a future Turn.
    """

    def __init__(
        self,
        *,
        max_pending: int = DEFAULT_MAX_PENDING_INPUTS,
        max_input_chars: int = DEFAULT_MAX_INPUT_CHARS,
        max_pending_chars: int = DEFAULT_MAX_PENDING_CHARS,
        dedup_history: int = DEFAULT_DEDUP_HISTORY,
    ) -> None:
        if min(max_pending, max_input_chars, max_pending_chars, dedup_history) < 1:
            raise ValueError("mailbox limits must be positive")
        if dedup_history < max_pending:
            raise ValueError("dedup_history must cover every pending input")
        self.max_pending = max_pending
        self.max_input_chars = max_input_chars
        self.max_pending_chars = max_pending_chars
        self.dedup_history = dedup_history
        self._condition = threading.Condition(threading.RLock())
        self._active_turn_id: str | None = None
        self._state = MailboxState.CLOSED
        self._pending: deque[_PendingInput] = deque()
        self._pending_by_id: dict[str, _PendingInput] = {}
        self._seen: OrderedDict[str, str] = OrderedDict()
        self._pending_chars = 0

    @property
    def active_turn_id(self) -> str | None:
        with self._condition:
            return self._active_turn_id

    @property
    def state(self) -> MailboxState:
        with self._condition:
            return self._state

    @property
    def pending_count(self) -> int:
        with self._condition:
            return len(self._pending)

    def prepare(self, turn_id: str) -> None:
        """Claim the mailbox before the initial canonical user message exists."""

        clean_turn_id = turn_id.strip()
        if not clean_turn_id:
            raise ValueError("turn_id must not be empty")
        with self._condition:
            if self._active_turn_id is not None:
                if (
                    self._active_turn_id == clean_turn_id
                    and self._state is MailboxState.STARTING
                ):
                    return
                raise RuntimeError(
                    f"mailbox already belongs to active Turn {self._active_turn_id}"
                )
            if self._pending:
                raise RuntimeError("closed mailbox retained pending input")
            self._active_turn_id = clean_turn_id
            self._state = MailboxState.STARTING
            self._condition.notify_all()

    def activate(self, turn_id: str) -> None:
        clean_turn_id = turn_id.strip()
        if not clean_turn_id:
            raise ValueError("turn_id must not be empty")
        with self._condition:
            if self._active_turn_id is not None:
                if (
                    self._active_turn_id == clean_turn_id
                    and self._state
                    in {MailboxState.STARTING, MailboxState.OPEN}
                ):
                    self._state = MailboxState.OPEN
                    self._condition.notify_all()
                    return
                raise RuntimeError(
                    f"mailbox already belongs to active Turn {self._active_turn_id}"
                )
            if self._pending:
                raise RuntimeError("closed mailbox retained pending input")
            self._active_turn_id = clean_turn_id
            self._state = MailboxState.OPEN
            self._condition.notify_all()

    def deactivate(self, turn_id: str) -> None:
        """Force-close the owning Turn during abort/error/shutdown."""

        with self._condition:
            if self._active_turn_id != turn_id:
                return
            self._active_turn_id = None
            self._state = MailboxState.CLOSED
            self._pending.clear()
            self._pending_by_id.clear()
            self._pending_chars = 0
            self._condition.notify_all()

    def reserve(self, value: TurnRuntimeInput) -> TurnInputReservation | None:
        clean_value = _validated_input(value)
        digest = _input_digest(clean_value)
        content = _input_content(clean_value)
        with self._condition:
            while (
                self._state is MailboxState.STARTING
                and self._active_turn_id == clean_value.target_turn_id
            ):
                # The durable Turn exists before its canonical initial user
                # message is appended. Wait for that ordering boundary rather
                # than rejecting a valid early Steer or persisting it first.
                self._condition.wait()
            previous = self._seen.get(clean_value.message_id)
            if previous is not None:
                if previous != digest:
                    raise TurnInputConflictError(
                        "message_id was already used with different content"
                    )
                return None
            if self._state is not MailboxState.OPEN:
                raise TurnInputClosedError(self._state)
            if self._active_turn_id != clean_value.target_turn_id:
                raise TurnInputTargetError(
                    clean_value.target_turn_id,
                    self._active_turn_id,
                )
            if len(content) > self.max_input_chars:
                raise TurnInputTooLargeError(
                    f"input may contain at most {self.max_input_chars} characters"
                )
            if len(self._pending) >= self.max_pending:
                raise TurnInputCapacityError("Turn input mailbox is full")
            if self._pending_chars + len(content) > self.max_pending_chars:
                raise TurnInputCapacityError(
                    "Turn input mailbox character budget is full"
                )
            entry = _PendingInput(clean_value)
            self._pending.append(entry)
            self._pending_by_id[entry.message_id] = entry
            self._pending_chars += entry.size
            self._remember_locked(entry.message_id, digest)
            return TurnInputReservation(
                message_id=entry.message_id,
                target_turn_id=entry.target_turn_id,
            )

    def commit(self, reservation: TurnInputReservation) -> None:
        with self._condition:
            entry = self._pending_by_id.get(reservation.message_id)
            if entry is None:
                raise TurnInputConflictError("input reservation no longer exists")
            if entry.target_turn_id != reservation.target_turn_id:
                raise TurnInputConflictError("input reservation target changed")
            entry.ready = True
            self._condition.notify_all()

    def cancel(self, reservation: TurnInputReservation) -> None:
        with self._condition:
            entry = self._pending_by_id.pop(reservation.message_id, None)
            if entry is None:
                return
            self._pending = deque(
                candidate
                for candidate in self._pending
                if candidate.message_id != entry.message_id
            )
            self._pending_chars -= entry.size
            self._seen.pop(entry.message_id, None)
            self._condition.notify_all()

    def put_transient(self, value: TurnRuntimeInput) -> bool:
        """Atomically offer non-durable runtime context to the active Turn."""

        try:
            reservation = self.reserve(value)
        except TurnInputError:
            return False
        if reservation is None:
            return True
        self.commit(reservation)
        return True

    async def drain(
        self,
        *,
        limit: int | None = None,
        close_if_empty: bool = False,
    ) -> list[TurnRuntimeInput]:
        """Drain committed input, optionally closing the final-response race."""

        maximum = self.max_pending if limit is None else max(0, limit)
        if maximum == 0:
            return []
        with self._condition:
            drained = self._drain_ready_locked(maximum)
            if drained or not close_if_empty:
                return drained
            if self._state is not MailboxState.OPEN:
                return []
            if not self._pending:
                self._close_locked()
                return []
            # A producer reserved first and is persisting. Reject new producers
            # while the final drain waits for that reservation to resolve.
            self._state = MailboxState.CLOSING
        return await asyncio.to_thread(self._wait_for_reserved_input, maximum)

    def _wait_for_reserved_input(self, maximum: int) -> list[TurnRuntimeInput]:
        with self._condition:
            while True:
                if self._state is MailboxState.CLOSED:
                    return []
                drained = self._drain_ready_locked(maximum)
                if drained:
                    self._state = MailboxState.OPEN
                    self._condition.notify_all()
                    return drained
                if not self._pending:
                    self._close_locked()
                    return []
                self._condition.wait()

    def _drain_ready_locked(self, maximum: int) -> list[TurnRuntimeInput]:
        drained: list[TurnRuntimeInput] = []
        while self._pending and len(drained) < maximum:
            entry = self._pending[0]
            # Reservations define input order. A later, faster producer must
            # not leapfrog an earlier message that is still being persisted.
            if not entry.ready:
                break
            self._pending.popleft()
            drained.append(entry.value)
            self._drop_locked(entry)
        return drained

    def _close_locked(self) -> None:
        self._state = MailboxState.CLOSED
        self._active_turn_id = None
        self._condition.notify_all()

    def _drop_locked(self, entry: _PendingInput) -> None:
        self._pending_by_id.pop(entry.message_id, None)
        self._pending_chars -= entry.size

    def _remember_locked(self, message_id: str, digest: str) -> None:
        self._seen[message_id] = digest
        self._seen.move_to_end(message_id)
        while len(self._seen) > self.dedup_history:
            candidate, candidate_digest = self._seen.popitem(last=False)
            if candidate in self._pending_by_id:
                self._seen[candidate] = candidate_digest
                continue
            break


def compose_injection_callbacks(
    *callbacks: InjectionCallback | None,
) -> InjectionCallback | None:
    """Combine input sources while preserving the active-Turn close handshake."""

    sources = tuple(callback for callback in callbacks if callback is not None)
    if not sources:
        return None

    async def drain(
        *,
        limit: int | None = None,
        close_if_empty: bool = False,
    ) -> list[InjectionValue]:
        combined = await _drain_sources(sources, limit=limit)
        if combined or not close_if_empty:
            return combined

        # Non-lifecycle sources get one final check before the close-aware
        # mailbox atomically closes user input. This keeps a ready child result
        # from being hidden behind a premature user-input close.
        passive = tuple(
            source
            for source in sources
            if not _accepts_keyword(source, "close_if_empty")
        )
        close_aware = tuple(source for source in sources if source not in passive)
        combined = await _drain_sources(passive, limit=limit)
        if combined:
            return combined
        return await _drain_sources(
            close_aware,
            limit=limit,
            close_if_empty=True,
        )

    return drain


async def _drain_sources(
    sources: tuple[InjectionCallback, ...],
    *,
    limit: int | None,
    close_if_empty: bool = False,
) -> list[InjectionValue]:
    remaining = limit
    combined: list[InjectionValue] = []
    for callback in sources:
        if remaining is not None and remaining <= 0:
            break
        values = await _call_injection_source(
            callback,
            remaining,
            close_if_empty=close_if_empty,
        )
        if not values:
            continue
        if remaining is not None:
            values = values[:remaining]
            remaining -= len(values)
        combined.extend(values)
    return combined


async def _call_injection_source(
    callback: InjectionCallback,
    limit: int | None,
    *,
    close_if_empty: bool,
) -> list[InjectionValue]:
    kwargs: dict[str, Any] = {}
    if _accepts_keyword(callback, "limit"):
        kwargs["limit"] = limit
    if close_if_empty and _accepts_keyword(callback, "close_if_empty"):
        kwargs["close_if_empty"] = True
    result = await callback(**kwargs)
    return list(result or ())


def _accepts_keyword(callback: InjectionCallback, name: str) -> bool:
    parameters = inspect.signature(callback).parameters.values()
    return any(
        parameter.name == name or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _validated_input(value: TurnRuntimeInput) -> TurnRuntimeInput:
    fields = {
        "message_id": value.message_id,
        "target_turn_id": value.target_turn_id,
        **(
            {"text": value.text}
            if isinstance(value, UserSteer)
            else {
                "goal_id": value.goal_id,
                "objective": value.objective,
            }
            if isinstance(value, GoalObjectiveUpdated)
            else {
                "agent_id": value.agent_id,
                "payload": value.payload,
            }
        ),
    }
    cleaned = {name: content.strip() for name, content in fields.items()}
    empty = next((name for name, content in cleaned.items() if not content), None)
    if empty is not None:
        raise TurnInputError(f"{empty} must not be empty")
    if isinstance(value, UserSteer):
        return UserSteer(**cleaned)
    if isinstance(value, GoalObjectiveUpdated):
        return GoalObjectiveUpdated(**cleaned)
    return SubagentMessage(**cleaned)


def _input_content(value: TurnRuntimeInput) -> str:
    if isinstance(value, UserSteer):
        return value.text
    if isinstance(value, GoalObjectiveUpdated):
        return value.objective
    return value.payload


def _input_digest(value: TurnRuntimeInput) -> str:
    if isinstance(value, UserSteer):
        identity = ("user_steer", value.target_turn_id, value.text)
    elif isinstance(value, GoalObjectiveUpdated):
        identity = (
            "goal_objective_updated",
            value.target_turn_id,
            value.goal_id,
            value.objective,
        )
    else:
        identity = (
            "subagent_message",
            value.target_turn_id,
            value.agent_id,
            value.payload,
        )
    return hashlib.sha256("\0".join(identity).encode()).hexdigest()


__all__ = [
    "GoalObjectiveUpdated",
    "InjectionCallback",
    "InjectionValue",
    "MailboxState",
    "ProviderMessage",
    "SubagentMessage",
    "TurnInputCapacityError",
    "TurnInputClosedError",
    "TurnInputConflictError",
    "TurnInputError",
    "TurnInputMailbox",
    "TurnInputReservation",
    "TurnInputSink",
    "TurnInputTargetError",
    "TurnInputTooLargeError",
    "TurnRuntimeInput",
    "UserSteer",
    "compose_injection_callbacks",
    "runtime_input_to_provider_message",
]
