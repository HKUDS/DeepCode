"""Event renderer — turns the SQ/EQ stream into terminal output.

Strictly a *consumer* of :data:`core.events.protocol.EventMsg` (§3
event-sourcing first: the UI never reaches into the kernel). One renderer
instance lives for the whole REPL; its only state is what streaming
reconciliation needs.

Rendering model (Claude Code semantics, dsh grammar):

- ``agent_message_delta`` — each completed line is rendered as Markdown at
  its newline (a code block line by line, a table once its last row is in;
  see :mod:`cli.tui.markdown_stream`). The unfinished line is previewed
  above the status line until then.
- ``tool_started`` — a bullet card ``● Label subject``.
- ``tool_completed`` — an elbow line under the card: ``⎿ ✓`` / ``⎿ ✗``.
- ``plan_updated`` — the plan tool's checklist, one line per step.
- ``agent_message`` — the authoritative final text. If its content already
  streamed as deltas it is not reprinted; otherwise (streaming off, or a
  provider that doesn't stream) it renders as markdown.
- ``task_complete`` / ``error`` — meta lines, plus the turn's own footer
  (wall time and token usage, dsh's settled-turn metrics).

Two dsh rules shape what a line says. **A row is one line**: the collapsed
form never wraps, so every field is cut to a cell budget and the end that
carries the meaning is the end that survives. **The failure is the
summary**: a settled error shows its first error line, not a generic mark.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from time import monotonic

from rich.cells import cell_len
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.text import Text

from cli.transcript import TranscriptMode
from cli.tui import animation, theme
from cli.tui.markdown_stream import MarkdownStream
from cli.tui.text import fit_head, fit_tail, workspace_path
from core.events.protocol import Event
from core.reasoning import ReasoningAvailability, ReasoningChannel


_NORMAL_PREVIEW_CHARS = 240
_STATUS_DETAIL_CHARS = 72
_SUBJECT_CELLS = 88  # ceiling; the real budget is the terminal's width
_SUBJECT_TAIL_CELLS = 32  # the elbow's "which call is this" fragment
_ELBOW_MIN_CELLS = 24
_PREVIEW_ROWS = 3  # the unfinished answer shown above the status line
# Below a second, a duration is noise on every row; above it, it is the
# most useful thing the elbow can say.
_DURATION_FLOOR_SECONDS = 1.0


@dataclass(slots=True)
class _ReasoningState:
    effort: str | None
    started_at: float
    summary_text: str = ""
    trace_text: str = ""
    # The card went out when the model moved on to its answer or a tool;
    # the kernel's late completion must not print it a second time.
    shown: bool = False

    @property
    def display_text(self) -> str:
        return self.summary_text or self.trace_text


@dataclass(slots=True)
class _ToolCall:
    """What the header card said, kept until its elbow settles."""

    label: str
    kind: str | None
    subject: str | None
    started_at: float
    # True once this call has ever shared the terminal with another
    # in-flight call: only then does its elbow have to name which card it
    # closes (concurrent tools settle out of order).
    concurrent: bool = False
    # True when a dedicated card has already told this call's story (the
    # plan checklist), so the generic card and elbow must stay out of the
    # way — dsh's rule that a keyed tool view REPLACES the generic row.
    superseded: bool = False


@dataclass(slots=True)
class _TurnStats:
    """The settled-turn footer's material, accumulated from real events."""

    started_at: float = 0.0
    usage: dict[str, int] = field(default_factory=dict)

    def add_usage(self, usage: dict[str, int]) -> None:
        for key, value in usage.items():
            if isinstance(value, int) and not isinstance(value, bool):
                self.usage[key] = self.usage.get(key, 0) + value

    def _first(self, *keys: str) -> int:
        for key in keys:
            value = self.usage.get(key)
            if value:
                return value
        return 0

    @property
    def input_tokens(self) -> int:
        """Provider dialects disagree on the key; the number is the same."""
        return self._first("prompt_tokens", "input_tokens")

    @property
    def output_tokens(self) -> int:
        return self._first("completion_tokens", "output_tokens")


