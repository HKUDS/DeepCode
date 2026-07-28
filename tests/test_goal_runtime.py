from __future__ import annotations

import pytest

from core.agent_runtime.goal_runtime import (
    GoalRuntimeContext,
    GoalRuntimeRouter,
)
from core.agent_runtime.runner import AgentRunner, AgentRunSpec
from core.agent_runtime.tools.base import Tool, tool_parameters
from core.agent_runtime.tools.registry import ToolRegistry
from core.harness.tools.goal import GetGoalTool, UpdateGoalTool
from core.providers.base import LLMResponse, ToolCallRequest


class FakeGoalHandler:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def read_goal(self, context: GoalRuntimeContext):
        return {"goalId": context.goal_id}

    def update_goal(
        self,
        context: GoalRuntimeContext,
        *,
        status: str,
        reason: str | None,
    ):
        request = {
            "turnId": context.turn_id,
            "status": status,
            "reason": reason,
        }
        self.requests.append(request)
        return {"goalId": context.goal_id, "status": status}


def _context() -> GoalRuntimeContext:
    return GoalRuntimeContext(
        thread_id="session",
        goal_id="goal_1",
        turn_id="turn_1",
    )


def test_goal_tools_are_hidden_outside_an_attempt_and_freeze_after_request() -> None:
    runtime = GoalRuntimeRouter()
    handler = FakeGoalHandler()
    runtime.configure(handler)
    registered = ("read", "get_goal", "update_goal", "bash")

    assert runtime.visible_tool_names(registered) == ("read", "bash")

    runtime.activate(_context())
    assert runtime.visible_tool_names(registered) == registered
    result = runtime.request(
        status="complete",
        reason="The latest Goal is complete.",
    )

    assert result["status"] == "complete"
    assert runtime.visible_tool_names(registered) == ()
    assert handler.requests[0]["turnId"] == "turn_1"

    runtime.deactivate("turn_1")
    assert runtime.visible_tool_names(registered) == ("read", "bash")


def test_goal_tool_schemas_expose_only_the_minimal_protocol() -> None:
    runtime = GoalRuntimeRouter()
    get_goal = GetGoalTool(runtime).to_schema()["function"]
    update_goal = UpdateGoalTool(runtime).to_schema()["function"]

    assert get_goal["parameters"]["properties"] == {}
    assert update_goal["parameters"]["properties"]["status"]["enum"] == [
        "complete",
        "blocked",
    ]
    assert update_goal["parameters"]["required"] == ["status"]
    assert "expected_definition_revision" not in update_goal["parameters"]["properties"]


@tool_parameters({"type": "object", "properties": {}})
class SideEffectTool(Tool):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "side_effect"

    @property
    def description(self) -> str:
        return "Record a test side effect."

    async def execute(self):
        self.calls += 1
        return "changed"


class TerminalThenFinalProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def chat_with_retry(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="decision",
                        name="update_goal",
                        arguments={
                            "status": "complete",
                            "reason": "The latest Goal is complete.",
                        },
                    ),
                    ToolCallRequest(
                        id="late-write",
                        name="side_effect",
                        arguments={},
                    ),
                ],
                finish_reason="tool_calls",
            )
        return LLMResponse(content="Final summary.", finish_reason="stop")


@pytest.mark.asyncio
async def test_terminal_request_blocks_later_tools_in_the_same_model_response() -> None:
    runtime = GoalRuntimeRouter()
    runtime.configure(FakeGoalHandler())
    runtime.activate(_context())
    side_effect = SideEffectTool()
    registry = ToolRegistry()
    registry.register(UpdateGoalTool(runtime))
    registry.register(side_effect)
    provider = TerminalThenFinalProvider()
    spec = AgentRunSpec(
        initial_messages=[{"role": "user", "content": "finish"}],
        tools=registry,
        model="fake",
        max_iterations=4,
        max_tool_result_chars=10_000,
        tool_filter=lambda: runtime.visible_tool_names(tuple(registry.tool_names)),
    )

    result = await AgentRunner(provider).run(spec)

    assert result.final_content == "Final summary."
    assert side_effect.calls == 0
    denied = [
        message["content"]
        for message in result.messages
        if message.get("role") == "tool" and message.get("tool_call_id") == "late-write"
    ]
    assert denied and "not allowed" in denied[0]
