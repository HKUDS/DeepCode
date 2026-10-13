"""What ends a headless Turn's wait for its last agent event."""

from __future__ import annotations

from cli.headless_turn import ends_agent_turn
from core.events.protocol import ErrorEvent, TaskComplete


def test_a_warning_does_not_end_a_headless_turn():
    # A Skill's unknown allowed-tools name, for one, arrives as a warning
    # early in a Turn that goes on to finish normally.
    assert not ends_agent_turn(ErrorEvent("unknown tool name", warning=True))
    assert ends_agent_turn(ErrorEvent("the model call failed"))
    assert ends_agent_turn(TaskComplete(final_text="done", stop_reason="completed"))
