"""Typed Turn command port; shares the original InteractiveTurnRouter."""

from __future__ import annotations

from cli.rpc_models import from_view
from core.application.turn_input_service import TurnInputReceipt
from core.application.turn_service import TurnSnapshot
from core.domain.turn import Turn


class ServiceTurns:
    def __init__(self, rpc):
        self.rpc = rpc

    def start(
        self,
        thread_id,
        *,
        prompt,
        message_id,
        skill_ids=(),
        client_surface=None,
        event_observer=None,
        connection_id=None,
        model=None,
        reasoning_effort=None,
    ):
        return from_view(
            TurnSnapshot,
            self.rpc.call(
                "turn/start",
                {
                    "threadId": thread_id,
                    "prompt": prompt,
                    "messageId": message_id,
                    "skills": list(skill_ids),
                    **(
                        {"connectionId": connection_id}
                        if connection_id is not None
                        else {}
                    ),
                    **({"model": model} if model is not None else {}),
                    **(
                        {"reasoningEffort": reasoning_effort}
                        if reasoning_effort is not None
                        else {}
                    ),
                },
            ),
        )

    def enqueue(
        self,
        thread_id,
        *,
        prompt,
        message_id,
        skill_ids=(),
        client_surface=None,
        event_observer=None,
    ):
        return from_view(
            TurnSnapshot,
            self.rpc.call(
                "turn/enqueue",
                {
                    "threadId": thread_id,
                    "prompt": prompt,
                    "messageId": message_id,
                    "skills": list(skill_ids),
                },
            ),
        )

    def steer(
        self, thread_id, *, expected_turn_id, prompt, message_id, client_surface=None
    ):
        return from_view(
            TurnInputReceipt,
            self.rpc.call(
                "turn/steer",
                {
                    "threadId": thread_id,
                    "expectedTurnId": expected_turn_id,
                    "prompt": prompt,
                    "messageId": message_id,
                },
            ),
        )

    def read(self, turn_id):
        return from_view(TurnSnapshot, self.rpc.call("turn/read", {"turnId": turn_id}))

    def list_for_thread(self, thread_id):
        turns = []
        while True:
            page = self.rpc.call(
                "turn/list", {"threadId": thread_id, "offset": len(turns), "limit": 100}
            )
            turns.extend(from_view(Turn, value) for value in page["turns"])
            if not page["hasMore"]:
                return turns
            if not page["turns"]:
                raise RuntimeError("Turn listing made no progress")

    def active_for_thread(self, thread_id):
        return self._first(thread_id, "active")

    def executing_for_thread(self, thread_id):
        return self._first(thread_id, "executing")

    def _first(self, thread_id, state):
        page = self.rpc.call(
            "turn/list", {"threadId": thread_id, "state": state, "limit": 1}
        )
        return from_view(Turn, page["turns"][0]) if page["turns"] else None

    def interrupt(self, thread_id, turn_id):
        value = self.rpc.call(
            "turn/interrupt", {"threadId": thread_id, "turnId": turn_id}
        )
        return value["accepted"], from_view(Turn, value["turn"])

    def retry(self, turn_id, *, use_current_selection=False):
        return from_view(
            TurnSnapshot,
            self.rpc.call(
                "turn/retry",
                {"turnId": turn_id, "useCurrentSelection": use_current_selection},
            ),
        )
