"""Streamed answers render as Markdown without ever reprinting a line."""

from __future__ import annotations

import io

from rich.console import Console

from cli.transcript import TranscriptMode
from cli.tui.renderer import EventRenderer
from core.events import (
    AgentMessage,
    AgentMessageCompleted,
    AgentMessageDelta,
    AgentMessagePhase,
    Event,
    TaskComplete,
    TurnStarted,
)

URL = "https://example.com/docs/a/rather/long/path/that/keeps/going/and/going"


def _renderer(width=60, mode=TranscriptMode.NORMAL):
    output = io.StringIO()
    console = Console(file=output, color_system=None, width=width, emoji=False)
    return EventRenderer(console, transcript_mode=mode), output


def _feed(renderer, *messages):
    for message in messages:
        renderer.on_event(Event("e", message))


def _lines(output) -> list[str]:
    return [line.rstrip() for line in output.getvalue().splitlines()]


def _stream(renderer, *chunks, final=True):
    _feed(renderer, TurnStarted())
    _feed(renderer, *(AgentMessageDelta(chunk, "m1") for chunk in chunks))
    if final:
        text = "".join(chunks)
        _feed(
            renderer,
            AgentMessageCompleted("m1", text, AgentMessagePhase.FINAL_ANSWER),
            AgentMessage(text, "m1"),
            TaskComplete(None, "completed"),
        )


def test_streamed_markdown_is_rendered_not_shown_as_source():
    renderer, output = _renderer()
    _stream(
        renderer,
        "## Plan\n\nUse **bold** and `code` here.\n",
        "- first item\n- second item\n",
    )
    text = output.getvalue()
    assert "Plan" in text and "##" not in text
    assert "Use bold and code here." in text
    assert "**" not in text and "`" not in text
    assert "• first item" in text and "• second item" in text


def test_a_line_is_printed_at_its_newline_and_previewed_before():
    renderer, output = _renderer()
    _stream(renderer, "First **done** line.\nSecond li", final=False)
    assert "First done line." in output.getvalue()
    assert "Second li" not in output.getvalue()
    assert renderer.preview_lines() == [" Second li"]
    preview = "".join(text for _, text in renderer.status_fragments())
    assert preview.startswith(" Second li\n")

    _feed(renderer, AgentMessageDelta("ne arrives.\n", "m1"))
    assert "Second line arrives." in output.getvalue()
    assert renderer.preview_lines() == []


def test_list_items_are_not_separated_by_blank_lines():
    renderer, output = _renderer()
    _stream(renderer, "Steps:\n1. one\n2. two\n3. three\n")
    lines = [line for line in _lines(output) if line.strip()]
    start = lines.index("Steps:")
    assert [line.strip() for line in lines[start + 1 : start + 4]] == [
        "1 one",
        "2 two",
        "3 three",
    ]
    every = _lines(output)
    first = every.index("Steps:")
    last = next(i for i, row in enumerate(every) if "three" in row)
    assert "" not in every[first : last + 1]


def test_nested_list_items_keep_their_indent():
    renderer, output = _renderer()
    _stream(renderer, "- top\n  - nested\n")
    lines = [line for line in _lines(output) if line.strip()]
    top = next(line for line in lines if "top" in line)
    nested = next(line for line in lines if "nested" in line)
    indent = len(nested) - len(nested.lstrip())
    assert indent > len(top) - len(top.lstrip())


def test_code_lines_are_highlighted_unindented_and_never_wrapped():
    renderer, output = _renderer(width=40)
    code = "value = compute(alpha, beta, gamma)  # longer than forty columns"
    _stream(renderer, "Run this:\n```python\n", f"{code}\n", "```\nDone.\n")
    lines = _lines(output)
    assert code in lines  # one line, at column 0, exactly as written
    assert not any("```" in line for line in lines)
    assert lines.index(code) < next(i for i, row in enumerate(lines) if "Done." in row)


def test_code_lines_stream_before_the_block_closes():
    renderer, output = _renderer()
    _stream(renderer, "```\nfirst = 1\nsecond = 2\n", final=False)
    assert "first = 1" in _lines(output)
    assert "second = 2" in _lines(output)


def test_a_line_with_a_url_is_left_to_the_terminal_to_wrap():
    renderer, output = _renderer(width=40)
    _stream(renderer, f"See {URL} for details.\n")
    assert f"See {URL} for details." in _lines(output)


