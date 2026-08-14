"""Structured sub-agent results via a forced capture tool.

Borrowed from dsh's in-process subagent providers, which implement the
``outputSchema`` capability "with a forced capture tool": the child gets one
extra tool whose parameters ARE the requested object-rooted JSON Schema, and
the delegation result is the arguments of its last accepted call — not prose
scraped out of a final message.

Validation is free: the tool registry's ``prepare_call`` already runs
``Tool.validate_params`` against a tool's declared parameters, so a
non-conforming submission comes back to the child as an ordinary
errors-as-data result it can correct and retry.
"""

from __future__ import annotations

import json
from typing import Any

from core.agent_runtime.tools.base import Tool

CAPTURE_TOOL_NAME = "submit_result"


class SchemaError(ValueError):
    """The requested output schema is outside the supported subset."""


def validate_output_schema(schema: Any) -> dict[str, Any]:
    """The supported subset: one object-rooted JSON Schema (dsh's rule).

    Anything else fails at spawn time — a schema the capture tool cannot
    express must be rejected loud, never accepted-then-ignored.
    """
    if not isinstance(schema, dict) or not schema:
        raise SchemaError("output_schema must be a non-empty JSON object")
    if schema.get("type", "object") != "object":
        raise SchemaError(
            f"output_schema must be object-rooted, got type={schema.get('type')!r}"
        )
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        raise SchemaError("output_schema must declare at least one property")
    return {**schema, "type": "object"}


class StructuredResultCapture:
    """One delegation's capture state plus the tool the child submits through."""

    def __init__(self, schema: dict[str, Any]):
        self._schema = validate_output_schema(schema)
        self._captured: dict[str, Any] | None = None

    @property
    def captured(self) -> dict[str, Any] | None:
        """The last accepted submission, or ``None`` if none arrived."""
        return self._captured

    def reset(self) -> None:
        """Discard the previous submission at a follow-up turn boundary.

        A conversational child answers each follow-up with a FRESH
        submission; without the reset, a turn that never called the capture
        tool would silently hand the caller the previous turn's result as if
        it were new.
        """
        self._captured = None

    def render(self) -> str | None:
        """The captured result as the JSON payload the parent receives."""
        if self._captured is None:
            return None
        return json.dumps(self._captured, ensure_ascii=False, sort_keys=True)

    def make_tool(self) -> Tool:
        return _CaptureTool(self)

    def prompt_addendum(self) -> str:
        """System-prompt section telling the child the submission is mandatory."""
        return (
            "## Required structured result\n"
            f"When your task is complete you MUST call the `{CAPTURE_TOOL_NAME}` "
            "tool exactly once with your final result matching its parameter "
            "schema. Your prose answer is not the deliverable — only the "
            f"`{CAPTURE_TOOL_NAME}` submission is returned to the caller. "
            "Calling it again replaces the previous submission."
        )


class _CaptureTool(Tool):
    """The child-facing submission endpoint; parameters ARE the schema."""

    def __init__(self, capture: StructuredResultCapture):
        self._capture = capture

    @property
    def name(self) -> str:
        return CAPTURE_TOOL_NAME

    @property
    def description(self) -> str:
        return (
            "Submit your final structured result. Call this exactly once when "
            "the task is complete; the arguments must match the schema and "
            "become the result returned to the caller."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return self._capture._schema

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> Any:
        # Registry dispatch already validated kwargs against the schema
        # (prepare_call → validate_params); by the time we run, they conform.
        self._capture._captured = dict(kwargs)
        return "Result recorded. Finish with a brief closing summary."


__all__ = [
    "CAPTURE_TOOL_NAME",
    "SchemaError",
    "StructuredResultCapture",
    "validate_output_schema",
]
