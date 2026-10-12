"""Markdown for a transcript that is printed once and never redrawn.

The TUI prints into the terminal's own scrollback, so a line that is out can
never change. Codex's scrollback rule fits that: commit only what can no
longer change.

- A completed source line of prose, a heading, a list item or a quote is
  rendered as Markdown as soon as its newline arrives. Models write one
  paragraph per source line, so this is block rendering in practice.
- A code block's lines go out one by one, highlighted when the fence names a
  language. They carry no indent and are never wrapped by DeepCode, so they
  copy back exactly as written.
- A table is held until its last row: a later row can widen every column.
- A line that carries a URL is left to the terminal's own soft wrap, so the
  address is not split by an inserted newline. Other prose wraps at word
  boundaries.

In raw mode every source line is printed exactly as the model wrote it and
the terminal does all the wrapping (Codex's ``/raw``), for copying.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from rich.console import Console
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.text import Text

_FENCE = re.compile(r"^(?P<indent> {0,3})(?P<marker>`{3,}|~{3,})(?P<info>[^`]*)$")
_TABLE_ROW = re.compile(r"^\s*\|")
_URL = re.compile(r"https?://\S")
_HEADING = re.compile(r"^\s*#{1,6}\s")
# Wide enough that no prose line wraps; the terminal soft-wraps it instead.
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
    ) -> None:
        self.console = console
        self.raw = raw
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
        # soft_wrap: the text is already laid out (or deliberately left to
        # the terminal); DeepCode must not insert newlines into it.
        self.console.print(text, soft_wrap=True, highlight=False)

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
        self._render("\n".join(rows), width=self.console.width)

    def _markdown(self, line: str) -> None:
        body = line.lstrip(" \t")
        indent = len(line[: len(line) - len(body)].expandtabs(4))
        width = max(20, self.console.width - indent)
        if _URL.search(body) and not _HEADING.match(body):
            width = _UNWRAPPED_WIDTH
        self._render(body, width=width, indent=indent)

    def _render(self, source: str, *, width: int, indent: int = 0) -> None:
        options = self.console.options.update_width(width)
        rendered = self.console.render_lines(
            Markdown(source, hyperlinks=False, code_theme=_CODE_THEME),
            options,
            pad=False,
        )
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
        prefix = " " * indent
        for index, text in enumerate(lines):
            if prefix:
                text = Text(prefix) + text
            self._print(text, separate=index == 0)
