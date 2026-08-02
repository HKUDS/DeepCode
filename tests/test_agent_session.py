"""Tests for the SQ/EQ AgentSession — the protocol's real engine.

Drives a full submit → event-stream cycle offline with a scripted provider
and a fake tool, proving the SQ/EQ protocol + kernel bridge work end to end.
"""

from __future__ import annotations

import sys
import asyncio
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_runtime.tools.base import Tool, tool_parameters  # noqa: E402
from core.agent_runtime.tools.registry import ToolRegistry  # noqa: E402
from core.events import (  # noqa: E402
    AgentMessage,
    AgentMessageCompleted,
    AgentMessageDelta,
    AgentMessagePhase,
    AgentReasoningCompleted,
    AgentReasoningDelta,
    AgentReasoningStarted,
    AgentSession,
    Event,
    Interrupt,
    ModelUsageRecorded,
    PlanStepStatus,
    PlanUpdated,
    Shutdown,
    Submission,
    TaskComplete,
    ToolCompleted,
    ToolStarted,
    UserInput,
    serialize_event,
)
from core.harness.tools.plan import UpdatePlanTool  # noqa: E402
from core.harness.tools.shell import BashTool  # noqa: E402
from core.providers.base import LLMResponse, ToolCallRequest  # noqa: E402
from core.reasoning import ReasoningAvailability, ReasoningChannel  # noqa: E402


