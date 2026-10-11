"""What the transcript shows, in what order, whatever the event timing."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from cli.transcript import TranscriptMode
from cli.tui.renderer import EventRenderer
from core.events import (
    AgentMessage,
    AgentMessageCompleted,
    AgentMessageDelta,
    AgentMessagePhase,
    AgentReasoningCompleted,
    AgentReasoningDelta,
    AgentReasoningStarted,
    Event,
    TaskComplete,
    ToolStarted,
    TurnStarted,
)
from core.reasoning import ReasoningAvailability, ReasoningChannel


def _renderer(mode=TranscriptMode.NORMAL):
    output = io.StringIO()
    console = Console(file=output, color_system=None, width=120, emoji=False)
    return EventRenderer(console, transcript_mode=mode), output


def _feed(renderer, *messages):
    for message in messages:
        renderer.on_event(Event("e", message))


def _lines(output) -> list[str]:
    return [line.rstrip() for line in output.getvalue().splitlines() if line.strip()]


def _reasoning_done():
    # The kernel completes reasoning only after the whole response streamed.
    return AgentReasoningCompleted(
        "r1",
        summary_text="",
        trace_text="Weigh the numbers first.",
        availability=ReasoningAvailability.AVAILABLE,
        effort="high",
        duration_ms=9000,
    )


def test_the_reasoning_card_precedes_the_answer_that_streamed_before_it_completed():
    renderer, output = _renderer()
    _feed(
        renderer,
        TurnStarted(),
        AgentReasoningStarted("r1", "high"),
        AgentReasoningDelta(
            "r1", ReasoningChannel.PROVIDER_TRACE, "Weigh the numbers first."
        ),
        AgentMessageDelta("The answer, line one.\n", "m1"),
    )
    assert renderer.status_line().strip().startswith("Responding")

    _feed(
        renderer,
        AgentMessageDelta("Line two.", "m1"),
        AgentMessageCompleted(
            "m1", "The answer, line one.\nLine two.", AgentMessagePhase.COMMENTARY
        ),
        _reasoning_done(),
        AgentMessage("The answer, line one.\nLine two.", "m1"),
        TaskComplete(None, "completed"),
    )

    lines = _lines(output)
    card = next(i for i, line in enumerate(lines) if "Thought for" in line)
    assert card < lines.index("The answer, line one.")
    # Timed when the answer began, not when the response ended (9s).
    assert "Thought for 9s" not in lines[card]
    assert sum("Thought for" in line for line in lines) == 1


def test_a_tool_call_settles_the_reasoning_card_first():
    renderer, output = _renderer()
    _feed(
        renderer,
        TurnStarted(),
        AgentReasoningStarted("r1", "high"),
        AgentReasoningDelta("r1", ReasoningChannel.PROVIDER_TRACE, "Read the file."),
        ToolStarted("c1", "read_file", "notes.txt"),
        _reasoning_done(),
    )

    lines = _lines(output)
    assert "Thought for" in lines[0]
    assert sum("Thought for" in line for line in lines) == 1


@pytest.mark.parametrize(
    ("start", "switch_to"),
    [
        (TranscriptMode.NORMAL, TranscriptMode.SUMMARY),
        (TranscriptMode.SUMMARY, TranscriptMode.NORMAL),
        (TranscriptMode.VERBOSE, TranscriptMode.SUMMARY),
    ],
)
def test_changing_detail_mid_answer_neither_repeats_nor_drops_it(start, switch_to):
    renderer, output = _renderer(start)
    text = "Line one.\n\nLine two.\n\nLine three.\n\nLine four."
    _feed(
        renderer,
        TurnStarted(),
        AgentMessageDelta("Line one.\n\n", "m1"),
        AgentMessageDelta("Line two.\n\n", "m1"),
    )
    renderer.set_transcript_mode(switch_to)  # Ctrl+O
    _feed(
        renderer,
        AgentMessageDelta("Line three.\n\n", "m1"),
        AgentMessageDelta("Line four.", "m1"),
        AgentMessageCompleted("m1", text, AgentMessagePhase.COMMENTARY),
        AgentMessage(text, "m1"),
        TaskComplete(None, "completed"),
    )

    lines = _lines(output)
    for line in ("Line one.", "Line two.", "Line three.", "Line four."):
        assert lines.count(line) == 1, lines


def test_the_next_answer_uses_the_newly_chosen_mode():
    renderer, output = _renderer(TranscriptMode.NORMAL)
    _feed(renderer, TurnStarted(), AgentMessageDelta("First.\n", "m1"))
    renderer.set_transcript_mode(TranscriptMode.SUMMARY)
    _feed(
        renderer,
        AgentMessage("First.", "m1"),
        TaskComplete(None, "completed"),
        TurnStarted(),
        AgentMessageDelta("Second, streamed unseen.\n", "m2"),
    )

    assert "Second, streamed unseen." not in output.getvalue()


def test_colon_words_are_printed_as_written():
    renderer = EventRenderer()  # the TUI's own console settings
    output = io.StringIO()
    renderer.console.file = output
    _feed(
        renderer,
        TurnStarted(),
        AgentMessageDelta("root:x:0:0 and :smile:\n", "m1"),
        ToolStarted("c1", "run_command", "grep root:x:0 /etc/passwd"),
    )

    assert "root:x:0:0 and :smile:" in output.getvalue()
    assert "grep root:x:0" in output.getvalue()