def _clean_line(line: str) -> str:
    """One line with its markdown punctuation stripped, for a status tail."""
    return re.sub(r"[*_`#>\[\]]", "", line).strip()


def _first_content_line(text: str, *, limit: int) -> str:
    for line in text.splitlines():
        clean = _clean_line(line)
        if clean:
            return fit_head(clean, limit)
    return ""


def _last_content_line(text: str, *, limit: int) -> str:
    """The newest complete thought, dsh's running-row summary.

    A settled reasoning block is summarised by its FIRST line (the thesis);
    a *running* one by its LAST (what the model is on now). Showing the
    first line while thinking froze the status detail on the opening
    sentence for the whole turn, which read as a hung UI.

    Walks back from the end instead of splitting the whole trace: this runs
    on every repaint of an animated status line, against a reasoning buffer
    that grows all turn, and the answer is almost always in the last line.
    """
    end = len(text.rstrip())
    while end > 0:
        start = text.rfind("\n", 0, end) + 1
        clean = _clean_line(text[start:end])
        if clean:
            return fit_head(clean, limit)
        end = start - 1
    return ""


def _compact_count(value: int) -> str:
    """``842`` · ``12.3k`` · ``1.2M`` — a token count at a glance."""
    if value < 1000:
        return str(value)
    if value < 1_000_000:
        return f"{value / 1000:.1f}k".replace(".0k", "k")
    return f"{value / 1_000_000:.1f}M".replace(".0M", "M")