@tool_parameters(
    {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
)
class EchoTool(Tool):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo text."

    async def execute(self, **kwargs: Any) -> Any:
        return f"echo: {kwargs.get('text', '')}"


@tool_parameters({"type": "object", "properties": {}})
class BlockingTool(Tool):
    @property
    def name(self) -> str:
        return "block"

    @property
    def description(self) -> str:
        return "Wait until the Turn is interrupted."

    async def execute(self, **kwargs: Any) -> Any:
        await asyncio.Event().wait()


class ScriptedProvider:
    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls = 0

    def get_default_model(self) -> str:
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        i = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return self.responses[i]


class StreamingTransportProvider(ScriptedProvider):
    def __init__(self, responses: list[LLMResponse]):
        super().__init__(responses)
        self.stream_calls = 0

    async def chat_stream_with_retry(self, **kwargs: Any) -> LLMResponse:
        self.stream_calls += 1
        i = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        response = self.responses[i]
        reasoning_callback = kwargs.get("on_reasoning_delta")
        if reasoning_callback is not None:
            if response.reasoning_summary:
                await reasoning_callback(
                    response.reasoning_summary,
                    ReasoningChannel.SUMMARY,
                )
            if (
                response.reasoning_content
                and response.reasoning_content != response.reasoning_summary
            ):
                await reasoning_callback(
                    response.reasoning_content,
                    ReasoningChannel.PROVIDER_TRACE,
                )
        callback = kwargs.get("on_content_delta")
        if callback is not None and response.content:
            await callback(response.content)
        return response


def _tools() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(EchoTool())
    return reg


def _tools_with_plan() -> ToolRegistry:
    reg = _tools()
    reg.register(UpdatePlanTool())
    return reg


def _session(provider, **kw) -> AgentSession:
    return AgentSession(provider, _tools(), model="fake-model", **kw)


def _types(events):
    return [e.msg.type for e in events]


@pytest.mark.asyncio
async def test_plain_text_turn_emits_started_message_complete():
    provider = ScriptedProvider([LLMResponse(content="hello", finish_reason="stop")])
    session = _session(provider)

    await session.submit(UserInput(text="hi"))
    events = session.drain_events()

    assert _types(events) == ["turn_started", "agent_message", "task_complete"]
    assert isinstance(events[1].msg, AgentMessage)
    assert events[1].msg.text == "hello"
    complete = events[-1].msg
    assert isinstance(complete, TaskComplete)
    assert complete.stop_reason == "completed"


@pytest.mark.asyncio
async def test_failed_model_response_still_records_reported_usage():
    provider = ScriptedProvider(
        [
            LLMResponse(
                content="provider rejected request",
                finish_reason="error",
                usage={
                    "prompt_tokens": 5,
                    "completion_tokens": 0,
                    "total_tokens": 5,
                },
            )
        ]
    )
    session = _session(provider)

    await session.submit(UserInput(text="fail"))
    events = session.drain_events()

    usage = next(
        event.msg for event in events if isinstance(event.msg, ModelUsageRecorded)
    )
    assert usage.usage["total_tokens"] == 5
    assert session.last_usage["total_tokens"] == 5
    assert events[-1].msg.stop_reason == "error"


@pytest.mark.asyncio
async def test_headless_session_can_use_streaming_transport_without_delta_events():
    provider = StreamingTransportProvider(
        [LLMResponse(content="hello", finish_reason="stop")]
    )
    session = _session(
        provider,
        streaming=False,
        streaming_transport=True,
    )

    await session.submit(UserInput(text="hi"))
    events = session.drain_events()

    assert provider.stream_calls == 1
    assert _types(events) == ["turn_started", "agent_message", "task_complete"]


@pytest.mark.asyncio
async def test_session_falls_back_for_provider_without_streaming_transport():
    provider = ScriptedProvider([LLMResponse(content="hello", finish_reason="stop")])
    session = _session(
        provider,
        streaming=False,
        streaming_transport=True,
    )

    await session.submit(UserInput(text="hi"))
    events = session.drain_events()

    assert provider.calls == 1
    assert _types(events) == ["turn_started", "agent_message", "task_complete"]


@pytest.mark.asyncio
async def test_reasoning_lifecycle_keeps_summary_trace_and_answer_separate():
    provider = ScriptedProvider(
        [
            LLMResponse(
                content="answer",
                reasoning_content="provider trace",
                reasoning_summary="Checked the constraints.",
                provider_state={"opaque": ["state"]},
            )
        ]
    )
    session = _session(provider)

    await session.submit(UserInput(text="solve"))
    events = session.drain_events()

    started = [
        event.msg for event in events if isinstance(event.msg, AgentReasoningStarted)
    ]
    deltas = [
        event.msg for event in events if isinstance(event.msg, AgentReasoningDelta)
    ]
    completed = [
        event.msg for event in events if isinstance(event.msg, AgentReasoningCompleted)
    ]
    assert len(started) == 1
    assert [(message.channel, message.delta) for message in deltas] == [
        (ReasoningChannel.SUMMARY, "Checked the constraints."),
        (ReasoningChannel.PROVIDER_TRACE, "provider trace"),
    ]
    assert len(completed) == 1
    assert completed[0].reasoning_id == started[0].reasoning_id
    assert completed[0].summary_text == "Checked the constraints."
    assert completed[0].trace_text == "provider trace"
    assert completed[0].availability is ReasoningAvailability.AVAILABLE
    answer = next(event.msg for event in events if isinstance(event.msg, AgentMessage))
    assert answer.text == "answer"
    assert "provider trace" not in answer.text
    assistant = next(
        message
        for message in reversed(session.history)
        if message["role"] == "assistant"
    )
    assert assistant["provider_state"] == {"opaque": ["state"]}


@pytest.mark.asyncio
async def test_opaque_reasoning_state_has_lifecycle_without_display_text():
    provider = ScriptedProvider(
        [
            LLMResponse(
                content="answer",
                provider_state={"encrypted": "opaque"},
            )
        ]
    )
    session = _session(provider)

    await session.submit(UserInput(text="solve"))
    events = session.drain_events()

    assert not any(isinstance(event.msg, AgentReasoningDelta) for event in events)
    completed = next(
        event.msg for event in events if isinstance(event.msg, AgentReasoningCompleted)
    )
    assert completed.availability is ReasoningAvailability.OPAQUE
    assert completed.summary_text == ""
    assert completed.trace_text == ""


@pytest.mark.asyncio
async def test_streamed_reasoning_is_ordered_before_visible_answer():
    provider = StreamingTransportProvider(
        [
            LLMResponse(
                content="answer",
                reasoning_summary="Checked inputs.",
                reasoning_content="provider trace",
            )
        ]
    )
    session = _session(provider, streaming=True)

    await session.submit(UserInput(text="solve"))
    events = session.drain_events()
    event_types = _types(events)

    start_index = event_types.index("agent_reasoning_started")
    completed_index = event_types.index("agent_reasoning_completed")
    answer_delta_index = event_types.index("agent_message_delta")
    assert start_index < answer_delta_index < completed_index
    deltas = [
        event.msg for event in events if isinstance(event.msg, AgentReasoningDelta)
    ]
    assert [(message.channel, message.delta) for message in deltas] == [
        (ReasoningChannel.SUMMARY, "Checked inputs."),
        (ReasoningChannel.PROVIDER_TRACE, "provider trace"),
    ]


@pytest.mark.asyncio
async def test_finalization_retry_has_its_own_reasoning_lifecycle():
    provider = ScriptedProvider(
        [
            LLMResponse(content="", finish_reason="stop"),
            LLMResponse(content="", finish_reason="stop"),
            LLMResponse(
                content="recovered answer",
                reasoning_summary="Recovered from empty responses.",
                finish_reason="stop",
            ),
        ]
    )
    session = _session(provider)

    await session.submit(UserInput(text="solve"))
    events = session.drain_events()

    completed = [
        event.msg for event in events if isinstance(event.msg, AgentReasoningCompleted)
    ]
    assert provider.calls == 3
    assert len(completed) == 1
    assert completed[0].summary_text == "Recovered from empty responses."
    assert (
        next(event.msg.text for event in events if isinstance(event.msg, AgentMessage))
        == "recovered answer"
    )


@pytest.mark.asyncio
async def test_tool_turn_emits_tool_started_and_completed():
    provider = ScriptedProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(id="c1", name="echo", arguments={"text": "x"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="done", finish_reason="stop"),
        ]
    )
    session = _session(provider)

    await session.submit(UserInput(text="use echo"))
    events = session.drain_events()

    kinds = _types(events)
    assert kinds[0] == "turn_started"
    assert "tool_started" in kinds and "tool_completed" in kinds
    started = [e.msg for e in events if isinstance(e.msg, ToolStarted)][0]
    completed = [e.msg for e in events if isinstance(e.msg, ToolCompleted)][0]
    assert started.name == "echo"
    assert completed.is_error is False
    assert kinds[-1] == "task_complete"


