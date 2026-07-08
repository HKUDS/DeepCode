"""Event renderer — turns the SQ/EQ stream into terminal output.

Strictly a *consumer* of :data:`core.events.protocol.EventMsg` (§3
event-sourcing first: the UI never reaches into the kernel). One renderer
instance lives for the whole REPL; its only state is what streaming
reconciliation needs.

Rendering model (Claude Code semantics):

- ``agent_message_delta`` — printed immediately, plain, as it arrives
  (the live "typing" stream).
- ``tool_started`` — a bullet card ``● name(detail)``.
- ``tool_completed`` — an elbow line under the card: ``⎿ ✓`` / ``⎿ ✗``.
- ``agent_message`` — the authoritative final text. If its content already
  streamed as deltas it is not reprinted; otherwise (streaming off, or a
  provider that doesn't stream) it renders as markdown.
- ``task_complete`` / ``error`` — meta lines.
"""

from __future__ import annotations

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape

from cli.tui import theme
from core.events.protocol import Event


class EventRenderer:
    """Render events to a rich console, reconciling streamed deltas."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()
        self._streamed = ""  # text already shown as deltas this turn
        self._line_open = False  # a delta stream left the cursor mid-line

    # -- helpers -------------------------------------------------------------

    def _close_line(self) -> None:
        if self._line_open:
            self.console.print()
            self._line_open = False

    # -- event entrypoint -----------------------------------------------------

    def on_event(self, event: Event) -> None:
        msg = event.msg
        handler = getattr(self, f"_on_{msg.type}", None)
        if handler is not None:
            handler(msg)

    # -- per-type handlers ----------------------------------------------------

    def _on_turn_started(self, msg) -> None:
        self._streamed = ""
        self._line_open = False

    def _on_agent_message_delta(self, msg) -> None:
        # Live typing: print the raw delta without a newline.
        self.console.print(msg.delta, end="", soft_wrap=True, highlight=False)
        self._streamed += msg.delta
        self._line_open = True

    def _on_agent_message(self, msg) -> None:
        text = msg.text or ""
        if self._streamed and self._streamed.strip() == text.strip():
            # Already fully shown as deltas — just settle the line.
            self._close_line()
        else:
            self._close_line()
            self.console.print(Markdown(text))
        self.console.print()
        self._streamed = ""

    def _on_tool_started(self, msg) -> None:
        self._close_line()
        label = (
            f"[{theme.TOOL_RUNNING_STYLE}]{theme.TOOL_BULLET}[/] [bold]{msg.name}[/]"
        )
        if msg.detail:
            label += f"[{theme.TOOL_DETAIL_STYLE}]({msg.detail})[/]"
        self.console.print(label, highlight=False)

    def _on_tool_completed(self, msg) -> None:
        self._close_line()
        if msg.is_error:
            mark = f"[{theme.TOOL_ERR_STYLE}]{theme.DONE_ERR} {msg.name} failed[/]"
        else:
            mark = (
                f"[{theme.TOOL_OK_STYLE}]{theme.DONE_OK}[/] "
                f"[{theme.META_STYLE}]{msg.name}[/]"
            )
        # First line of the result, dimmed, next to the mark (Claude Code's
        # ⎿-result convention). getattr keeps older event shapes working.
        preview = getattr(msg, "result_preview", "") or ""
        first_line = preview.splitlines()[0] if preview else ""
        if first_line:
            snippet = first_line[:100] + ("…" if len(first_line) > 100 else "")
            mark += f"  [{theme.TOOL_DETAIL_STYLE}]{escape(snippet)}[/]"
        self.console.print(f"{theme.TOOL_RESULT_ELBOW} {mark}", highlight=False)

    def _on_error(self, msg) -> None:
        self._close_line()
        self.console.print(f"[{theme.ERROR_STYLE}]error:[/] {msg.message}")

    def _on_task_complete(self, msg) -> None:
        self._close_line()
        if msg.stop_reason != "completed":
            self.console.print(
                f"[{theme.META_STYLE}]· turn ended: {msg.stop_reason}[/]"
            )
        self._streamed = ""
