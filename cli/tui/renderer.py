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
_SUBJECT_CHARS = 88


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
        self._stream_tail = ""  # unterminated tail of the delta stream
        self._completed_message_id: str | None = None
        self._completed_message_text = ""
        self._reasoning: dict[str, _ReasoningState] = {}
        self._active_reasoning_id: str | None = None
        # Presentation-only activity tracking for the status toolbar: the
        # label of the tool currently running, if any, and when it started.
        self._active_tool_label: str | None = None
        self._active_tool_started_at: float = 0.0
        # One blank line between blocks (tool card ↔ prose), owed lazily so
        # a turn that ends right after a card does not trail blank lines.
        self._gap_pending = False
        # Subject tails remembered per call_id so elbows can name the call
        # they close (concurrent tools complete out of order).
        self._tool_subjects: dict[str, str | None] = {}
        # Becomes True at the first visible streamed line of a segment;
        # leading blank lines a model emits before its prose are dropped
        # (block spacing is the renderer's job, via the gap).
        self._stream_body_started = False
        # The final message text already on screen this turn, kept for the
        # duplicate-suppression check in ``_on_error``.
        self._final_text_shown = ""

    def _emit_gap(self) -> None:
        if self._gap_pending:
            self.console.print()
            self._gap_pending = False

    def _emit_stream_line(self, line: str) -> None:
        if not self._stream_body_started and not line.strip():
            return
        self._stream_body_started = True
        self._emit_gap()
        # Default rich wrapping (word boundaries) instead of raw terminal
        # wrap, which used to split words mid-letter at the margin.
        self.console.print(line, highlight=False, markup=False)

    # -- helpers -------------------------------------------------------------

    def _close_line(self) -> None:
        if self._stream_tail:
            self._emit_stream_line(self._stream_tail)
            self._stream_tail = ""

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
            if self._active_tool_label is not None:
                # A long tool run is work too: keep the toolbar moving
                # instead of looking idle for its whole duration.
                elapsed = max(0, int(monotonic() - self._active_tool_started_at))
                return f" Running {self._active_tool_label} · {elapsed}s "
            return (
                f" Transcript: {self.transcript_mode.value} · "
                f"Ctrl+O changes detail · Esc interrupts "
            )
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
        self._stream_tail = ""
        self._completed_message_id = None
        self._completed_message_text = ""
        self._reasoning.clear()
        self._active_reasoning_id = None
        self._active_tool_label = None
        self._gap_pending = False
        self._tool_subjects.clear()
        self._stream_body_started = False
        self._final_text_shown = ""

    def _on_skill_loaded(self, msg) -> None:
        if self.transcript_mode is TranscriptMode.SUMMARY:
            return
        self._close_line()
        invocation = msg.invocation
        self.console.print(
            f"[{theme.META_STYLE}]{theme.THINKING_MARK} Skill {escape(invocation.name)} "
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
        # Whole lines only. prompt_toolkit's stdout proxy buffers an
        # unterminated tail and interleaves it badly with prompt redraws —
        # the first lines of a streamed reply used to be swallowed. Emitting
        # complete lines atomically (tail held until its newline or the
        # segment settles) is redraw-safe. ``markup=False`` because
        # assistant text is content, not markup — bracketed spans like
        # "[a-z]" used to be silently eaten by the markup parser.
        self._stream_tail += msg.delta
        while "\n" in self._stream_tail:
            line, self._stream_tail = self._stream_tail.split("\n", 1)
            self._emit_stream_line(line)

    def _on_agent_message_completed(self, msg) -> None:
        """Settle one streamed response segment without duplicating it later."""

        if self.transcript_mode is not TranscriptMode.SUMMARY:
            if self._streamed.strip() == (msg.text or "").strip():
                self._close_line()
            else:
                self._close_line()
                self._emit_gap()
                self.console.print(Markdown(msg.text or ""))
        self._completed_message_id = msg.message_id
        self._completed_message_text = msg.text or ""
        self._final_text_shown = msg.text or ""
        self._streamed = ""
        self._stream_body_started = False

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
            self._emit_gap()
            self.console.print(Markdown(text))
        self.console.print()
        self._final_text_shown = text
        self._streamed = ""
        self._gap_pending = False
        self._stream_body_started = False
        self._completed_message_id = None
        self._completed_message_text = ""

    def _on_agent_reasoning_summary(self, msg) -> None:
        """Render legacy completed summary events."""

        if self.transcript_mode is TranscriptMode.SUMMARY:
            return
        self._close_line()
        self.console.print(
            f"[{theme.META_STYLE}]{theme.THINKING_MARK} Thinking summary[/]"
        )
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
        self._emit_gap()
        duration = _duration_label(msg.duration_ms)
        effort = (msg.effort or "auto").title()
        mark = theme.THINKING_MARK
        label = (
            f"{mark} Thought for {duration}"
            if duration
            else f"{mark} Thinking completed"
        )
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

    @staticmethod
    def _tool_heading(msg) -> tuple[str, str | None]:
        """Presentation label + subject for one tool call.

        Prefers the provider-neutral :class:`ToolActivity` descriptor the
        kernel already computes (clean labels like ``Read`` / ``Run``), and
        falls back to the raw tool name for older event shapes. Neither the
        header nor the elbow may wrap: commands keep their HEAD (how they
        start is the informative part), everything else — paths — keeps its
        TAIL (the file name is the informative part).
        """
        activity = getattr(msg, "activity", None)
        label = getattr(activity, "label", None) or msg.name
        kind = getattr(getattr(activity, "kind", None), "value", None)
        subject = getattr(activity, "subject", None) or getattr(msg, "detail", None)
        if subject:
            subject = str(subject)
            if len(subject) > _SUBJECT_CHARS:
                if kind == "run":
                    subject = subject[: _SUBJECT_CHARS - 1] + "…"
                else:
                    subject = "…" + subject[-(_SUBJECT_CHARS - 1) :]
        return str(label), (subject if subject else None)

    @staticmethod
    def _subject_tail(subject: str | None) -> str | None:
        """Short recognizable tail of a subject (a file name, a command head)
        so a settling elbow can say WHICH call it closes — with concurrent
        same-tool calls the elbows were indistinguishable otherwise."""
        if not subject:
            return None
        tail = subject.rstrip("/").rsplit("/", 1)[-1] or subject
        if len(tail) > 32:
            tail = tail[:31] + "…"
        return tail

    def _on_tool_started(self, msg) -> None:
        label, subject = self._tool_heading(msg)
        self._active_tool_label = label
        self._active_tool_started_at = monotonic()
        call_id = getattr(msg, "call_id", None)
        if call_id is not None:
            self._tool_subjects[str(call_id)] = self._subject_tail(subject)
        if self.transcript_mode is TranscriptMode.SUMMARY:
            return
        self._close_line()
        if self._streamed and not self._gap_pending:
            # Prose ran straight into this card; give it breathing room.
            self._gap_pending = True
        self._emit_gap()
        line = (
            f"[{theme.TOOL_RUNNING_STYLE}]{theme.TOOL_BULLET}[/] "
            f"[bold]{escape(label)}[/]"
        )
        if subject:
            line += f" [{theme.TOOL_DETAIL_STYLE}]{escape(subject)}[/]"
        self.console.print(line, highlight=False)

    def _on_tool_completed(self, msg) -> None:
        self._active_tool_label = None
        subject_tail = self._tool_subjects.pop(str(getattr(msg, "call_id", None)), None)
        if self.transcript_mode is TranscriptMode.SUMMARY:
            return
        self._close_line()
        if msg.is_error:
            head = f"{theme.DONE_ERR} {msg.name} failed"
            mark = f"[{theme.TOOL_ERR_STYLE}]{escape(head)}[/]"
        else:
            head = f"{theme.DONE_OK} {msg.name}"
            mark = (
                f"[{theme.TOOL_OK_STYLE}]{theme.DONE_OK}[/] "
                f"[{theme.META_STYLE}]{escape(msg.name)}[/]"
            )
        if subject_tail:
            head += f" {subject_tail}"
            mark += f" [{theme.TOOL_DETAIL_STYLE}]{escape(subject_tail)}[/]"
        # First line of the result, dimmed, next to the mark (the ⎿-result
        # elbow — DeepCode's signature two-line tool rhythm). The elbow is a
        # SINGLE line: the snippet is cut to the remaining terminal width, so
        # a long error path can never wrap into an unindented mess below.
        preview = getattr(msg, "result_preview", "") or ""
        first_line = preview.splitlines()[0] if preview else ""
        if first_line:
            used = len(theme.TOOL_RESULT_ELBOW) + 1 + len(head) + 2
            budget = max(24, self.console.width - used - 1)
            snippet = first_line[:budget] + ("…" if len(first_line) > budget else "")
            mark += f"  [{theme.TOOL_DETAIL_STYLE}]{escape(snippet)}[/]"
        self.console.print(f"{theme.TOOL_RESULT_ELBOW} {mark}", highlight=False)
        self._gap_pending = True

    def _on_error(self, msg) -> None:
        self._close_line()
        # Providers surface one failure through several events (assistant
        # message, error, task_complete). If this exact text is already on
        # screen as the turn's message, repeating it is noise — the
        # "turn ended: error" meta line still closes the story.
        text = (msg.message or "").strip()
        if text and text in {self._final_text_shown.strip(), self._streamed.strip()}:
            return
        # The message is content: an error containing "[/]" used to CRASH
        # the markup parser here.
        self.console.print(f"[{theme.ERROR_STYLE}]error:[/] {escape(msg.message)}")

    def _on_task_complete(self, msg) -> None:
        self._close_line()
        if msg.stop_reason != "completed":
            self.console.print(
                f"[{theme.META_STYLE}]· turn ended: {escape(str(msg.stop_reason))}[/]"
            )
        self._streamed = ""
        self._active_reasoning_id = None
        self._active_tool_label = None