@pytest.mark.asyncio
async def test_nonzero_bash_exit_emits_failed_tool_completion(tmp_path):
    provider = ScriptedProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="bash-failed",
                        name="bash",
                        arguments={"command": "printf 'failed'; exit 9"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="recovered", finish_reason="stop"),
        ]
    )
    tools = ToolRegistry()
    tools.register(BashTool(str(tmp_path)))
    session = AgentSession(provider, tools, model="fake-model")

    await session.submit(UserInput(text="run verification"))
    events = session.drain_events()

    completed = next(
        event.msg for event in events if isinstance(event.msg, ToolCompleted)
    )
    assert completed.name == "bash"
    assert completed.is_error is True
    assert completed.result_preview.startswith("[exit 9]")
    tool_message = next(
        message for message in session.history if message.get("role") == "tool"
    )
    assert tool_message["content"].startswith("[exit 9]")


@pytest.mark.asyncio
async def test_streaming_turn_preserves_commentary_and_final_message_items():
    provider = StreamingTransportProvider(
        [
            LLMResponse(
                content="I will inspect the input.",
                tool_calls=[
                    ToolCallRequest(id="c1", name="echo", arguments={"text": "x"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="Inspection complete.", finish_reason="stop"),
        ]
    )
    session = _session(provider, streaming=True)

    await session.submit(UserInput(text="inspect"))
    events = session.drain_events()

    first_delta = next(
        event.msg
        for event in events
        if isinstance(event.msg, AgentMessageDelta)
        and event.msg.delta == "I will inspect the input."
    )
    completions = [
        event.msg for event in events if isinstance(event.msg, AgentMessageCompleted)
    ]
    final = next(event.msg for event in events if isinstance(event.msg, AgentMessage))

    assert len(completions) == 2
    assert completions[0].message_id == first_delta.message_id
    assert completions[0].phase is AgentMessagePhase.COMMENTARY
    assert completions[0].text == "I will inspect the input."
    assert completions[1].text == "Inspection complete."
    assert final.message_id == completions[1].message_id
    assert final.phase is AgentMessagePhase.FINAL_ANSWER

    ordered_types = _types(events)
    assert ordered_types.index("agent_message_completed") < ordered_types.index(
        "tool_started"
    )
    assert ordered_types.index("tool_completed") < max(
        index
        for index, event_type in enumerate(ordered_types)
        if event_type == "agent_message_completed"
    )


@pytest.mark.asyncio
async def test_successful_update_plan_emits_structured_plan_state():
    provider = ScriptedProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="plan-1",
                        name="update_plan",
                        arguments={
                            "explanation": "Starting",
                            "plan": [
                                {
                                    "step": "Inspect the repository",
                                    "status": "in_progress",
                                },
                                {"step": "Run tests", "status": "pending"},
                            ],
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="done", finish_reason="stop"),
        ]
    )
    session = AgentSession(
        provider,
        _tools_with_plan(),
        model="fake-model",
    )

    await session.submit(UserInput(text="make a plan"))
    events = session.drain_events()

    plan = next(event.msg for event in events if isinstance(event.msg, PlanUpdated))
    assert plan.explanation == "Starting"
    assert [item.step for item in plan.plan] == [
        "Inspect the repository",
        "Run tests",
    ]
    assert [item.status for item in plan.plan] == [
        PlanStepStatus.IN_PROGRESS,
        PlanStepStatus.PENDING,
    ]
    assert _types(events).index("tool_started") < _types(events).index("plan_updated")
    assert _types(events).index("plan_updated") < _types(events).index("tool_completed")


@pytest.mark.asyncio
async def test_denied_tool_marks_completed_as_error():
    provider = ScriptedProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(id="c1", name="echo", arguments={"text": "x"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="ok", finish_reason="stop"),
        ]
    )

    def deny(_name, _args):
        return ("deny", "blocked")

    session = _session(provider, permission_checker=deny)
    await session.submit(UserInput(text="go"))
    events = session.drain_events()

    completed = [e.msg for e in events if isinstance(e.msg, ToolCompleted)][0]
    assert completed.is_error is True


