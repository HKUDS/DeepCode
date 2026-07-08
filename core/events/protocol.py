"""SQ/EQ protocol — the wire between any UI and the engine (L1).

Two decoupled directions, mirroring the pattern proven by Codex/Claude
Code (DEEPCODE_V2_MASTER_PLAN.md §4.2):

- **Submission Queue (UI → engine)** carries :class:`Submission` ``{id, op}``
  where :data:`Op` is a small, extensible command union (``UserInput`` /
  ``Interrupt`` / ``Shutdown``).
- **Event Queue (engine → UI)** carries :class:`Event` ``{id, msg}`` where
  :data:`EventMsg` is the lifecycle + streaming union the UI renders.

Every message carries a correlating ``id`` so a UI can tie events back to
the submission that produced them. The engine that consumes ``Op`` and emits
``EventMsg`` is :class:`~core.events.session.AgentSession`; nothing here
touches a model or a terminal — this is pure protocol.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Union

# --------------------------------------------------------------------------
# Submission Queue — commands the UI sends the engine.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class UserInput:
    text: str
    type: str = field(default="user_input", init=False)


@dataclass(frozen=True)
class Interrupt:
    """Ask the engine to abort the active turn."""

    type: str = field(default="interrupt", init=False)


@dataclass(frozen=True)
class Shutdown:
    type: str = field(default="shutdown", init=False)


Op = Union[UserInput, Interrupt, Shutdown]


@dataclass(frozen=True)
class Submission:
    id: str
    op: Op


# --------------------------------------------------------------------------
# Event Queue — what the engine emits back.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TurnStarted:
    type: str = field(default="turn_started", init=False)


@dataclass(frozen=True)
class AgentMessage:
    """A unit of assistant visible text (a turn's answer)."""

    text: str
    type: str = field(default="agent_message", init=False)


@dataclass(frozen=True)
class AgentMessageDelta:
    """A streaming increment of assistant text (emitted only when the
    session has streaming enabled).

    A delta sequence is always followed by the authoritative full
    ``AgentMessage``, so consumers may render deltas live and reconcile on
    the final text — or ignore deltas entirely (as headless NDJSON
    consumers do).
    """

    delta: str
    type: str = field(default="agent_message_delta", init=False)


@dataclass(frozen=True)
class ToolStarted:
    call_id: str
    name: str
    # Short human-readable argument summary ("pytest -q", "mathlib.py"),
    # so a frontend can render Claude Code-style `bash(pytest -q)` cards
    # without re-deriving it from raw arguments.
    detail: str = ""
    type: str = field(default="tool_started", init=False)


def summarize_call(name: str, arguments: dict[str, Any] | None) -> str:
    """One-line argument summary for a tool call (pure, best-effort).

    Picks the most informative argument by a preference order shared by all
    tools (command for bash, path for file tools, pattern for search), so
    frontends render consistent cards without per-tool special cases.
    """
    if not isinstance(arguments, dict) or not arguments:
        return ""
    for key in ("command", "file_path", "pattern", "prompt", "patch", "text"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            first_line = value.strip().splitlines()[0]
            return first_line[:80] + ("…" if len(first_line) > 80 else "")
    value = next(iter(arguments.values()))
    text = str(value).strip().splitlines()[0] if value is not None else ""
    return text[:80] + ("…" if len(text) > 80 else "")


@dataclass(frozen=True)
class ToolCompleted:
    call_id: str
    name: str
    is_error: bool
    type: str = field(default="tool_completed", init=False)


@dataclass(frozen=True)
class ErrorEvent:
    message: str
    type: str = field(default="error", init=False)


@dataclass(frozen=True)
class TaskComplete:
    final_text: str | None
    stop_reason: str
    type: str = field(default="task_complete", init=False)


@dataclass(frozen=True)
class ShutdownComplete:
    type: str = field(default="shutdown_complete", init=False)


EventMsg = Union[
    TurnStarted,
    AgentMessage,
    AgentMessageDelta,
    ToolStarted,
    ToolCompleted,
    ErrorEvent,
    TaskComplete,
    ShutdownComplete,
]


@dataclass(frozen=True)
class Event:
    id: str
    msg: EventMsg


def serialize_event(event: Event) -> dict[str, Any]:
    """Serialize an event to a plain dict (``msg.type`` discriminator kept).

    Suitable for NDJSON transport (``deepcode exec --json``) or an SSE frame.
    """
    return asdict(event)