def test_a_markdown_link_shows_its_address():
    renderer, output = _renderer()
    _stream(renderer, f"Read [the docs]({URL}).\n")
    assert URL in output.getvalue()


def test_prose_without_a_url_wraps_at_word_boundaries():
    renderer, output = _renderer(width=40)
    sentence = "This sentence is long enough that it has to wrap at least once."
    _stream(renderer, sentence + "\n")
    lines = [line for line in _lines(output) if line.strip()]
    assert all(len(line) <= 40 for line in lines)
    assert " ".join(line.strip() for line in lines) == sentence


def test_a_table_waits_for_its_last_row():
    renderer, output = _renderer()
    _stream(renderer, "| name | size |\n|---|---|\n| a.py | 12 |\n", final=False)
    assert "a.py" not in output.getvalue()
    assert any("a.py" in row for row in renderer.preview_lines())

    _feed(renderer, AgentMessageDelta("| b.py | 7 |\nAfter the table.\n", "m1"))
    text = output.getvalue()
    assert "a.py" in text and "b.py" in text and "|---|" not in text
    assert text.index("b.py") < text.index("After the table.")


def test_a_table_at_the_end_of_the_answer_is_printed_once():
    renderer, output = _renderer()
    _stream(renderer, "| k | v |\n|---|---|\n| x | 1 |")
    assert output.getvalue().count("x") == 1


def test_blank_lines_separate_blocks_once_and_never_lead():
    renderer, output = _renderer()
    _stream(renderer, "\n\nFirst paragraph.\n\n\n\nSecond paragraph.\n")
    lines = _lines(output)
    first = lines.index("First paragraph.")
    second = lines.index("Second paragraph.")
    assert second - first == 2
    assert lines[first - 1] == ""  # the turn's own gap, not the model's


def test_an_answer_that_did_not_stream_renders_by_the_same_rules():
    streamed, streamed_output = _renderer()
    _stream(streamed, "## Head\n\n- **a**\n- b\n\n```python\nx = 1\n```\n")
    whole, whole_output = _renderer()
    text = "## Head\n\n- **a**\n- b\n\n```python\nx = 1\n```\n"
    _feed(
        whole, TurnStarted(), AgentMessage(text, "m1"), TaskComplete(None, "completed")
    )
    assert streamed_output.getvalue().strip() == whole_output.getvalue().strip()


def test_colon_words_and_brackets_stay_as_written():
    renderer, output = _renderer()
    _stream(renderer, "Exit code :x: for `root:x:0`, see [a-z] ranges.\n")
    text = output.getvalue()
    assert ":x:" in text and "root:x:0" in text and "[a-z]" in text


def test_raw_mode_prints_the_source_exactly_and_unwrapped():
    renderer, output = _renderer(width=40)
    renderer.raw_output = True
    source = [
        "## Plan",
        f"Use **bold** and {URL}",
        "```python",
        "x = 1",
        "```",
    ]
    _stream(renderer, "\n".join(source) + "\n")
    lines = _lines(output)
    for line in source:
        assert line in lines
    assert renderer.preview_lines() == []


def test_the_raw_command_toggles_and_sets_raw_output():
    import asyncio
    from types import SimpleNamespace

    from cli.tui import commands
    from cli.tui.app import TuiApp

    renderer, _ = _renderer()
    app = SimpleNamespace(renderer=renderer)
    app.set_raw_output = lambda enabled: TuiApp.set_raw_output(app, enabled)

    assert "raw output on" in asyncio.run(commands.dispatch(app, "/raw"))
    assert renderer.raw_output is True
    assert "raw output off" in asyncio.run(commands.dispatch(app, "/raw"))
    assert renderer.raw_output is False
    asyncio.run(commands.dispatch(app, "/raw on"))
    assert renderer.raw_output is True
    asyncio.run(commands.dispatch(app, "/raw off"))
    assert renderer.raw_output is False
    assert asyncio.run(commands.dispatch(app, "/raw maybe")) == "usage: /raw [on|off]"


def test_a_code_block_stands_apart_from_the_prose_around_it():
    renderer, output = _renderer()
    _stream(renderer, "Run this:\n```\nmake test\n```\nThen check.\n")
    lines = _lines(output)
    code = lines.index("make test")
    assert lines[code - 1] == "" and lines[code + 1] == ""


