"""The TUI in a real terminal, against a real service and a scripted model."""

from __future__ import annotations

import json
import re
import time

from rich.text import Text as RichText

from cli.tui.text import wrap_text
from tests.e2e.fake_model import Pause, Reasoning, Reply, Text, ToolCall
from tests.e2e.terminal import COLUMNS, CTRL_C, CTRL_O, ESC

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


def test_a_markdown_answer_is_rendered_as_it_streams(deepcode):
    code = "value = compute(alpha, beta)  # stays on one line"
    deepcode.model.on(
        "show markdown",
        Reply(
            [
                Text(
                    "## Release notes\n\n"
                    "The **fast** path uses `cache`.\n"
                    "- first point\n- second point\n\n"
                    f"```python\n{code}\n```\n\n"
                    "| name | size |\n|---|---|\n| a.py | 12 |\n\n"
                    "Done."
                )
            ]
        ),
    )
    tui = deepcode.open_tui("--access", "read-only")

    tui.submit("show markdown")
    deepcode.wait_idle()

    screen = tui.screen()
    assert "Release notes" in screen and "## Release" not in screen
    assert "The fast path uses cache." in screen
    assert "**" not in screen and "```" not in screen
    assert "• first point" in screen and "• second point" in screen
    assert screen.count(code) == 1 and f"\n{code}\n" in screen  # column 0
    assert screen.count("a.py") == 1


def test_an_unfinished_line_is_previewed_then_printed_once(deepcode):
    opening = "Opening words of a long paragraph"
    deepcode.model.on(
        "preview please",
        Reply(
            [Text(opening), Pause(2.5), Text(" that finally ends.\nNext line.")],
            delay=0.03,
        ),
    )
    tui = deepcode.open_tui("--access", "read-only")

    tui.submit("preview please")
    # The service sends text in batches at most 50 ms apart, released by the
    # next piece of text, so the last words before the pause arrive after it.
    # The words sent earlier are on screen while the model is silent.
    tui.wait_for_screen("Opening words of a")
    assert "finally ends" not in tui.screen()

    deepcode.wait_idle()
    screen = tui.screen()
    assert screen.count(f"{opening} that finally ends.") == 1
    assert screen.count(opening) == 1  # the preview is gone


def test_a_long_url_is_never_split_by_an_inserted_newline(deepcode):
    url = "https://example.com/" + "/".join(f"segment-{i:02d}" for i in range(20))
    assert len(url) > 160  # wider than the test terminal
    deepcode.model.on("give me the link", Reply([Text(f"The link: {url} works.")]))
    tui = deepcode.open_tui("--access", "read-only")
    start = tui.mark()

    tui.submit("give me the link")
    deepcode.wait_idle()

    # The terminal wraps it on screen; the bytes keep it whole, so a copy
    # from the scrollback or a terminal that reflows gets the real address.
    assert f"The link: {url} works." in tui.text(start)


def test_chinese_text_fills_each_line_and_hangs_under_its_mark(deepcode):
    sentence = "先读取配置文件，确认 relic_offer 在哪里触发，再决定埋点放在哪个函数里。"
    deepcode.model.on(
        "wrap chinese",
        Reply([Reasoning(sentence * 4), Pause(0.3), Text("- " + sentence * 4)]),
    )
    tui = deepcode.open_tui("--access", "read-only")
    # Ctrl+O sent before the TUI reads the keyboard reaches the terminal
    # instead, which on macOS then discards the program's output.
    tui.wait_for_screen(r"Transcript: normal")
    tui.send(CTRL_O, 0.3)  # verbose: the whole reasoning, under its card
    tui.wait_for_screen(r"Transcript: verbose")

    tui.submit("wrap chinese")
    deepcode.wait_idle()
    tui.wait_for_screen(r"\n· [\d.]+s · ")  # the Turn's footer, after the answer

    lines = tui.screen().splitlines()
    card = next(i for i, line in enumerate(lines) if "Thought for" in line)
    thought = lines[card + 1 : lines.index("", card)]
    item = next(i for i, line in enumerate(lines) if line.startswith(" • 先读取"))
    answer = lines[item : lines.index("", item)]
    for block, mark in ((thought, "  "), (answer, " • ")):
        # Exactly the wrapped rows, the first after its mark, the rest under it.
        rows = wrap_text(RichText(sentence * 4), COLUMNS - len(mark))
        assert len(rows) >= 2
        hang = " " * len(mark)
        assert block == [
            (mark if index == 0 else hang) + row.plain for index, row in enumerate(rows)
        ]


