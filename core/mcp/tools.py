"""MCP capabilities adapted to native DeepCode Tool contracts."""

from __future__ import annotations

import json
import time
from typing import Any

from loguru import logger

from core.agent_runtime.tools.base import Tool, ToolResult, sanitize_description
from core.mcp.connection import McpConnection
from core.mcp.models import (
    McpToolAnnotations,
    McpToolIdentity,
)
from core.mcp.schema import normalize_schema_for_openai
from core.observability import log_mcp_call


class McpToolAdapter(Tool):
    """One discovered MCP tool with authoritative raw identity metadata."""

    def __init__(
        self,
        connection: McpConnection,
        tool_definition: Any,
        *,
        visible_name: str,
    ) -> None:
        server = connection.server
        self.connection = connection
        self.identity = McpToolIdentity(
            server_id=server.server_id,
            server_name=server.name,
            source=server.source,
            raw_name=str(tool_definition.name),
        )
        self._name = visible_name
        # P1-2: remote descriptions are untrusted and quality-uncontrolled —
        # bound length (they count against the prompt budget) and replace
        # degenerate/empty ones so the model still has something to route on.
        self._description = sanitize_description(
            str(tool_definition.description or ""),
            name=visible_name,
        )
        raw_schema = getattr(tool_definition, "inputSchema", None)
        self._parameters = normalize_schema_for_openai(raw_schema)
        self.annotations = McpToolAnnotations.from_sdk(
            getattr(tool_definition, "annotations", None)
        )
        self.approval_mode = server.definition.policy_for(self.identity.raw_name)

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    @property
    def read_only(self) -> bool:
        # MCP annotations are hints, not grants. Unknown is deliberately
        # mutating so default/writes policies fail toward confirmation.
        return self.annotations.read_only

    @property
    def concurrency_safe(self) -> bool:
        return self.connection.server.definition.supports_parallel_tool_calls

    def presentation_detail(self, arguments: dict[str, Any]) -> str | None:
        # Arbitrary remote tools may accept credentials or signed URLs under
        # innocent-looking argument names. Inventory identifies the tool; raw
        # arguments remain in protected observability only.
        return ""

    async def execute(self, **kwargs: Any) -> ToolResult:
        started = time.monotonic()
        try:
            result = await self.connection.call_tool(self.identity.raw_name, kwargs)
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            _log_call(
                self.identity,
                kwargs,
                started,
                status="error",
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        text = _result_text(result)
        is_error = bool(getattr(result, "isError", False))
        _log_call(
            self.identity,
            kwargs,
            started,
            status="error" if is_error else "ok",
            result=None if is_error else text,
            error=text if is_error else None,
        )
        return ToolResult(
            text,
            is_error=is_error,
            metadata={
                "origin": "mcp",
                "serverId": self.identity.server_id,
                "serverName": self.identity.server_name,
                "toolName": self.identity.raw_name,
                "source": self.identity.source.value,
                "readOnly": self.annotations.read_only,
                "approvalMode": self.approval_mode.value,
            },
        )


def _result_text(result: Any) -> str:
    parts: list[str] = []
    for block in getattr(result, "content", ()):
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
            continue
        dump = getattr(block, "model_dump", None)
        value = dump(mode="json", by_alias=True) if callable(dump) else str(block)
        parts.append(
            json.dumps(value, ensure_ascii=False)
            if not isinstance(value, str)
            else value
        )
    structured = getattr(result, "structuredContent", None)
    if structured is not None and not parts:
        parts.append(json.dumps(structured, ensure_ascii=False, sort_keys=True))
    return "\n".join(part for part in parts if part) or "(no output)"


def _log_call(
    identity: McpToolIdentity,
    arguments: dict[str, Any],
    started: float,
    *,
    status: str,
    result: str | None = None,
    error: str | None = None,
) -> None:
    try:
        log_mcp_call(
            server=identity.server_id,
            tool=identity.raw_name,
            duration_ms=int((time.monotonic() - started) * 1_000),
            status=status,
            arguments=arguments,
            result=result,
            error=error,
        )
    except Exception as exc:  # noqa: BLE001 - telemetry never changes tool outcome
        logger.debug("Unable to record MCP call telemetry: {}", type(exc).__name__)


__all__ = ["McpToolAdapter"]
