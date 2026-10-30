"""What the transcript shows, in what order, whatever the event timing."""

from __future__ import annotations

import io
import json

import pytest
from rich.console import Console

from cli.transcript import TranscriptMode
from cli.tui.renderer import EventRenderer
from core.agent_runtime.runtime import encode_structured_tool_result
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
from core.events.protocol import summarize_result
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


def test_structured_tool_results_reach_the_model_as_json_text():
    value = {
        "goal": {"objective": "Count files", "status": "active"},
        "canUpdate": True,
    }

    encoded = encode_structured_tool_result(value)

    assert isinstance(encoded, str)
    assert json.loads(encoded) == value
    # Content blocks and block lists are already in a provider's shape.
    block = {"type": "text", "text": "hello"}
    assert encode_structured_tool_result(block) is block
    blocks = [block]
    assert encode_structured_tool_result(blocks) is blocks
    assert encode_structured_tool_result("plain") == "plain"


def test_a_structured_tool_result_preview_reads_as_json():
    preview = summarize_result({"goal": {"status": "active"}, "canUpdate": True})

    assert preview == '{"goal": {"status": "active"}, "canUpdate": true}'


def test_a_long_result_preview_keeps_both_ends_at_line_breaks():
    lines = [f"line {index:03d} " + "x" * 20 for index in range(100)]
    preview = summarize_result("\n".join(lines), limit=400)

    head, elision, tail = preview.partition("\n…\n")
    assert elision
    assert head.startswith("line 000 ")
    assert tail.endswith("line 099 " + "x" * 20)
    # Neither end is cut mid-line.
    assert all(line in lines for line in head.splitlines() + tail.splitlines())
    assert len(preview) <= 400 + len("\n…\n")


def test_a_preview_cut_mid_line_says_so_at_the_cut():
    preview = summarize_result("x" * 500, limit=400)

    head, elision, tail = preview.split("\n")
    assert head.endswith("…") and elision == "…" and tail.startswith("…")


def test_a_preview_keeps_the_indentation_of_its_tail():
    # The tail's cut falls near a line break, before an indented frame.
    frames = "\n".join(["    frame()"] * 10)
    text = "head\n" + "x" * 500 + "\n" + frames + "\nValueError: bad"
    preview = summarize_result(text, limit=400)

    tail = preview.split("\n…\n")[1]
    assert tail.startswith("    frame()")


def test_a_preview_is_bounded_with_its_cut_marks():
    for size in (401, 500, 5000):
        assert len(summarize_result("y" * size, limit=400)) <= 405


def test_program_output_reads_as_a_terminal_shows_it():
    from cli.tui.text import terminal_text

    raw = "\x1b[32mok\x1b[0m\tdone\r\n10%\r50%\r100%\nend\x1b]0;title\x07"
    assert terminal_text(raw) == "ok  done\n100%\nend"
    # Escapes of every form, ended or cut short, never reach the terminal.
    for raw in (
        "bold\x1b(B plain",
        "\x1b[38:2:255:0:0mred\x1b[0m",
        "\x1b[>4;1mx",
        "\x1b7a\x1b8b\x1b=c",
        "text \x1b[3",
        "link \x1b]8;;http://example.com",
    ):
        assert "\x1b" not in terminal_text(raw)
    # A line a carriage return ends keeps its text.
    assert terminal_text("progress 100%\r\r\nnext") == "progress 100%\nnext"


def test_a_preview_for_people_leaves_out_the_models_notes():
    from core.agent_runtime.hook import ToolEvent
    from core.agent_runtime.runner import AgentRunner
    from core.agent_runtime.tools.model_notes import ERROR_HINT
    from core.events.session import display_preview

    # Program output may hold the hook mark itself; only the runner's own
    # hook context is left out, found where the runner recorded it.
    output = "step 1\n\n[hook] printed by the program\nstep 2 ran" + ERROR_HINT
    event: ToolEvent = {}
    composed = AgentRunner._compose_hook_context(output, ["ruff found 2 issues"], event)

    assert "ruff found 2 issues" in composed
    shown = display_preview(composed, hook_context_at=event["hook_context_at"])
    assert shown == "step 1\n\n[hook] printed by the program\nstep 2 ran"


def test_hook_context_keeps_a_structured_result_structured():
    from core.agent_runtime.hook import ToolEvent
    from core.agent_runtime.runner import AgentRunner
    from core.events.session import display_preview

    as_json = AgentRunner._compose_hook_context({"goal": "x"}, ["note"], {})
    assert as_json.startswith('{"goal": "x"}')

    image = {"type": "image_url", "image_url": {"url": "data:..."}}
    for result in ([image], image):
        event: ToolEvent = {}
        blocks = AgentRunner._compose_hook_context(result, ["note"], event)
        assert blocks[0] == image
        assert blocks[-1] == {"type": "text", "text": "[hook] note"}
        # People see the blocks without the hook's.
        shown = display_preview(blocks, hook_context_at=event["hook_context_at"])
        assert "[hook]" not in shown and "image_url" in shown


def test_a_rebuilt_preview_finds_hook_context_by_its_mark():
    from core.agent_runtime.tools.model_notes import (
        before_hook_context,
        with_hook_context,
    )

    assert before_hook_context(with_hook_context("output", ["note"])) == "output"
