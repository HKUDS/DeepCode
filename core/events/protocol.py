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

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Union

from core.reasoning import ReasoningAvailability, ReasoningChannel
from core.skills.models import SkillInvocation, SkillSelection

# --------------------------------------------------------------------------
# Submission Queue — commands the UI sends the engine.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class UserInput:
    text: str
    skills: tuple[SkillSelection, ...] = ()
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
    skill_invocations: tuple[SkillInvocation, ...] = ()
    type: str = field(default="turn_started", init=False)


@dataclass(frozen=True)
class SkillLoaded:
    invocation: SkillInvocation
    type: str = field(default="skill_loaded", init=False)


@dataclass(frozen=True)
class SkillLoadFailed:
    message: str
    skill_id: str | None = None
    type: str = field(default="skill_load_failed", init=False)


class AgentMessagePhase(str, Enum):
    """User-visible role of one assistant message inside a Turn."""

    COMMENTARY = "commentary"
    FINAL_ANSWER = "final_answer"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class AgentMessage:
    """The authoritative final assistant message for a Turn.

    ``message_id`` correlates this final form with the item that streamed
    earlier. It remains optional so legacy AgentSession adapters can keep
    emitting the original ``AgentMessage(text)`` shape.
    """

    text: str
    message_id: str | None = None
    phase: AgentMessagePhase = AgentMessagePhase.FINAL_ANSWER
    type: str = field(default="agent_message", init=False)


@dataclass(frozen=True)
class AgentMessageDelta:
    """A streaming increment of assistant text (emitted only when the
    session has streaming enabled).

    ``message_id`` identifies one provider response item. It is optional only
    for compatibility with older session adapters; first-party streaming
    sessions always populate it.
    """

    delta: str
    message_id: str | None = None
    type: str = field(default="agent_message_delta", init=False)


@dataclass(frozen=True)
class AgentMessageCompleted:
    """A complete assistant message item, usually mid-Turn commentary."""

    message_id: str
    text: str
    phase: AgentMessagePhase = AgentMessagePhase.COMMENTARY
    type: str = field(default="agent_message_completed", init=False)


@dataclass(frozen=True)
class AgentReasoningSummary:
    """Legacy completed summary event retained for replay compatibility."""

    text: str
    type: str = field(default="agent_reasoning_summary", init=False)


@dataclass(frozen=True)
class AgentReasoningStarted:
    """One provider response began returning reasoning metadata."""

    reasoning_id: str
    effort: str | None = None
    type: str = field(default="agent_reasoning_started", init=False)


@dataclass(frozen=True)
class AgentReasoningDelta:
    """An incremental provider summary or provider trace."""

    reasoning_id: str
    channel: ReasoningChannel
    delta: str
    type: str = field(default="agent_reasoning_delta", init=False)


@dataclass(frozen=True)
class AgentReasoningCompleted:
    """Authoritative final reasoning item for one provider response."""

    reasoning_id: str
    summary_text: str = ""
    trace_text: str = ""
    availability: ReasoningAvailability = ReasoningAvailability.AVAILABLE
    effort: str | None = None
    duration_ms: int | None = None
    type: str = field(default="agent_reasoning_completed", init=False)


@dataclass(frozen=True)
class ModelUsageRecorded:
    """Incremental usage for one completed provider response in this Turn."""

    response_ordinal: int
    usage: dict[str, int]
    type: str = field(default="model_usage_recorded", init=False)


class PlanStepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass(frozen=True)
class PlanStep:
    step: str
    status: PlanStepStatus


@dataclass(frozen=True)
class PlanUpdated:
    """Structured TODO/checklist state emitted by the built-in plan tool."""

    plan: tuple[PlanStep, ...]
    explanation: str | None = None
    type: str = field(default="plan_updated", init=False)


def parse_plan_update(arguments: Mapping[str, Any]) -> PlanUpdated:
    """Validate and normalize one ``update_plan`` argument object.

    The tool and event bridge share this parser so validation and UI state can
    never drift apart.
    """

    raw = arguments.get("plan")
    if not isinstance(raw, list) or not raw:
        raise ValueError("'plan' must be a non-empty list of {step, status} items.")

    steps: list[PlanStep] = []
    in_progress = 0
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ValueError(
                f"plan[{index}] must be an object with 'step' and 'status'."
            )
        step = str(item.get("step", "")).strip()
        status_value = str(item.get("status", "")).strip()
        if not step:
            raise ValueError(f"plan[{index}].step is required.")
        try:
            status = PlanStepStatus(status_value)
        except ValueError as exc:
            allowed = ", ".join(status.value for status in PlanStepStatus)
            raise ValueError(
                f"plan[{index}].status must be one of {allowed} (got {status_value!r})."
            ) from exc
        if status is PlanStepStatus.IN_PROGRESS:
            in_progress += 1
        steps.append(PlanStep(step=step, status=status))

    if in_progress > 1:
        raise ValueError("at most one step may be in_progress at a time.")

    explanation = str(arguments.get("explanation") or "").strip() or None
    return PlanUpdated(plan=tuple(steps), explanation=explanation)


