"""Markdown for a transcript that is printed once and never redrawn.

The TUI prints into the terminal's own scrollback, so a line that is out can
never change. Codex's scrollback rule fits that: commit only what can no
longer change.

- A completed source line of prose, a heading, a list item or a quote is
  rendered as Markdown as soon as its newline arrives. Models write one
  paragraph per source line, so this is block rendering in practice.
- A code block's lines go out one by one, highlighted when the fence names a
  language. They get no indent beyond the stream's own (``indent``, which an
  answer does not have) and are never wrapped by DeepCode, so they copy back
  exactly as written.
- A table is held until its last row: a later row can widen every column.
- A line that carries a URL is left to the terminal's own soft wrap, so the
  address is not split by an inserted newline. Other prose wraps at spaces
  and between CJK characters (:func:`cli.tui.text.wrap_text`); a list item's
  or a quote's wrapped lines hang under its text.

In raw mode every source line is printed exactly as the model wrote it and
the terminal does all the wrapping (Codex's ``/raw``), for copying.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from rich.cells import cell_len
from rich.console import Console
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.text import Text

from cli.tui.text import wrap_text

_FENCE = re.compile(r"^(?P<indent> {0,3})(?P<marker>`{3,}|~{3,})(?P<info>[^`]*)$")
_TABLE_ROW = re.compile(r"^\s*\|")
_URL = re.compile(r"https?://\S")
# Blocks Rich lays out across the width it is given (a centred title, a
# full-width rule, a padded code block), wherever they are nested.
_LAID_OUT = frozenset({"heading_open", "hr", "code_block", "fence"})
# What Rich renders before the text of a line: a bar per quote level and a
# bullet or number per list level.
_BLOCK_MARKER = re.compile(r"(?:▌ | +(?:•|\d+) )*")
# Rich renders a line this wide, or twice its source if that is wider (a
# rendered line never is), so it never wraps one itself.
_UNWRAPPED_WIDTH = 4096
_CODE_THEME = "ansi_dark"  # ANSI colors, so the user's terminal theme applies


@dataclass(slots=True)
class _Fence:
    marker: str
    indent: int
    language: str


class MarkdownStream:
    """Turn complete source lines of one message segment into output."""

    def __init__(
        self,
        console: Console,
        *,
        raw: bool = False,
        before_output: Callable[[], None] | None = None,
        indent: int = 0,
        style: str | None = None,
    ) -> None:
        self.console = console
        self.raw = raw
        # Every printed line, code included, is indented by ``indent`` and
        # drawn in ``style``: a block that belongs under another one's mark.
        self.indent = indent
        self.style = style
        # Called before anything is printed: the renderer owns the blank line
        # that separates this segment from the block above it.
        self._before_output = before_output or (lambda: None)
        self._fence: _Fence | None = None
        self._table: list[str] = []
        self._printed = False
        self._blank_pending = False

    @property
    def held(self) -> list[str]:
        """Source lines waiting to be printed (a table still being written)."""
        return list(self._table)

    def feed_line(self, line: str) -> None:
        """Print what one completed source line settles."""
        if self.raw:
            if line.strip() or self._printed:
                self._print(Text(line), separate=False)
            return
        if self._fence is not None:
            self._code_line(line)
            return
        if self._table and not _TABLE_ROW.match(line):
            self._flush_table()
        fence = _FENCE.match(line)
        if fence:
            self._fence = _Fence(
                fence["marker"],
                len(fence["indent"]),
                fence["info"].strip().split(" ")[0],
            )
            # A code block stands apart from the prose around it.
            self._blank_pending = self._printed
            return
        if not line.strip():
            if self._printed:
                self._blank_pending = True
            return
        if _TABLE_ROW.match(line):
            self._table.append(line)
            return
        self._markdown(line)

    def finish(self) -> None:
        """End of the segment: print anything held and reset."""
        if self._table:
            self._flush_table()
        self._fence = None
        self._printed = False
        self._blank_pending = False

    # -- printing -------------------------------------------------------------

    def _print(self, text: Text, *, separate: bool = True) -> None:
        if not self._printed:
            self._before_output()
        elif separate and self._blank_pending:
            self.console.print()
        self._blank_pending = False
        self._printed = True
        if self.indent:
            text = Text(" " * self.indent) + text
        # soft_wrap: the text is already laid out (or deliberately left to
        # the terminal); DeepCode must not insert newlines into it.
        self.console.print(text, style=self.style, soft_wrap=True, highlight=False)

    def _code_line(self, line: str) -> None:
        fence = self._fence
        assert fence is not None
        stripped = line.strip()
        if stripped.startswith(
            fence.marker[0] * len(fence.marker)
        ) and not stripped.strip(fence.marker[0]):
            self._fence = None
            self._blank_pending = True
            return
        if fence.indent:
            # Content of an indented fence carries the fence's own indent.
            prefix = len(line) - len(line.lstrip(" "))
            line = line[min(prefix, fence.indent) :]
        self._print(self._highlight(line, fence.language))

    @staticmethod
    def _highlight(line: str, language: str) -> Text:
        if not language:
            return Text(line)
        text = Syntax(line, language, theme=_CODE_THEME).highlight(line)
        text.rstrip()
        return text

    def _flush_table(self) -> None:
        rows, self._table = self._table, []
        table = _parse("\n".join(rows))
        self._emit(self._render(table, self.console.width - self.indent))

    def _markdown(self, line: str) -> None:
        body = line.lstrip(" \t")
        nesting = len(line[: len(line) - len(body)].expandtabs(4))
        width = max(20, self.console.width - self.indent - nesting)
        markdown = _parse(body)
        unwrapped = max(_UNWRAPPED_WIDTH, 2 * cell_len(body))
        if any(token.type in _LAID_OUT for token in markdown.parsed):
            self._emit(self._render(markdown, width), nesting)
        elif _URL.search(body):
            self._emit(self._render(markdown, unwrapped), nesting)
        else:
            # Rich lays the line out unwrapped; the wrapping is DeepCode's.
            rendered = self._render(markdown, unwrapped)
            self._emit(
                [row for text in rendered for row in _wrap(text, width)], nesting
            )

    def _emit(self, lines: list[Text], nesting: int = 0) -> None:
        prefix = " " * nesting
        for index, text in enumerate(lines):
            if prefix:
                text = Text(prefix) + text
            self._print(text, separate=index == 0)

    def _render(self, markdown: Markdown, width: int) -> list[Text]:
        """Rich's lines for ``markdown`` laid out at ``width``, unframed."""
        options = self.console.options.update_width(width)
        rendered = self.console.render_lines(markdown, options, pad=False)
        lines = [
            Text.assemble(*((s.text, s.style) for s in line if not s.control))
            for line in rendered
        ]
        for text in lines:
            text.rstrip()
        # Rich frames some blocks (a list, a table) with blank lines; block
        # spacing here follows the source's own blank lines instead.
        while lines and not lines[0].plain.strip():
            lines.pop(0)
        while lines and not lines[-1].plain.strip():
            lines.pop()
        return lines


def _parse(source: str) -> Markdown:
    return Markdown(source, hyperlinks=False, code_theme=_CODE_THEME)


def _wrap(line: Text, width: int) -> list[Text]:
    """One rendered line of prose wrapped to ``width`` cells.

    The wrapped lines hang under the text: a list item's bullet or number
    becomes space, a quote's bars repeat.
    """
    marker = _BLOCK_MARKER.match(line.plain).end()
    head = line[:marker]
    hang = head.copy()
    hang.plain = re.sub(r"[^▌ ]", " ", head.plain)
    rows = wrap_text(line[marker:], width - head.cell_len)
    return [(head if index == 0 else hang) + row for index, row in enumerate(rows)]
