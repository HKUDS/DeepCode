"""request_user_input — let the agent ask the user mid-task (C1).

A general agent should be able to stop and ask when it is genuinely blocked and
no safe assumption exists (the working-style preamble tells it to prefer
assumptions otherwise). The model supplies one or a few questions, each with an
optional short header and 2-3 choices; the answer is fed back as the tool
result.

Delivery is an injected ``ask`` callback (mirroring the permission
``approval_callback``): interactive frontends pass one that prompts the human;
headless runs pass none, so this tool is simply not registered there and the
agent proceeds on its own. Should it ever be called without a live channel, it
degrades to telling the model to make a reasonable assumption and continue.
"""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable

from core.agent_runtime.tools.base import Tool, tool_parameters

# (question, options|None) -> the user's free-text answer (sync or async).
AskUser = Callable[[str, "list[str] | None"], "Awaitable[str] | str"]

_NO_CHANNEL = (
    "No interactive user is available to answer. Make a reasonable assumption, "
    "state it briefly, and continue."
)


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "description": "Questions to ask the user. Prefer 1; do not exceed 3.",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "Single-sentence prompt shown to the user.",
                        },
                        "header": {
                            "type": "string",
                            "description": "Short label (<=12 chars) shown in the UI.",
                        },
                        "options": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional 2-3 mutually exclusive choices; "
                            "put the recommended one first. Omit for a free-form "
                            "answer.",
                        },
                    },
                    "required": ["question"],
                },
            }
        },
        "required": ["questions"],
    }
)
class RequestUserInputTool(Tool):
    """Ask the user one or a few questions and return their answers."""

    def __init__(self, ask: AskUser | None = None):
        self._ask = ask

    @property
    def name(self) -> str:
        return "request_user_input"

    @property
    def description(self) -> str:
        return (
            "Ask the user for a decision when you are genuinely blocked and no "
            "safe assumption exists. Provide 1-3 questions, each with an "
            "optional short header and 2-3 choices. Returns the user's answers. "
            "Prefer making a reasonable assumption over asking."
        )

    @property
    def read_only(self) -> bool:
        # No workspace / security side effects — just an interaction.
        return True

    async def execute(self, **kwargs: Any) -> Any:
        raw = kwargs.get("questions")
        if not isinstance(raw, list) or not raw:
            return "Error: 'questions' must be a non-empty list."
        if self._ask is None:
            return _NO_CHANNEL

        answers: list[str] = []
        for i, q in enumerate(raw[:3]):  # cap at 3, matching the schema guidance
            if not isinstance(q, dict) or not str(q.get("question", "")).strip():
                return f"Error: questions[{i}].question is required."
            question = str(q["question"]).strip()
            options = [str(o) for o in q.get("options") or [] if str(o).strip()]
            try:
                result = self._ask(question, options or None)
                answer = await result if inspect.isawaitable(result) else result
            except Exception as exc:  # noqa: BLE001 - a failed prompt must not crash
                return f"Error asking the user: {exc}"
            answers.append(f"Q: {question}\nA: {str(answer).strip()}")
        return "\n\n".join(answers)
