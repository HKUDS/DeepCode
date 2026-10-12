"""The TUI in a real terminal, against a real service and a scripted model."""

from __future__ import annotations

import json
import re
import time

from tests.e2e.fake_model import Pause, Reasoning, Reply, Text, ToolCall
from tests.e2e.terminal import CTRL_C, CTRL_O, ESC

_INTERRUPTED = r"[Ii]nterrupt|[Ss]topped"


def _numbered_lines(count: int, topic: str) -> list[str]:
    return [f"{topic} line {index:02d} carries its own words" for index in range(count)]


def _slow_answer(lines: list[str], *, pause: float = 0.15) -> Reply:
    parts = []
    for line in lines:
        parts += [Text(line + "\n"), Pause(pause)]
    return Reply(parts)


def test_a_prompt_gets_its_answer(deepcode):
    deepcode.model.on("ping", Reply([Text("pong from the scripted model")]))
    tui = deepcode.open_tui("--access", "read-only")

    tui.submit("ping")

    tui.wait_for(r"pong from the scripted model")
    deepcode.wait_idle()


def test_a_long_streamed_answer_is_shown_exactly_once(deepcode):
    lines = _numbered_lines(40, "Streamed")
    deepcode.model.on("write forty lines", Reply([Text("\n".join(lines))], delay=0.002))
    tui = deepcode.open_tui("--access", "read-only")

    tui.submit("write forty lines")
    deepcode.wait_idle()

    screen = tui.screen()
    counts = {line: screen.count(line) for line in lines}
    assert all(count == 1 for count in counts.values()), counts


def test_esc_on_an_empty_prompt_interrupts_and_the_session_carries_on(deepcode):
    deepcode.model.on("tell a long story", _slow_answer(_numbered_lines(60, "Story")))
    deepcode.model.on("are you there", Reply([Text("still here after the stop")]))
    tui = deepcode.open_tui("--access", "read-only")
    start = tui.mark()

    tui.submit("tell a long story")
    tui.wait_for(r"Story line 03", since=start)
    tui.send(ESC, 0.5)

    deepcode.wait_idle(timeout=15)
    tui.wait_for(_INTERRUPTED, since=start)
    assert "Story line 59" not in tui.text(start)

    tui.submit("are you there")
    tui.wait_for(r"still here after the stop", since=start)
    deepcode.wait_idle()


def test_a_draft_is_never_interrupted_or_sent_by_esc_and_ctrl_c(deepcode):
    deepcode.model.on("tell another story", _slow_answer(_numbered_lines(60, "Saga")))
    tui = deepcode.open_tui("--access", "read-only")
    start = tui.mark()

    tui.submit("tell another story")
    tui.wait_for(r"Saga line 02", since=start)
    tui.send("half-typed draft", 0.3)

    tui.send(ESC, 0.8)  # with a draft, Esc must not stop the Turn
    assert deepcode.status()["activeTurns"] == 1

    tui.send(CTRL_C, 0.8)  # clears the draft, still does not stop the Turn
    assert deepcode.status()["activeTurns"] == 1

    tui.send(CTRL_C, 0.5)  # an empty prompt: now it stops
    deepcode.wait_idle(timeout=15)
    tui.wait_for(_INTERRUPTED, since=start)

    sent = [
        message
        for request in deepcode.model.requests
        for message in request["messages"]
        if message.get("role") == "user"
    ]
    assert not any("half-typed draft" in str(message) for message in sent)


def test_compact_then_the_conversation_carries_on(deepcode):
    deepcode.model.on("first question", Reply([Text("First answer, kept short.")]))
    deepcode.model.on("second question", Reply([Text("Second answer, also short.")]))
    deepcode.model.on("third question", Reply([Text("Third answer after compacting.")]))
    tui = deepcode.open_tui("--access", "read-only")
    start = tui.mark()

    tui.submit("first question")
    tui.wait_for(r"First answer", since=start)
    deepcode.wait_idle()
    tui.submit("second question")
    tui.wait_for(r"Second answer", since=start)
    deepcode.wait_idle()

    before_compact = tui.mark()
    tui.submit("/compact")
    tui.wait_for(r"[Cc]ompact", since=before_compact, timeout=30)
    deepcode.wait_idle()
    assert not re.search(r"[Ii]nternal error", tui.text(before_compact))

    tui.submit("third question")
    tui.wait_for(r"Third answer after compacting", since=before_compact)
    deepcode.wait_idle()


