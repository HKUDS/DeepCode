"""Typed event & message vocabulary shared across the harness (P1, L0/L1).

Three cleanly separated layers, each a small discriminated union:

- :mod:`core.events.llm_events` — the *provider stream* vocabulary
  (text / reasoning / tool-call / usage / error). Every provider backend
  normalizes to this; the loop only knows this shape. (L0)
- :mod:`core.events.parts` — the *structured message* model (text /
  reasoning / tool state machine / step / patch). The persistable,
  streamable representation of a conversation. (L1)
- :mod:`core.events.protocol` — the *SQ/EQ* wire protocol
  (``Submission{id, op}`` / ``Event{id, msg}``) decoupling any UI from the
  engine, plus :class:`~core.events.session.AgentSession`. (L1)

Rule: these modules are pure data + small pure functions. No IO, no model
calls, no UI. They are the shared nouns the engine and every frontend agree
on.
"""

from core.events.llm_events import (
    LLMEvent,
    ReasoningDelta,
    StreamError,
    TextDelta,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    UsageInfo,
    llm_response_to_events,
    serialize_llm_event,
)
from core.events.parts import (
    Message,
    Part,
    ReasoningPart,
    TextPart,
    ToolPart,
    ToolState,
    messages_to_parts,
    serialize_message,
)
from core.events.protocol import (
    AgentMessage,
    Event,
    EventMsg,
    Interrupt,
    Op,
    Shutdown,
    Submission,
    TaskComplete,
    ToolCompleted,
    ToolStarted,
    TurnStarted,
    UserInput,
    serialize_event,
)
from core.events.session import AgentSession

__all__ = [
    # L0 — provider stream vocabulary
    "LLMEvent",
    "TextDelta",
    "ReasoningDelta",
    "ToolCallStart",
    "ToolCallDelta",
    "ToolCallEnd",
    "UsageInfo",
    "StreamError",
    "llm_response_to_events",
    "serialize_llm_event",
    # L1 — structured message parts
    "Message",
    "Part",
    "TextPart",
    "ReasoningPart",
    "ToolPart",
    "ToolState",
    "messages_to_parts",
    "serialize_message",
    # L1 — SQ/EQ protocol + session
    "Submission",
    "Event",
    "Op",
    "EventMsg",
    "UserInput",
    "Interrupt",
    "Shutdown",
    "TurnStarted",
    "AgentMessage",
    "ToolStarted",
    "ToolCompleted",
    "TaskComplete",
    "AgentSession",
    "serialize_event",
]
