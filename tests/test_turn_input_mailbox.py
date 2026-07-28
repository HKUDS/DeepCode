from __future__ import annotations

import asyncio

import pytest

from core.agent_runtime.injections import (
    GoalObjectiveUpdated,
    MailboxState,
    TurnInputClosedError,
    TurnInputConflictError,
    TurnInputMailbox,
    UserSteer,
    compose_injection_callbacks,
    runtime_input_to_provider_message,
)


@pytest.mark.asyncio
async def test_mailbox_only_drains_committed_typed_input() -> None:
    mailbox = TurnInputMailbox()
    mailbox.activate("turn_current")
    reservation = mailbox.reserve(
        GoalObjectiveUpdated(
            message_id="message-1",
            target_turn_id="turn_current",
            goal_id="goal-1",
            objective="Use the revised Goal.",
        )
    )
    assert reservation is not None
    assert await mailbox.drain() == []

    mailbox.commit(reservation)
    drained = await mailbox.drain()
    assert drained == [
        GoalObjectiveUpdated(
            message_id="message-1",
            target_turn_id="turn_current",
            goal_id="goal-1",
            objective="Use the revised Goal.",
        )
    ]
    provider_message = runtime_input_to_provider_message(drained[0])
    assert provider_message["role"] == "user"
    assert "Use the revised Goal." in provider_message["content"]
    assert "<deepcode_context" not in provider_message["content"]


@pytest.mark.asyncio
async def test_mailbox_waits_for_initial_message_before_accepting_early_steer() -> None:
    mailbox = TurnInputMailbox()
    mailbox.prepare("turn_current")
    value = UserSteer(
        message_id="early-user",
        target_turn_id="turn_current",
        text="Keep the public API compatible.",
    )

    reservation_task = asyncio.create_task(asyncio.to_thread(mailbox.reserve, value))
    await asyncio.sleep(0.01)
    assert not reservation_task.done()

    mailbox.activate("turn_current")
    reservation = await reservation_task
    assert reservation is not None
    mailbox.commit(reservation)
    assert await mailbox.drain() == [value]


@pytest.mark.asyncio
async def test_final_close_waits_for_reserved_input_and_reopens_for_sampling() -> None:
    mailbox = TurnInputMailbox()
    mailbox.activate("turn_current")
    reservation = mailbox.reserve(
        UserSteer(
            message_id="user-1",
            target_turn_id="turn_current",
            text="Keep the compatibility layer.",
        )
    )
    assert reservation is not None

    final_drain = asyncio.create_task(mailbox.drain(close_if_empty=True))
    for _ in range(100):
        if mailbox.state is MailboxState.CLOSING:
            break
        await asyncio.sleep(0)
    assert mailbox.state is MailboxState.CLOSING

    mailbox.commit(reservation)
    assert await final_drain == [
        UserSteer(
            message_id="user-1",
            target_turn_id="turn_current",
            text="Keep the compatibility layer.",
        )
    ]
    assert mailbox.state is MailboxState.OPEN


@pytest.mark.asyncio
async def test_final_close_rejects_late_input_instead_of_carrying_it_forward() -> None:
    mailbox = TurnInputMailbox()
    mailbox.activate("turn_old")

    assert await mailbox.drain(close_if_empty=True) == []
    assert mailbox.state is MailboxState.CLOSED
    with pytest.raises(TurnInputClosedError):
        mailbox.reserve(
            UserSteer(
                message_id="late-user",
                target_turn_id="turn_old",
                text="This belongs to the old Turn.",
            )
        )

    mailbox.activate("turn_new")
    assert await mailbox.drain() == []


def test_mailbox_message_ids_are_idempotent_but_not_reusable() -> None:
    mailbox = TurnInputMailbox()
    mailbox.activate("turn_current")
    value = UserSteer(
        message_id="stable-id",
        target_turn_id="turn_current",
        text="First value",
    )
    reservation = mailbox.reserve(value)
    assert reservation is not None
    mailbox.commit(reservation)
    assert mailbox.reserve(value) is None
    with pytest.raises(TurnInputConflictError):
        mailbox.reserve(
            UserSteer(
                message_id="stable-id",
                target_turn_id="turn_current",
                text="Different value",
            )
        )


@pytest.mark.asyncio
async def test_reserved_input_order_cannot_be_overtaken() -> None:
    mailbox = TurnInputMailbox()
    mailbox.activate("turn_current")
    first = mailbox.reserve(
        UserSteer("first", "turn_current", "First accepted message")
    )
    second = mailbox.reserve(
        UserSteer("second", "turn_current", "Second accepted message")
    )
    assert first is not None and second is not None

    mailbox.commit(second)
    assert await mailbox.drain() == []
    mailbox.commit(first)
    assert [item.message_id for item in await mailbox.drain()] == [
        "first",
        "second",
    ]


@pytest.mark.asyncio
async def test_transient_input_uses_the_same_active_turn_boundary() -> None:
    mailbox = TurnInputMailbox()
    mailbox.activate("turn_current")
    value = GoalObjectiveUpdated(
        message_id="goal-update",
        target_turn_id="turn_current",
        goal_id="goal-1",
        objective="Revised objective",
    )
    assert mailbox.put_transient(value) is True
    assert await mailbox.drain() == [value]

    assert await mailbox.drain(close_if_empty=True) == []
    assert (
        mailbox.put_transient(
            GoalObjectiveUpdated(
                message_id="late-update",
                target_turn_id="turn_current",
                goal_id="goal-1",
                objective="Too late",
            )
        )
        is False
    )


@pytest.mark.asyncio
async def test_composed_sources_close_only_after_passive_sources_are_empty() -> None:
    mailbox = TurnInputMailbox()
    mailbox.activate("turn_current")
    passive_values = [{"role": "user", "content": "subagent result"}]

    async def passive(*, limit=None):
        del limit
        values = list(passive_values)
        passive_values.clear()
        return values

    combined = compose_injection_callbacks(mailbox.drain, passive)
    assert combined is not None
    assert await combined(close_if_empty=True) == [
        {"role": "user", "content": "subagent result"}
    ]
    assert mailbox.state is MailboxState.OPEN

    assert await combined(close_if_empty=True) == []
    assert mailbox.state is MailboxState.CLOSED