# -- what the transcript shows -------------------------------------------------


def test_startup_prints_no_http_request_logs(deepcode):
    tui = deepcode.open_tui("--access", "read-only")

    assert "HTTP Request" not in tui.text()


def test_colon_words_are_shown_as_written(deepcode):
    literal = "The entry root:x:0:0 and the word :smile: stay as written."
    deepcode.model.on("show the passwd line", Reply([Text(literal)]))
    tui = deepcode.open_tui("--access", "read-only")
    start = tui.mark()

    tui.submit("show the passwd line")
    deepcode.wait_idle()

    assert literal in tui.text(start)


def test_the_reasoning_card_comes_before_the_answer(deepcode):
    answer = _numbered_lines(8, "Reasoned")
    deepcode.model.on(
        "think first",
        Reply(
            [Reasoning("Weigh the numbers before answering."), Pause(0.4)]
            + _slow_answer(answer, pause=0.3).parts
        ),
    )
    tui = deepcode.open_tui("--access", "read-only")
    start = tui.mark()

    tui.submit("think first")
    deepcode.wait_idle()

    screen = tui.text(start)
    card, first_line = screen.find("Thought for"), screen.find(answer[0])
    assert 0 <= card < first_line, screen[-3000:]
    # While the answer streams, the status line says so, not "Thinking".
    assert "Thinking ·" not in screen[first_line:]


def test_ctrl_o_during_an_answer_neither_repeats_nor_drops_lines(deepcode):
    lines = _numbered_lines(16, "Toggled")
    answer = Reply(
        [part for line in lines for part in (Text(line + "\n\n"), Pause(0.2))]
    )
    deepcode.model.on("toggle while streaming", answer)
    deepcode.model.on("toggle back while streaming", answer)
    tui = deepcode.open_tui("--access", "read-only")

    # normal -> verbose -> summary, while the first lines are on screen.
    start = tui.mark()
    tui.submit("toggle while streaming")
    tui.wait_for(lines[3], since=start)
    tui.send(CTRL_O, 0.2)
    tui.send(CTRL_O, 0.2)
    deepcode.wait_idle()
    screen = tui.screen()
    assert {line: screen.count(line) for line in lines} == dict.fromkeys(lines, 1)

    # summary -> normal, while the first lines are streaming unseen.
    tui.submit("toggle back while streaming")
    deepcode.wait_active()
    time.sleep(1.0)
    tui.send(CTRL_O, 0.2)
    deepcode.wait_idle()
    screen = tui.screen()  # both answers are on it now
    assert {line: screen.count(line) for line in lines} == dict.fromkeys(lines, 2)


def test_tool_results_read_as_text_not_python_values(deepcode):
    deepcode.model.on(
        "Finish the scripted objective",
        Reply([ToolCall("get_goal", {})]),
        Reply([ToolCall("update_goal", {"status": "complete", "reason": "Scripted."})]),
        Reply([Text("The scripted objective is complete.")]),
    )
    tui = deepcode.open_tui("--access", "read-only")
    start = tui.mark()

    tui.submit("/goal Finish the scripted objective")
    tui.wait_for(r"The scripted objective is complete", since=start, timeout=40)
    deepcode.wait_idle()

    screen = tui.text(start)
    assert "⎿" in screen  # the tool cards are there ...
    assert "{'" not in screen  # ... and none of them prints a Python dict

    # The model got the Goal as JSON text, which every provider accepts,
    # not as the undocumented [dict] it used to be wrapped into.
    results = [
        message["content"]
        for request in deepcode.model.requests
        for message in request["messages"]
        if message.get("role") == "tool"
    ]
    assert results and all(isinstance(content, str) for content in results)
    assert json.loads(results[0])["goal"]["objective"] == (
        "Finish the scripted objective"
    )