@pytest.mark.asyncio
async def test_history_persists_across_turns():
    provider = ScriptedProvider(
        [
            LLMResponse(content="a", finish_reason="stop"),
            LLMResponse(content="b", finish_reason="stop"),
        ]
    )
    session = _session(provider)
    await session.submit(UserInput(text="first"))
    await session.submit(UserInput(text="second"))
    roles_contents = [(m["role"], m.get("content")) for m in session.history]
    # two user turns + two assistant replies retained
    assert ("user", "first") in roles_contents
    assert ("user", "second") in roles_contents


@pytest.mark.asyncio
async def test_ids_correlate_and_increase():
    provider = ScriptedProvider([LLMResponse(content="x", finish_reason="stop")])
    session = _session(provider)
    await session.submit(UserInput(text="hi"))
    events = session.drain_events()
    ids = [int(e.id) for e in events]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)


def test_legacy_cli_serialization_omits_rich_message_metadata():
    message = Event(
        id="1",
        msg=AgentMessage(
            text="done",
            message_id="message-1",
            phase=AgentMessagePhase.FINAL_ANSWER,
        ),
        submission_id="submission-1",
    )
    delta = Event(
        id="2",
        msg=AgentMessageDelta(delta="do", message_id="message-1"),
        submission_id="submission-1",
    )

    assert serialize_event(message) == {
        "id": "1",
        "msg": {"text": "done", "type": "agent_message"},
    }
    assert serialize_event(delta) == {
        "id": "2",
        "msg": {"delta": "do", "type": "agent_message_delta"},
    }


@pytest.mark.asyncio
async def test_shutdown_emits_shutdown_complete():
    provider = ScriptedProvider([LLMResponse(content="x", finish_reason="stop")])
    session = _session(provider)
    await session.submit(Shutdown())
    assert _types(session.drain_events()) == ["shutdown_complete"]


@pytest.mark.asyncio
async def test_interrupt_when_idle_is_noop():
    provider = ScriptedProvider([LLMResponse(content="x", finish_reason="stop")])
    session = _session(provider)
    await session.submit(Interrupt())  # nothing running
    assert session.drain_events() == []


@pytest.mark.asyncio
async def test_envelope_correlates_events_and_busy_turn_terminates():
    provider = ScriptedProvider([LLMResponse(content="x", finish_reason="stop")])
    session = _session(provider)

    events = [
        event
        async for event in session.run_stream_envelope(
            Submission(id="submission-1", op=UserInput(text="hi"))
        )
    ]
    assert events[-1].msg.type == "task_complete"
    assert {event.submission_id for event in events} == {"submission-1"}

    session._busy = True
    busy_events = [
        event
        async for event in session.run_stream_envelope(
            Submission(id="submission-2", op=UserInput(text="again"))
        )
    ]
    assert _types(busy_events) == ["error", "task_complete"]
    assert busy_events[-1].msg.stop_reason == "busy"


@pytest.mark.asyncio
async def test_interrupt_terminates_the_active_submission_with_original_id():
    class BlockingProvider(ScriptedProvider):
        async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
            await asyncio.Event().wait()

    session = _session(BlockingProvider([]))
    stream = session.run_stream_envelope(
        Submission(id="turn-submission", op=UserInput(text="wait"))
    )
    started = await anext(stream)
    assert started.msg.type == "turn_started"

    await session.submit_envelope(Submission(id="control-submission", op=Interrupt()))
    terminal = await anext(stream)

    assert terminal.msg.stop_reason == "interrupted"
    assert terminal.submission_id == "turn-submission"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.asyncio
async def test_interrupt_preserves_usage_observed_before_blocking_tool():
    provider = ScriptedProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(id="c1", name="block", arguments={}),
                ],
                finish_reason="tool_calls",
                usage={
                    "prompt_tokens": 11,
                    "completion_tokens": 2,
                    "total_tokens": 13,
                },
            )
        ]
    )
    tools = ToolRegistry()
    tools.register(BlockingTool())
    session = AgentSession(provider, tools, model="fake-model")
    stream = session.run_stream_envelope(
        Submission(id="turn-submission", op=UserInput(text="wait"))
    )

    started = await anext(stream)
    usage_event = await anext(stream)
    tool_started = await anext(stream)
    assert started.msg.type == "turn_started"
    assert isinstance(usage_event.msg, ModelUsageRecorded)
    assert usage_event.msg.response_ordinal == 1
    assert tool_started.msg.type == "tool_started"

    await session.submit_envelope(Submission(id="control-submission", op=Interrupt()))
    terminal = await anext(stream)

    assert terminal.msg.stop_reason == "interrupted"
    assert session.last_usage == {
        "prompt_tokens": 11,
        "completion_tokens": 2,
        "total_tokens": 13,
    }