def _elapsed_label(seconds: float) -> str:
    """``1.4s`` · ``18s`` · ``2m 05s`` — one wall-clock reading."""
    if seconds < 10:
        return f"{seconds:.1f}s"
    if seconds < 60:
        return f"{round(seconds)}s"
    minutes, remainder = divmod(round(seconds), 60)
    return f"{minutes}m {remainder:02d}s"


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
        workspace: str | None = None,
    ) -> None:
        self.console = console or Console(emoji=False)
        self.transcript_mode = transcript_mode
        # Paths are named the way the user would type them: relative to the
        # workspace they launched in. Absolute tool arguments read as noise
        # ("…orkbase/test_repo/.deepcode/tool-results/…" says nothing).
        self.workspace = workspace
        self._streamed = ""  # text already shown as deltas this turn
        self._stream_tail = ""  # unterminated tail of the delta stream
        self._completed_message_id: str | None = None
        self._completed_message_text = ""
        self._reasoning: dict[str, _ReasoningState] = {}
        self._active_reasoning_id: str | None = None
        # In-flight tool calls keyed by call_id, in start order: the status
        # line reads the newest, each elbow pops its own.
        self._tool_calls: dict[str, _ToolCall] = {}
        # One blank line between blocks (tool card ↔ prose), owed lazily so
        # a turn that ends right after a card does not trail blank lines.
        self._gap_pending = False
        # The plan last drawn, so a tool that re-states an unchanged
        # checklist does not redraw it.
        self._plan_signature: tuple[tuple[str, str], ...] | None = None
        self._stats = _TurnStats()
        # Turn-level status: a turn spends most of its life waiting for the
        # provider, with no tool and no reasoning to name. That wait is
        # still work and the status line has to say so.
        self._turn_active = False
        self._streaming_message = False
        # Becomes True at the first visible streamed line of a segment;
        # leading blank lines a model emits before its prose are dropped
        # (block spacing is the renderer's job, via the gap).
        self._stream_body_started = False
        # The final message text already on screen this turn, kept for the
        # duplicate-suppression check in ``_on_error``.
        self._final_text_shown = ""
        # The transcript mode a message segment started in. It holds until
        # the segment settles: Ctrl+O mid-answer would otherwise reprint what
        # already streamed (into summary) or drop it (out of summary).
        self._segment_mode: TranscriptMode | None = None
        self._completed_message_mode: TranscriptMode | None = None
        # The streamed segment's Markdown state (open code block, held table).
        self._markdown: MarkdownStream | None = None
        # /raw: print the model's text exactly as written and let the
        # terminal wrap it, for copying.
        self.raw_output = False

    def _emit_gap(self) -> None:
        if self._gap_pending:
            self.console.print()
            self._gap_pending = False

    def _open_stream_body(self) -> None:
        self._stream_body_started = True
        self._emit_gap()

    def _emit_stream_line(self, line: str) -> None:
        if self._markdown is None:
            self._markdown = MarkdownStream(
                self.console, raw=self.raw_output, before_output=self._open_stream_body
            )
        self._markdown.feed_line(line)

    def _print_text(self, text: str, *, gap: bool = True) -> None:
        """Print a whole message that did not stream, by the same rules."""
        stream = MarkdownStream(
            self.console,
            raw=self.raw_output,
            before_output=self._emit_gap if gap else None,
        )
        for line in text.split("\n"):
            stream.feed_line(line)
        stream.finish()

    # -- helpers -------------------------------------------------------------

    def _close_line(self) -> None:
        """End the streamed segment: print its held tail, table or block."""
        if self._stream_tail:
            self._emit_stream_line(self._stream_tail)
            self._stream_tail = ""
        if self._markdown is not None:
            self._markdown.finish()
            self._markdown = None

    def _settle_streamed(self, text: str) -> bool:
        """Finish a streamed segment against its authoritative text.

        Returns True when the terminal now shows ``text``. Printed lines can
        never be rewritten, so a final text that merely extends what streamed
        (the stream ended early, or its last chunk was still in flight) is
        completed by printing only the missing suffix. Anything else is left
        to the caller, which prints the full text.
        """

        streamed = self._streamed
        if not streamed:
            return False
        if streamed.strip() == text.strip():
            self._close_line()
            return True
        if not text.startswith(streamed):
            return False
        self._stream_tail += text[len(streamed) :]
        while "\n" in self._stream_tail:
            line, self._stream_tail = self._stream_tail.split("\n", 1)
            self._emit_stream_line(line)
        self._close_line()
        return True

    def _begin_block(self) -> None:
        """Settle the stream and open one blank line before a card.

        Every card (tool, plan, reasoning) starts here, so block spacing is
        one rule in one place instead of three near-copies that drift.
        """
        self._close_line()
        if self._streamed and not self._gap_pending:
            # Prose ran straight into this card; give it breathing room.
            self._gap_pending = True
        self._emit_gap()

    def set_transcript_mode(self, mode: str | TranscriptMode) -> TranscriptMode:
        self.transcript_mode = (
            mode if isinstance(mode, TranscriptMode) else TranscriptMode.parse(mode)
        )
        return self.transcript_mode

    def cycle_transcript_mode(self) -> str:
        mode = self.set_transcript_mode(self.transcript_mode.next())
        return mode.value

    # -- status surface -------------------------------------------------------

    def _activity(self) -> tuple[str, float, str] | None:
        """The one thing the status line reports: ``(label, elapsed,
        detail)``, or ``None`` when no turn is running.

        Ordered most concrete first. A running tool is what the user is
        actually waiting on; failing that a live reasoning block; failing
        that the answer arriving; and failing all three, the turn itself —
        because "the model has not answered yet" is still work, and a
        status line that goes blank there reads as a hung UI. That last
        state is the common one: a turn spends most of its life waiting
        for the first token.
        """
        if self._tool_calls:
            # The newest call is the one in front; the older ones are on
            # screen as cards already.
            call = next(reversed(self._tool_calls.values()))
            extra = len(self._tool_calls) - 1
            detail = call.subject or ""
            if extra > 0:
                detail = (
                    f"{detail} (+{extra} running)" if detail else f"+{extra} running"
                )
            return (call.label, max(0.0, monotonic() - call.started_at), detail)
        reasoning_id = self._active_reasoning_id
        if reasoning_id is not None:
            state = self._reasoning.get(reasoning_id)
            if state is not None:
                effort = (state.effort or "auto").title()
                detail = _last_content_line(
                    state.display_text,
                    limit=_STATUS_DETAIL_CHARS,
                )
                return (
                    "Thinking",
                    max(0.0, monotonic() - state.started_at),
                    " · ".join(part for part in (effort, detail) if part),
                )
        if not self._turn_active:
            return None
        turn_elapsed = max(0.0, monotonic() - (self._stats.started_at or monotonic()))
        label = "Responding" if self._streaming_message else "Working"
        return (label, turn_elapsed, "")

    def _idle_status(self) -> str:
        return (
            f"Transcript: {self.transcript_mode.value} · "
            f"Ctrl+O changes detail · Esc interrupts"
        )

    def is_working(self) -> bool:
        """Whether the status line currently has motion to show.

        O(1) and side-effect free: the input layer asks this several times a
        second to decide whether the prompt needs an animation repaint at
        all. An idle TUI must cost nothing.
        """
        return bool(self._tool_calls or self._active_reasoning_id or self._turn_active)

    def settle_turn(self) -> None:
        """Stop reporting activity for a turn that ended off the event stream.

        An interrupted Turn reaches its terminal state in the durable
        record, but no terminal *event* follows it to this sink — the
        cancelled task never emits one. The caller that requested the
        interrupt is the one that knows, so it says so here instead of the
        status line spinning forever on a turn that is already over.
        """
        # Print the held partial line now, so the caller's status note lands
        # below the interrupted text instead of above it.
        self._close_line()
        self._turn_active = False
        self._streaming_message = False
        self._tool_calls.clear()
        self._active_reasoning_id = None
        self._segment_mode = None
        self._completed_message_mode = None

    def status_line(self) -> str:
        """The status as plain text (no motion) — pipes, tests, fallbacks."""
        activity = self._activity()
        if activity is None:
            return f" {self._idle_status()} "
        label, elapsed, detail = activity
        parts = [label, f"{int(elapsed)}s"]
        if detail:
            parts.append(detail)
        return f" {' · '.join(parts)} "

    def preview_lines(self) -> list[str]:
        """The answer text that is written but not yet in the transcript.

        A line reaches the transcript at its newline (a table at its last
        row), and a model can spend many seconds on one long paragraph. Until
        then its newest rows show here, above the status line, so the answer
        is readable as it arrives and the TUI never looks stuck (Codex shows
        the unfinished line the same way).
        """
        if (
            not self._streaming_message
            or self._segment_mode is TranscriptMode.SUMMARY
            or self.raw_output
        ):
            return []
        pending = self._markdown.held if self._markdown is not None else []
        if self._stream_tail.strip():
            pending.append(self._stream_tail)
        width = max(20, self.console.width - 2)
        rows: list[str] = []
        for line in pending:
            text = Text(line.replace("\r", "").replace("\t", "    "))
            rows.extend(row.plain for row in text.wrap(self.console, width))
        return [f" {row}" for row in rows[-_PREVIEW_ROWS:]]

    def status_fragments(self) -> list[tuple[str, str]]:
        """The status as prompt_toolkit fragments — the animated surface.

        Idle is a still, recessed line. Work is a spinner plus the
        activity's own word under dsh's glare sweep, then the detail: the
        two signals a terminal can carry that something is alive without
        redrawing the transcript.
        """
        activity = self._activity()
        if activity is None:
            return [(theme.STATUS_BASE_PTK, f" {self._idle_status()} ")]
        label, elapsed, detail = activity
        fragments: list[tuple[str, str]] = [
            (theme.STATUS_PREVIEW_PTK, f"{row}\n") for row in self.preview_lines()
        ]
        fragments.append(
            (theme.STATUS_SPINNER_PTK, f" {animation.spinner_frame(elapsed)} ")
        )
        fragments.extend(
            animation.shimmer(
                label,
                elapsed,
                base_style=theme.STATUS_LABEL_PTK,
                glare_style=theme.STATUS_GLARE_PTK,
            )
        )
        tail = f" · {int(elapsed)}s"
        if detail:
            tail += f" · {detail}"
        fragments.append((theme.STATUS_DETAIL_PTK, tail + " "))
        return fragments

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
        # The line above is the user's own message (the prompt echoes it);
        # the turn's first block is a new speaker and owes a blank line.
        self._gap_pending = True
        self._tool_calls.clear()
        self._stream_body_started = False
        self._final_text_shown = ""
        self._plan_signature = None
        self._stats = _TurnStats(started_at=monotonic())
        self._turn_active = True
        self._streaming_message = False
        self._segment_mode = None
        self._completed_message_mode = None

    def _on_model_usage_recorded(self, msg) -> None:
        """Accumulate provider usage for the settled-turn footer."""
        usage = getattr(msg, "usage", None)
        if isinstance(usage, dict):
            self._stats.add_usage(usage)

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
        self._settle_reasoning()
        self._streamed += msg.delta
        self._streaming_message = True
        if self._segment_mode is None:
            self._segment_mode = self.transcript_mode
        if self._segment_mode is TranscriptMode.SUMMARY:
            return
        # Whole lines only. prompt_toolkit's stdout proxy buffers an
        # unterminated tail and interleaves it badly with prompt redraws —
        # the first lines of a streamed reply used to be swallowed. Emitting
        # complete lines atomically (tail held until its newline or the
        # segment settles) is redraw-safe. The text goes through the
        # Markdown parser, never Rich markup — bracketed spans like "[a-z]"
        # used to be silently eaten by the markup parser.
        self._stream_tail += msg.delta
        while "\n" in self._stream_tail:
            line, self._stream_tail = self._stream_tail.split("\n", 1)
            self._emit_stream_line(line)

    def _on_agent_message_completed(self, msg) -> None:
        """Settle one streamed response segment without duplicating it later."""

        self._settle_reasoning()
        mode = self._segment_mode or self.transcript_mode
        if mode is not TranscriptMode.SUMMARY:
            if not self._settle_streamed(msg.text or ""):
                self._close_line()
                self._print_text(msg.text or "")
        self._completed_message_id = msg.message_id
        self._completed_message_text = msg.text or ""
        self._completed_message_mode = mode
        self._final_text_shown = msg.text or ""
        self._streamed = ""
        self._stream_body_started = False
        self._streaming_message = False
        self._segment_mode = None

    def _on_agent_message(self, msg) -> None:
        self._settle_reasoning()
        text = msg.text or ""
        already_completed = (
            msg.message_id is not None
            and msg.message_id == self._completed_message_id
            and text == self._completed_message_text
        )
        mode = (
            self._completed_message_mode if already_completed else self._segment_mode
        ) or self.transcript_mode
        if mode is TranscriptMode.SUMMARY:
            self._close_line()
            self._print_text(text, gap=False)
        elif already_completed:
            self._close_line()
        elif self._settle_streamed(text):
            # Shown as deltas, completed with any missing suffix.
            pass
        else:
            self._close_line()
            self._print_text(text)
        self.console.print()
        self._final_text_shown = text
        self._streamed = ""
        self._gap_pending = False
        self._stream_body_started = False
        self._streaming_message = False
        self._completed_message_id = None
        self._completed_message_text = ""
        self._completed_message_mode = None
        self._segment_mode = None

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
        if state.shown or self.transcript_mode is TranscriptMode.SUMMARY:
            return
        state.shown = True
        self._print_reasoning_card(
            state, _duration_label(msg.duration_ms), msg.availability
        )

    def _settle_reasoning(self) -> None:
        """Show the live reasoning block once the model moves on.

        The kernel completes a reasoning block only when the whole response
        has ended, after its answer streamed. Waiting for that put the
        "Thought for" card below the answer and kept the status line on
        "Thinking" while the answer arrived. The model stopped thinking when
        it began to answer or called a tool, so the card is printed then,
        timed to that moment; the late completion only updates the record.
        """
        reasoning_id = self._active_reasoning_id
        if reasoning_id is None:
            return
        self._active_reasoning_id = None
        state = self._reasoning.get(reasoning_id)
        if state is None or state.shown or not state.display_text:
            return
        state.shown = True
        if self.transcript_mode is TranscriptMode.SUMMARY:
            return
        elapsed_ms = int(max(0.0, monotonic() - state.started_at) * 1000)
        self._print_reasoning_card(
            state, _duration_label(elapsed_ms), ReasoningAvailability.AVAILABLE
        )

    def _print_reasoning_card(
        self,
        state: _ReasoningState,
        duration: str | None,
        availability: ReasoningAvailability,
    ) -> None:
        self._begin_block()
        effort = (state.effort or "auto").title()
        mark = theme.THINKING_MARK
        label = (
            f"{mark} Thought for {duration}"
            if duration
            else f"{mark} Thinking completed"
        )
        label += f" · {effort}"
        self.console.print(f"[{theme.META_STYLE}]{label}[/]")
        # Whatever this card prints below belongs to it; the next block —
        # the answer's prose — is a new one and owes a blank line.
        self._gap_pending = True
        if availability is ReasoningAvailability.OPAQUE:
            self.console.print(
                f"[{theme.META_STYLE}]Details were not provided by this model.[/]"
            )
            return

        if self.transcript_mode is TranscriptMode.NORMAL:
            # Collapsed, the block is summarised by its FIRST line — dsh's
            # settled Think row — indented under its own mark so the card
            # reads as one object rather than two stray lines.
            preview = _first_content_line(
                state.display_text,
                limit=_NORMAL_PREVIEW_CHARS,
            )
            if preview:
                self.console.print(
                    f"  [{theme.THINKING_STYLE}]{escape(preview)}[/]",
                    highlight=False,
                )
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

    def _tool_heading(self, msg) -> tuple[str, str | None, str | None]:
        """Presentation ``(label, kind, subject)`` for one tool call.

        Prefers the provider-neutral :class:`ToolActivity` descriptor the
        kernel already computes (clean labels like ``Read`` / ``Run``), and
        falls back to the raw tool name for older event shapes. Neither the
        header nor the elbow may wrap, so the subject is cut to what is left
        of the terminal — commands keep their HEAD (how they start is the
        informative part), paths keep their TAIL (the file name is), and a
        path inside the workspace is named the way the user would type it.
        """
        activity = getattr(msg, "activity", None)
        label = str(getattr(activity, "label", None) or msg.name)
        kind = getattr(getattr(activity, "kind", None), "value", None)
        raw = getattr(activity, "subject", None) or getattr(msg, "detail", None)
        if not raw:
            return label, kind, None
        subject = str(raw)
        if kind != "run":
            subject = workspace_path(subject, self.workspace)
        # bullet + space + label + space, then a column of slack at the edge.
        used = 2 + cell_len(label) + 1
        budget = min(_SUBJECT_CELLS, max(20, self.console.width - used - 1))
        subject = (
            fit_head(subject, budget) if kind == "run" else fit_tail(subject, budget)
        )
        return label, kind, (subject or None)

    @staticmethod
    def _subject_tail(kind: str | None, subject: str | None) -> str | None:
        """The short fragment an elbow uses to name the card it closes.

        Concurrent calls settle out of order, so an elbow that says only
        "✓" is unattributable. Which fragment identifies a call depends on
        what the call is: a command is recognised by how it STARTS, a path
        by its last segment.
        """
        if not subject:
            return None
        if kind == "run":
            return fit_head(subject, _SUBJECT_TAIL_CELLS)
        tail = subject.rstrip("/").rsplit("/", 1)[-1] or subject
        return fit_tail(tail, _SUBJECT_TAIL_CELLS)

    def _on_tool_started(self, msg) -> None:
        self._settle_reasoning()
        label, kind, subject = self._tool_heading(msg)
        call_id = str(
            getattr(msg, "call_id", None) or f"anonymous-{len(self._tool_calls)}"
        )
        self._tool_calls[call_id] = _ToolCall(
            label=label,
            kind=kind,
            subject=subject,
            started_at=monotonic(),
        )
        if len(self._tool_calls) > 1:
            # From here on every in-flight call shares the transcript, so
            # each of their elbows has to name itself.
            for call in self._tool_calls.values():
                call.concurrent = True
        if self.transcript_mode is TranscriptMode.SUMMARY:
            return
        if kind == "plan":
            # The checklist card that follows is this call's real card; a
            # generic "● Update plan" header above it would announce the
            # same thing twice. It still shows in the status line, and the
            # generic form comes back if the plan never arrives (below).
            return
        self._begin_block()
        line = (
            f"[{theme.TOOL_RUNNING_STYLE}]{theme.TOOL_BULLET}[/] "
            f"[bold]{escape(label)}[/]"
        )
        if subject:
            line += f" [{theme.TOOL_DETAIL_STYLE}]{escape(subject)}[/]"
        self.console.print(line, highlight=False)

    def _on_tool_completed(self, msg) -> None:
        call = self._tool_calls.pop(str(getattr(msg, "call_id", None)), None)
        if self.transcript_mode is TranscriptMode.SUMMARY:
            return
        if call is not None and call.superseded and not msg.is_error:
            # The plan card already settled this call. A failure still gets
            # the generic elbow: the checklist never arrived, so nothing
            # else on screen says what went wrong.
            return
        self._close_line()
        # The elbow — DeepCode's signature second line — answers three
        # questions and nothing else: did it work, which card is this (only
        # when more than one was in flight), and what came back. The tool's
        # own name is NOT among them on success: the card above already said
        # it, in the label the user reads. A failure keeps the name, because
        # "bash failed" is the failure.
        parts: list[str] = []
        if call is not None and (call.concurrent or self._tool_calls):
            tail = self._subject_tail(call.kind, call.subject)
            if tail:
                parts.append(escape(tail))
        if call is not None:
            elapsed = monotonic() - call.started_at
            if elapsed >= _DURATION_FLOOR_SECONDS:
                parts.append(_elapsed_label(elapsed))
        preview = getattr(msg, "result_preview", "") or ""
        first_line = preview.splitlines()[0].strip() if preview else ""

        if msg.is_error:
            head = f"{theme.DONE_ERR} {msg.name} failed"
            mark = f"[{theme.TOOL_ERR_STYLE}]{escape(head)}[/]"
        else:
            head = theme.DONE_OK
            mark = f"[{theme.TOOL_OK_STYLE}]{theme.DONE_OK}[/]"
        rendered = " · ".join(parts)
        if rendered:
            head += f" {rendered}"
            mark += f" [{theme.TOOL_DETAIL_STYLE}]{rendered}[/]"
        # The elbow is a SINGLE line: the result snippet is cut to the width
        # left over, so a long error path can never wrap into an unindented
        # mess below.
        if first_line:
            used = cell_len(theme.TOOL_RESULT_ELBOW) + 1 + cell_len(head) + 3
            budget = max(_ELBOW_MIN_CELLS, self.console.width - used - 1)
            snippet = fit_head(first_line, budget)
            # A dot separates two facts; a space separates a mark from the
            # one fact it carries ("✓ total 0", but "✗ bash failed · …").
            separator = " · " if (rendered or msg.is_error) else " "
            mark += f"{separator}[{theme.TOOL_DETAIL_STYLE}]{escape(snippet)}[/]"
        self.console.print(f"{theme.TOOL_RESULT_ELBOW} {mark}", highlight=False)
        self._gap_pending = True

    def _on_plan_updated(self, msg) -> None:
        """The plan tool's checklist — dsh's todo row, one line per step.

        Without this the plan tool was invisible in the TUI: the model kept
        a checklist the user could not see.
        """
        steps = tuple(getattr(msg, "plan", ()) or ())
        signature = tuple(
            (str(step.step), getattr(step.status, "value", str(step.status)))
            for step in steps
        )
        # The plan tool's own call is settled by this card, whether or not
        # the checklist itself changed.
        for call in self._tool_calls.values():
            if call.kind == "plan":
                call.superseded = True
        if not signature or signature == self._plan_signature:
            return
        self._plan_signature = signature
        if self.transcript_mode is TranscriptMode.SUMMARY:
            return
        self._begin_block()
        done = sum(1 for _step, status in signature if status == "completed")
        self.console.print(
            f"[{theme.TOOL_RUNNING_STYLE}]{theme.TOOL_BULLET}[/] "
            f"[bold]{theme.PLAN_LABEL}[/] "
            f"[{theme.META_STYLE}]{done}/{len(signature)}[/]",
            highlight=False,
        )
        indent = " " * (cell_len(theme.TOOL_RESULT_ELBOW) + 1)
        budget = max(_ELBOW_MIN_CELLS, self.console.width - cell_len(indent) - 3)
        for index, (step, status) in enumerate(signature):
            if status == "completed":
                glyph, style = theme.PLAN_STEP_DONE, theme.META_STYLE
            elif status == "in_progress":
                glyph, style = theme.PLAN_STEP_ACTIVE, theme.PLAN_ACTIVE_STYLE
            else:
                glyph, style = theme.PLAN_STEP_PENDING, theme.META_STYLE
            lead = f"{theme.TOOL_RESULT_ELBOW} " if index == 0 else indent
            self.console.print(
                f"{lead}[{style}]{glyph} {escape(fit_head(step, budget))}[/]",
                highlight=False,
            )
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
        if msg.warning:
            self.console.print(f"[{theme.WARNING}]warning:[/] {escape(msg.message)}")
            return
        self.console.print(f"[{theme.ERROR_STYLE}]error:[/] {escape(msg.message)}")

    def _turn_footer(self) -> str:
        """dsh's settled-turn metrics, from events the kernel already emits.

        Wall time and provider token counts only — no throughput: the turn's
        clock includes tool execution, so tokens-per-second computed over it
        would be a made-up number wearing a real one's clothes.
        """
        parts: list[str] = []
        if self._stats.started_at:
            parts.append(_elapsed_label(max(0.0, monotonic() - self._stats.started_at)))
        if self._stats.input_tokens:
            parts.append(f"{_compact_count(self._stats.input_tokens)} in")
        if self._stats.output_tokens:
            parts.append(f"{_compact_count(self._stats.output_tokens)} out")
        # Time alone is what any stopwatch shows; the footer earns its line
        # once the provider actually reported usage.
        return " · ".join(parts) if self._stats.usage else ""

    def _on_task_complete(self, msg) -> None:
        self._close_line()
        if msg.stop_reason != "completed":
            self.console.print(
                f"[{theme.META_STYLE}]· turn ended: {escape(str(msg.stop_reason))}[/]"
            )
        if self.transcript_mode is not TranscriptMode.SUMMARY:
            footer = self._turn_footer()
            if footer:
                self.console.print(
                    f"[{theme.META_STYLE}]· {footer}[/]", highlight=False
                )
        self._streamed = ""
        self._active_reasoning_id = None
        self._tool_calls.clear()
        self._turn_active = False
        self._streaming_message = False
        self._segment_mode = None
        self._completed_message_mode = None