def test_raw_mode_drops_leading_blank_lines():
    renderer, output = _renderer()
    renderer.raw_output = True
    _stream(renderer, "\n\nFirst line.\n")
    lines = _lines(output)
    # Only the turn's own gap precedes the text, not the model's blank lines.
    assert lines[: lines.index("First line.")] == [""]


def test_chinese_prose_fills_each_line_and_keeps_english_words_whole():
    renderer, output = _renderer(width=40)
    _stream(
        renderer,
        "埋点：run_start、death、relic_offer 这些事件都要记录下来，"
        "并且在面板里展示出来以便调试。\n",
    )
    assert [line for line in _lines(output) if line] == [
        "埋点：run_start、death、relic_offer 这些",
        "事件都要记录下来，并且在面板里展示出来以",
        "便调试。",
    ]


def test_a_wrapped_list_item_or_quote_hangs_under_its_text():
    renderer, output = _renderer(width=40)
    _stream(
        renderer,
        "- 列表项：使用 Python 编写一个 Web 服务器，监听 8080 端口并返回 JSON。\n"
        "12. 第十二项，内容很长很长很长很长很长很长很长很长很长很长很长。\n"
        "> 引用：这是一段很长的引用文字，需要换行显示才能完整看到全部内容。\n",
    )
    assert [line for line in _lines(output) if line] == [
        " • 列表项：使用 Python 编写一个 Web 服务",
        "   器，监听 8080 端口并返回 JSON。",
        " 12 第十二项，内容很长很长很长很长很长很",
        "    长很长很长很长很长很长。",
        "▌ 引用：这是一段很长的引用文字，需要换行",
        "▌ 显示才能完整看到全部内容。",
    ]


def test_headings_and_rules_keep_the_terminal_width():
    renderer, output = _renderer(width=40)
    _stream(renderer, "# Title\n\n---\n\n> ## Quoted heading\n")
    title, rule, quoted = [line for line in _lines(output) if line]
    # Rich centres a title and draws a rule across the width it is given.
    assert title == "Title".center(40).rstrip()
    assert len(rule) == 40 and set(rule) <= {"-", "─"}
    assert quoted == "▌ Quoted heading"


def test_the_line_being_written_wraps_by_the_same_rule():
    renderer, _ = _renderer(width=40)
    _stream(
        renderer,
        "先看一下这个函数里面的处理逻辑handle_relic_offer然后再决定怎么修改它的返回值。",
        final=False,
    )
    assert renderer.preview_lines() == [
        " 先看一下这个函数里面的处理逻辑",
        " handle_relic_offer然后再决定怎么修改它",
        " 的返回值。",
    ]


def test_a_rule_or_heading_nested_in_markers_keeps_the_terminal_width():
    renderer, output = _renderer(width=40)
    _stream(renderer, "- > ---\n\n- > # Title\n\n1. 2. ---\n")
    lines = [line for line in _lines(output) if line]
    assert all(len(line) <= 40 for line in lines)
    assert any("Title" in line and "▌" in line for line in lines)


def test_wrapped_lines_hang_under_the_innermost_marker():
    renderer, output = _renderer(width=40)
    _stream(
        renderer,
        "- > 引用在列表里：这是一段很长的引用文字，需要换行显示才能完整看到。\n\n"
        "- - 嵌套的列表项：这也是一段很长的文字，需要换行显示才能完整看到。\n",
    )
    assert [line for line in _lines(output) if line] == [
        " • ▌ 引用在列表里：这是一段很长的引用文",
        "   ▌ 字，需要换行显示才能完整看到。",
        " •  • 嵌套的列表项：这也是一段很长的文",
        "      字，需要换行显示才能完整看到。",
    ]


def test_a_paragraph_wider_than_rich_is_given_still_wraps_by_word():
    renderer, output = _renderer(width=40)
    words = "abc_relic_offer_identifier"
    _stream(renderer, "- " + "这是很长的中文内容" * 227 + f"{words} 结尾。\n")
    rows = [line for line in _lines(output) if line]
    assert any(words in row for row in rows)
    assert all(row.startswith("   ") and len(row) <= 40 for row in rows[1:])