def test_resuming_mid_answer_shows_each_line_once_in_order(deepcode):
    # Long enough that the second TUI attaches mid-answer even on a slow host.
    lines = _numbered_lines(40, "Resumed")
    deepcode.model.on("answer slowly", _slow_answer(lines, pause=0.25))
    first = deepcode.open_tui("--access", "read-only")
    session = re.search(r"Session ([0-9a-f]{8})", first.screen())[1]

    first.submit("answer slowly")
    first.wait_for(lines[6])
    second = deepcode.open_tui("--access", "read-only", "--resume", session)
    deepcode.wait_idle(timeout=30)
    second.wait_for_screen(r"\n· [\d.]+s · ")  # the Turn's footer

    # The lines streamed before it attached come first, then the rest; the
    # end of the Turn adds no second copy.
    screen = second.screen()
    assert [screen.count(line) for line in lines] == [1] * len(lines), screen
    positions = [screen.index(line) for line in lines]
    assert positions == sorted(positions)


def test_resuming_a_turn_that_waits_for_approval_shows_the_approval(deepcode):
    deepcode.model.on(
        "list the files",
        Reply([ToolCall("bash", {"command": "ls"})]),
        Reply([Text("Listed the files.")]),
    )
    first = deepcode.open_tui("--access", "ask")
    session = re.search(r"Session ([0-9a-f]{8})", first.screen())[1]
    first.submit("list the files")
    first.wait_for(r"Approval needed")

    second = deepcode.open_tui("--access", "ask", "--resume", session)
    second.wait_for_screen(r"Approval needed")
    # Answered here, as if it had been asked here.
    second.submit("y")
    deepcode.wait_idle(timeout=30)
    second.wait_for_screen(r"Listed the files\.")
    assert second.screen().count("Approval needed") == 1


def test_an_approval_waits_still_holds_typing_and_always_allow_is_narrow(deepcode):
    deepcode.model.on(
        "list the files",
        Reply([ToolCall("bash", {"command": "ls"})]),
        Reply([Text("Listed the files.")]),
    )
    deepcode.model.on(
        "list again",
        Reply([ToolCall("bash", {"command": "ls -la"})]),
        Reply([Text("Listed again.")]),
    )
    deepcode.model.on(
        "clean the build",
        Reply([ToolCall("bash", {"command": "rm -rf build"})]),
        Reply([Text("Left it alone.")]),
    )
    tui = deepcode.open_tui("--access", "ask")
    start = tui.mark()

    tui.submit("list the files")
    tui.wait_for(r"Approval needed · Run a command", since=start)
    tui.wait_for_screen(
        r"◆ Waiting for your approval · y allow once · a always allow `ls …`"
    )
    assert "allow? y/a/n" in tui.screen()

    # Text typed now is not sent to the model as a steer.
    tui.submit("what is this?")
    tui.wait_for(r"Answer the approval first", since=start)
    assert not any("what is this" in json.dumps(r) for r in deepcode.model.requests)
    tui.send(CTRL_C)  # clear the message that came back to the prompt

    tui.submit("a")
    tui.wait_for(r"Allowed `ls …` for the rest of this Session\.", since=start)
    tui.wait_for(r"Listed the files\.", since=start)
    deepcode.wait_idle()

    again = tui.mark()
    tui.submit("list again")
    tui.wait_for(r"Listed again\.", since=again)
    assert "Approval needed" not in tui.text(again)
    deepcode.wait_idle()

    later = tui.mark()
    tui.submit("clean the build")
    tui.wait_for(r"Approval needed · Run a command", since=later)
    tui.submit("n")
    tui.wait_for(r"Left it alone\.", since=later)
    deepcode.wait_idle()


def test_leaving_during_a_turn_says_it_keeps_running_and_how_to_return(deepcode):
    deepcode.model.on(
        "tell a long story",
        Reply([Text("The story begins.\n"), Pause(4), Text("The story ends.")]),
    )
    tui = deepcode.open_tui("--access", "read-only")

    tui.submit("tell a long story")
    tui.wait_for(r"The story begins\.")
    tui.send("\x04", 1.0)  # Ctrl-D
    tui.process.wait(timeout=20)

    text = tui.text()
    assert "The current task keeps running in the background service." in text
    assert re.search(r"Reattach:\s+deepcode --resume \S+", text)
    assert deepcode.status()["activeTurns"]
    deepcode.wait_idle()
