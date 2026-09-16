"""Headless service attachment preserving foreground exit and approval behavior."""

from __future__ import annotations

import asyncio
import sys
import time
import threading

from cli.thread_client import HeadlessTurnOptions, HeadlessTurnResult
from cli.service_thread_client import ServiceThreadClient
from core.domain.approval import ApprovalStatus
from core.domain.common import new_id


def run_service_turn(
    options: HeadlessTurnOptions,
    *,
    on_event=None,
    decide_approval=None,
    detach=False,
    detach_requested=None,
) -> HeadlessTurnResult:
    prompt = options.prompt.strip()
    if not prompt:
        raise ValueError("prompt must not be empty")
    client = ServiceThreadClient(
        workspace=options.workspace,
        model=options.model,
        connection_id=options.connection_id,
        reasoning_effort=options.reasoning_effort,
        max_iterations=options.max_iterations,
        streaming=False,
        trust_workspace=options.trust_workspace,
        resume_id=options.resume_id,
        event_sink=on_event,
        surface="headless",
    )
    interrupted = False
    try:
        if options.access_preset is not None:
            client.set_access_preset(options.access_preset)
        if options.agent_preset is not None:
            client.set_agent_preset(options.agent_preset)
        skill_ids = tuple(
            client.skills.select(client.project.id, value).id
            for value in options.skill_identifiers
        )
        snapshot = client.turns.start(
            client.thread.id,
            prompt=prompt,
            message_id=new_id("tinp"),
            skill_ids=skill_ids,
            connection_id=options.connection_id,
            model=options.model,
            reasoning_effort=options.reasoning_effort,
        )
        if not detach:
            handled = set()
            while not snapshot.turn.status.is_terminal:
                if detach_requested is not None and detach_requested.is_set():
                    raise InterruptedError(
                        "Client detached; the task continues in the service"
                    )
                client.drain_events()
                for approval in snapshot.approvals:
                    if (
                        approval.status is ApprovalStatus.PENDING
                        and approval.id not in handled
                    ):
                        handled.add(approval.id)
                        decision = (
                            decide_approval(approval)
                            if decide_approval
                            else ApprovalStatus.DENIED
                        )
                        client.respond_to_approval(approval.id, decision)
                time.sleep(0.05)
                snapshot = client.turns.read(snapshot.turn.id)
            client.drain_events()
        return HeadlessTurnResult(snapshot.turn, client.thread.id, client.workspace)
    except KeyboardInterrupt:
        interrupted = True
        try:
            client.interrupt()
        except Exception:  # transport failure must not replace the user's exit 130
            print(
                "Task interruption could not be confirmed; reconnect to inspect its state.",
                file=sys.stderr,
            )
        raise
    finally:
        try:
            asyncio.run(client.close())
        except Exception:
            if not interrupted:
                raise
            print("The client connection could not finish closing.", file=sys.stderr)


async def run_service_turn_async(
    options: HeadlessTurnOptions, **kwargs
) -> HeadlessTurnResult:
    """Cancel the client waiter without cancelling an already admitted Turn."""
    detached = threading.Event()
    try:
        return await asyncio.to_thread(
            run_service_turn, options, detach_requested=detached, **kwargs
        )
    finally:
        detached.set()
