"""Minimal Goal protocol tools for the working Agent."""

from __future__ import annotations

from typing import Any

from core.agent_runtime.goal_runtime import GoalRuntimeRouter
from core.agent_runtime.tools.base import Tool, tool_parameters


@tool_parameters(
    {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
)
class GetGoalTool(Tool):
    def __init__(self, runtime: GoalRuntimeRouter) -> None:
        self.runtime = runtime

    @property
    def name(self) -> str:
        return "get_goal"

    @property
    def description(self) -> str:
        return (
            "Read the latest Thread Goal, status, usage, and whether this Turn "
            "may update it."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **_kwargs: Any) -> dict[str, Any]:
        return self.runtime.read()


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["complete", "blocked"],
            },
            "reason": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2_000,
            },
        },
        "required": ["status"],
        "additionalProperties": False,
    }
)
class UpdateGoalTool(Tool):
    def __init__(self, runtime: GoalRuntimeRouter) -> None:
        self.runtime = runtime

    @property
    def name(self) -> str:
        return "update_goal"

    @property
    def description(self) -> str:
        return (
            "Update the current Thread Goal. Use 'complete' only after checking "
            "the latest Goal against real code and tool evidence. Use 'blocked' "
            "only for a persistent external blocker with no safe remaining "
            "action. Goal identity comes from this Turn; do not guess IDs."
        )

    @property
    def read_only(self) -> bool:
        # The call records a constrained orchestration request; it cannot
        # mutate the workspace, grant permissions, or bypass host validation.
        return True

    async def execute(
        self,
        *,
        status: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        return self.runtime.request(
            status=status,
            reason=reason,
        )


__all__ = ["GetGoalTool", "UpdateGoalTool"]
