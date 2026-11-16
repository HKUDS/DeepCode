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
            "Update the current Thread Goal. Use 'complete' only when current "
            "evidence (code, commands you ran, tests you checked cover it) "
            "proves every requirement of the latest Goal; the claim may be "
            "checked independently. The reason names only evidence from this "
            "conversation (commands you ran and what they showed), never a "
            "check you did not run. Use 'blocked' only for a persistent "
            "external blocker with no safe remaining action; it is accepted "
            "from the Goal's third Turn on. Goal identity "
            "comes from this Turn; do not guess IDs."
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
        return await self.runtime.request(
            status=status,
            reason=reason,
        )


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "checks": {
                "type": "array",
                "minItems": 1,
                "maxItems": 12,
                "items": {
                    "type": "object",
                    "properties": {
                        "requirement": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 500,
                            "description": "One requirement of the Goal, in plain words.",
                        },
                        "command": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 64,
                            "description": (
                                "The command that proves it, as an argument "
                                "list run in the workspace without a shell "
                                "(no pipes, redirections or globs), e.g. "
                                '["python", "-m", "pytest", "-q"]. Empty when '
                                "no command can prove it."
                            ),
                        },
                    },
                    "required": ["requirement"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["checks"],
        "additionalProperties": False,
    }
)
class ProposeGoalChecksTool(Tool):
    def __init__(self, runtime: GoalRuntimeRouter) -> None:
        self.runtime = runtime

    @property
    def name(self) -> str:
        return "propose_goal_checks"

    @property
    def description(self) -> str:
        return (
            "Propose the Goal's checklist: one entry per requirement of the "
            "Goal, each with the command that proves that requirement where "
            "one exists (a focused test rather than the whole suite when you "
            "can). The user confirms the "
            "list; after that, DeepCode itself runs the commands whenever the "
            "Goal is claimed complete, and a command that does not exit 0 "
            "turns the claim back. Proposing replaces an earlier proposal; it "
            "never changes a confirmed checklist."
        )

    @property
    def read_only(self) -> bool:
        # Only records a proposal; nothing runs until the user confirms it.
        return True

    async def execute(self, *, checks: list[dict[str, Any]]) -> dict[str, Any]:
        from core.domain.thread_goal import GoalCheck

        try:
            proposal = tuple(
                GoalCheck(
                    requirement=str(entry.get("requirement") or "").strip(),
                    command=tuple(str(part) for part in entry.get("command") or ()),
                )
                for entry in checks
            )
        except (AttributeError, ValueError) as exc:
            return {"proposed": 0, "error": str(exc)}
        return self.runtime.propose_checks(proposal)


__all__ = ["GetGoalTool", "ProposeGoalChecksTool", "UpdateGoalTool"]
