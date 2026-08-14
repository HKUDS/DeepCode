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

import re
from dataclasses import dataclass
from time import monotonic

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape

from cli.transcript import TranscriptMode
from cli.tui import theme
from core.events.protocol import Event
from core.reasoning import ReasoningAvailability, ReasoningChannel


_NORMAL_PREVIEW_CHARS = 240
_STATUS_DETAIL_CHARS = 72


@dataclass(slots=True)
class _ReasoningState:
    effort: str | None
    started_at: float
    summary_text: str = ""
    trace_text: str = ""

    @property
    def display_text(self) -> str:
        return self.summary_text or self.trace_text


def _first_content_line(text: str, *, limit: int) -> str:
    for line in text.splitlines():
        clean = re.sub(r"[*_`#>\[\]]", "", line).strip()
        if clean:
            return clean[:limit] + ("…" if len(clean) > limit else "")
    return ""


def _duration_label(duration_ms: int | None) -> str | None:
    if duration_ms is None:
        return None
    seconds = max(0, round(duration_ms / 1000))
    if seconds < 60:
        return f"{seconds}s"
    minutes, remainder = divmod(seconds, 60)
    return f"{minutes}m {remainder:02d}s"


class EventRenderer:
    """Render events to a rich console, reconciling streamed deltas."""

    def __init__(
        self,
        console: Console | None = None,
        *,
        transcript_mode: TranscriptMode = TranscriptMode.NORMAL,
    ) -> None:
        self.console = console or Console()
        self.transcript_mode = transcript_mode
        self._streamed = ""  # text already shown as deltas this turn
        self._line_open = False  # a delta stream left the cursor mid-line
        self._completed_message_id: str | None = None
        self._completed_message_text = ""
        self._reasoning: dict[str, _ReasoningState] = {}
        self._active_reasoning_id: str | None = None

    # -- helpers -------------------------------------------------------------

    def _close_line(self) -> None:
        if self._line_open:
            self.console.print()
            self._line_open = False

    def set_transcript_mode(self, mode: str | TranscriptMode) -> TranscriptMode:
        self.transcript_mode = (
            mode if isinstance(mode, TranscriptMode) else TranscriptMode.parse(mode)
        )
        return self.transcript_mode

    def cycle_transcript_mode(self) -> str:
        mode = self.set_transcript_mode(self.transcript_mode.next())
        return mode.value

    def status_line(self) -> str:
        reasoning_id = self._active_reasoning_id
        if reasoning_id is None:
            return f" Transcript: {self.transcript_mode.value} · Ctrl+O changes detail "
        state = self._reasoning.get(reasoning_id)
        if state is None:
            return ""
        elapsed = max(0, int(monotonic() - state.started_at))
        effort = (state.effort or "auto").title()
        detail = _first_content_line(
            state.display_text,
            limit=_STATUS_DETAIL_CHARS,
        )
        suffix = f" — {detail}" if detail else ""
        return f" Thinking · {effort} · {elapsed}s{suffix} "

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
        self._completed_message_id = None
        self._completed_message_text = ""
        self._reasoning.clear()
        self._active_reasoning_id = None

    def _on_skill_loaded(self, msg) -> None:
        if self.transcript_mode is TranscriptMode.SUMMARY:
            return
        self._close_line()
        invocation = msg.invocation
        self.console.print(
            f"[{theme.META_STYLE}]◇ Skill {escape(invocation.name)} "
            f"({invocation.kind.value})[/]"
        )

    def _on_skill_load_failed(self, msg) -> None:
        self._close_line()
        self.console.print(
            f"[{theme.ERROR_STYLE}]Skill error:[/] {escape(msg.message)}"
        )

    def _on_agent_message_delta(self, msg) -> None:
        self._streamed += msg.delta
        if self.transcript_mode is TranscriptMode.SUMMARY:
            return
        # Live typing: print the raw delta without a newline.
        self.console.print(msg.delta, end="", soft_wrap=True, highlight=False)
        self._line_open = True

    def _on_agent_message_completed(self, msg) -> None:
        """Settle one streamed response segment without duplicating it later."""

        if self.transcript_mode is not TranscriptMode.SUMMARY:
            if self._streamed.strip() == (msg.text or "").strip():
                self._close_line()
            else:
                self._close_line()
                self.console.print(Markdown(msg.text or ""))
        self._completed_message_id = msg.message_id
        self._completed_message_text = msg.text or ""
        self._streamed = ""

    def _on_agent_message(self, msg) -> None:
        text = msg.text or ""
        already_completed = (
            msg.message_id is not None
            and msg.message_id == self._completed_message_id
            and text == self._completed_message_text
        )
        if self.transcript_mode is TranscriptMode.SUMMARY:
            self._close_line()
            self.console.print(Markdown(text))
        elif already_completed:
            self._close_line()
        elif self._streamed and self._streamed.strip() == text.strip():
            # Already fully shown as deltas — just settle the line.
            self._close_line()
        else:
            self._close_line()
            self.console.print(Markdown(text))
        self.console.print()
        self._streamed = ""
        self._completed_message_id = None
        self._completed_message_text = ""

    def _on_agent_reasoning_summary(self, msg) -> None:
        """Render legacy completed summary events."""

        if self.transcript_mode is TranscriptMode.SUMMARY:
            return
        self._close_line()
        self.console.print(f"[{theme.META_STYLE}]◇ Thinking summary[/]")
        self.console.print(Markdown(msg.text), style=theme.META_STYLE)

    def _on_agent_reasoning_started(self, msg) -> None:
        self._reasoning[msg.reasoning_id] = _ReasoningState(
            effort=msg.effort,
            started_at=monotonic(),
        )
        self._active_reasoning_id = msg.reasoning_id

    def _on_agent_reasoning_delta(self, msg) -> None:
        state = self._reasoning.get(msg.reasoning_id)
        if state is None:
            state = _ReasoningState(effort=None, started_at=monotonic())
            self._reasoning[msg.reasoning_id] = state
        if msg.channel is ReasoningChannel.SUMMARY:
            state.summary_text += msg.delta
        else:
            state.trace_text += msg.delta
        self._active_reasoning_id = msg.reasoning_id

    def _on_agent_reasoning_completed(self, msg) -> None:
        state = self._reasoning.get(msg.reasoning_id)
        if state is None:
            state = _ReasoningState(
                effort=msg.effort,
                started_at=monotonic(),
            )
            self._reasoning[msg.reasoning_id] = state
        state.summary_text = msg.summary_text
        state.trace_text = msg.trace_text
        state.effort = msg.effort
        if self._active_reasoning_id == msg.reasoning_id:
            self._active_reasoning_id = None
        if self.transcript_mode is TranscriptMode.SUMMARY:
            return

        self._close_line()
        duration = _duration_label(msg.duration_ms)
        effort = (msg.effort or "auto").title()
        label = f"◇ Thought for {duration}" if duration else "◇ Thinking completed"
        label += f" · {effort}"
        self.console.print(f"[{theme.META_STYLE}]{label}[/]")
        if msg.availability is ReasoningAvailability.OPAQUE:
            self.console.print(
                f"[{theme.META_STYLE}]Details were not provided by this model.[/]"
            )
            return

        if self.transcript_mode is TranscriptMode.NORMAL:
            preview = _first_content_line(
                state.display_text,
                limit=_NORMAL_PREVIEW_CHARS,
            )
            if preview:
                self.console.print(f"[{theme.THINKING_STYLE}]{escape(preview)}[/]")
            return

        if state.summary_text:
            self.console.print(
                Markdown(state.summary_text),
                style=theme.THINKING_STYLE,
            )
        if state.trace_text and state.trace_text != state.summary_text:
            if state.summary_text:
                self.console.print(f"[{theme.META_STYLE}]Provider reasoning details[/]")
            self.console.print(
                Markdown(state.trace_text),
                style=theme.THINKING_STYLE,
            )

    def _on_tool_started(self, msg) -> None:
        if self.transcript_mode is TranscriptMode.SUMMARY:
            return
        self._close_line()
        label = (
            f"[{theme.TOOL_RUNNING_STYLE}]{theme.TOOL_BULLET}[/] [bold]{msg.name}[/]"
        )
        if msg.detail:
            label += f"[{theme.TOOL_DETAIL_STYLE}]({msg.detail})[/]"
        self.console.print(label, highlight=False)

    def _on_tool_completed(self, msg) -> None:
        if self.transcript_mode is TranscriptMode.SUMMARY:
            return
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
        self._active_reasoning_id = None
