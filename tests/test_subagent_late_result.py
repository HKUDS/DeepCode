"""Sub-agent results that lose the Turn-close delivery race are persisted.

The defect under test (control.py's old comment admitted it): in an
application-backed Session, a sub-agent finishing after its parent Turn
closed posted a result that survived only on ``SubAgent.result`` — the model
never saw it. The fix routes the lost result through ``context_note_sink``
as a ``between_turns`` canonical message WITHOUT syncing the runtime's
message count, so the next acquire sees the count mismatch, reloads visible
history, and the model reads the result at the start of its next Turn.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.agents.control import AgentControl


def _control(tmp_path: Path, **kwargs) -> AgentControl:
    return AgentControl(str(tmp_path), **kwargs)


def test_lost_race_routes_result_to_the_note_sink(tmp_path: Path) -> None:
    notes: list[tuple[str, str, bool]] = []
    control = _control(
        tmp_path,
        runtime_input_sink=lambda _msg: True,  # accepts, but…
        active_turn_id_provider=lambda: None,  # …no Turn is active
        context_note_sink=lambda content, source, *, already_in_history=True: (
            notes.append((content, source, already_in_history))
        ),
    )
    control._post("worker", "RESULT: finished after the turn closed")

    assert len(notes) == 1
    content, source, already_in_history = notes[0]
    assert "finished after the turn closed" in content
    assert source == "subagent"
    # NOT in the live history — the reload path must carry it, so the sink is
    # told not to sync the canonical message count.
    assert already_in_history is False


def test_mailbox_refusal_also_routes_to_the_note_sink(tmp_path: Path) -> None:
    notes: list[str] = []
    control = _control(
        tmp_path,
        runtime_input_sink=lambda _msg: False,  # active Turn, but refused
        active_turn_id_provider=lambda: "turn-1",
        context_note_sink=lambda content, source, *, already_in_history=True: (
            notes.append(content)
        ),
    )
    control._post("worker", "RESULT: refused by a closing mailbox")
    assert len(notes) == 1


def test_delivered_result_never_touches_the_late_path(tmp_path: Path) -> None:
    notes: list[str] = []
    delivered: list[object] = []
    control = _control(
        tmp_path,
        runtime_input_sink=lambda msg: (delivered.append(msg), True)[1],
        active_turn_id_provider=lambda: "turn-1",
        context_note_sink=lambda content, source, *, already_in_history=True: (
            notes.append(content)
        ),
    )
    control._post("worker", "RESULT: delivered normally")
    assert len(delivered) == 1
    assert notes == []


def test_sink_failure_does_not_raise_out_of_post(tmp_path: Path) -> None:
    def broken_sink(_c: str, _s: str, *, already_in_history: bool = True) -> None:
        raise OSError("disk full")

    control = _control(
        tmp_path,
        runtime_input_sink=lambda _msg: True,
        active_turn_id_provider=lambda: None,
        context_note_sink=broken_sink,
    )
    # _post runs inside _run's finally; an exception here would mask the
    # sub-agent's own outcome.
    control._post("worker", "RESULT: y")


def test_local_mailbox_path_is_untouched(tmp_path: Path) -> None:
    """CLI-direct sessions keep the local mailbox exactly as before — results
    there are never lost (the next drain collects them), so the late path
    must not interfere."""

    notes: list[str] = []
    control = _control(
        tmp_path,
        context_note_sink=lambda content, source, *, already_in_history=True: (
            notes.append(content)
        ),
    )
    control._post("worker", "RESULT: local")
    assert notes == []
    assert len(control._mailbox) == 1