class ToolActivityKind(str, Enum):
    READ = "read"
    SEARCH = "search"
    LIST = "list"
    RUN = "run"
    EDIT = "edit"
    PLAN = "plan"
    OTHER = "other"


@dataclass(frozen=True)
class ToolActivity:
    """Small, provider-neutral presentation descriptor for a tool call."""

    kind: ToolActivityKind
    label: str
    subject: str = ""


def _first_argument(
    arguments: Mapping[str, Any] | None,
    *keys: str,
    limit: int = 160,
) -> str:
    if not isinstance(arguments, Mapping):
        return ""
    for key in keys:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            first_line = value.strip().splitlines()[0]
            return first_line[:limit] + ("…" if len(first_line) > limit else "")
    return ""


def describe_tool_activity(
    name: str,
    arguments: Mapping[str, Any] | None,
) -> ToolActivity:
    """Map built-in tool contracts to stable UI semantics in one place."""

    lowered = name.strip().lower()
    if lowered in {"read", "view_image"}:
        return ToolActivity(
            ToolActivityKind.READ,
            "Read",
            _first_argument(arguments, "file_path", "path"),
        )
    if lowered in {"grep", "search"}:
        return ToolActivity(
            ToolActivityKind.SEARCH,
            "Search",
            _first_argument(arguments, "pattern", "query", "path"),
        )
    if lowered in {"glob", "list", "list_files"}:
        return ToolActivity(
            ToolActivityKind.LIST,
            "List",
            _first_argument(arguments, "pattern", "path"),
        )
    if lowered in {"bash", "exec", "execute_bash", "execute_commands"}:
        return ToolActivity(
            ToolActivityKind.RUN,
            "Run",
            _first_argument(arguments, "command"),
        )
    if lowered in {"write", "edit", "apply_patch"}:
        return ToolActivity(
            ToolActivityKind.EDIT,
            "Edit",
            _first_argument(arguments, "file_path", "path", "patch"),
        )
    if lowered in {"update_plan", "plan"}:
        return ToolActivity(ToolActivityKind.PLAN, "Update plan")
    return ToolActivity(
        ToolActivityKind.OTHER,
        name.replace("_", " ").strip().title() or "Tool",
        _first_argument(
            arguments,
            "file_path",
            "path",
            "pattern",
            "query",
            "prompt",
        ),
    )


@dataclass(frozen=True)
class ToolStarted:
    call_id: str
    name: str
    # Short human-readable argument summary ("pytest -q", "mathlib.py"),
    # so a frontend can render Claude Code-style `bash(pytest -q)` cards
    # without re-deriving it from raw arguments.
    detail: str = ""
    activity: ToolActivity | None = None
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
    # Unknown arguments are opaque. Guessing from the first value can persist
    # credentials, signed URLs, or other tool-private data in the event ledger.
    # Sensitive tools provide an explicit presentation detail on their Tool.
    return ""


@dataclass(frozen=True)
class ToolCompleted:
    call_id: str
    name: str
    is_error: bool
    # A short preview of the tool's result (first lines, truncated), so a
    # frontend can show "what came back" on an expandable card without the
    # full — possibly huge — payload. Empty when the result was empty.
    result_preview: str = ""
    type: str = field(default="tool_completed", init=False)


def summarize_result(result: Any, limit: int = 400) -> str:
    """A compact, single-block preview of a tool result for display.

    Collapses to the leading ``limit`` characters with an elision marker.
    Pure and defensive: any value becomes a string, never raises.
    """
    text = result if isinstance(result, str) else str(result)
    text = text.strip()
    if len(text) > limit:
        return text[:limit].rstrip() + " …"
    return text


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
    SkillLoaded,
    SkillLoadFailed,
    AgentMessage,
    AgentMessageDelta,
    AgentMessageCompleted,
    AgentReasoningSummary,
    AgentReasoningStarted,
    AgentReasoningDelta,
    AgentReasoningCompleted,
    ModelUsageRecorded,
    PlanUpdated,
    ToolStarted,
    ToolCompleted,
    ErrorEvent,
    TaskComplete,
    ShutdownComplete,
]


@dataclass(frozen=True)
class Event:
    """One ordered session event correlated to its source submission."""

    id: str
    msg: EventMsg
    submission_id: str | None = None


def serialize_event(
    event: Event, *, include_submission_id: bool = False
) -> dict[str, Any]:
    """Serialize an event while preserving the legacy CLI wire shape.

    ``submission_id`` is an internal correlation field used by the application
    layer. Existing ``deepcode exec --json`` consumers receive the original
    ``{"id", "msg"}`` object unless a versioned caller opts in explicitly.
    """

    message = asdict(event.msg)
    if not include_submission_id:
        # Preserve the unversioned CLI NDJSON shape. Rich in-process clients
        # consume the correlation metadata directly from the dataclasses.
        if isinstance(event.msg, AgentMessage):
            message.pop("message_id", None)
            message.pop("phase", None)
        elif isinstance(event.msg, AgentMessageDelta):
            message.pop("message_id", None)
    payload = {"id": event.id, "msg": message}
    if include_submission_id:
        payload["submission_id"] = event.submission_id
    return payload
