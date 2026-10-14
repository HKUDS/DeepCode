"""Regression cover for optional tool arguments in the bundled MCP servers.

A parameter written ``start_line: int = None`` makes FastMCP advertise
``{"type": "integer", "default": null}``: the default is null, but the type
excludes null. A client that sends the advertised default back explicitly
(``"start_line": null``) is rejected with a validation error before the tool
runs. Every argument whose schema default is null must also accept null.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.code_implementation_server as code_implementation_server
import tools.document_segmentation_server as document_segmentation_server

SERVERS = {
    "code-implementation": code_implementation_server.mcp,
    "document-segmentation": document_segmentation_server.mcp,
}


def _accepts_null(prop: dict) -> bool:
    if prop.get("type") == "null":
        return True
    if isinstance(prop.get("type"), list) and "null" in prop["type"]:
        return True
    return any(_accepts_null(option) for option in prop.get("anyOf", []))


def _null_default_args(server):
    tools = asyncio.run(server.list_tools())
    for tool in tools:
        for name, prop in tool.inputSchema.get("properties", {}).items():
            if "default" in prop and prop["default"] is None:
                yield tool.name, name, prop


@pytest.mark.parametrize("server_name", sorted(SERVERS))
def test_null_default_arguments_accept_null(server_name):
    server = SERVERS[server_name]
    found = list(_null_default_args(server))
    assert found, f"{server_name}: expected optional arguments with a null default"

    rejected = []
    for tool_name, arg_name, prop in found:
        tool = server._tool_manager.get_tool(tool_name)
        required = {req: "x" for req in tool.parameters.get("required", [])}
        if not _accepts_null(prop):
            rejected.append(f"{tool_name}.{arg_name}: schema {prop}")
            continue
        try:
            tool.fn_metadata.arg_model.model_validate({**required, arg_name: None})
        except ValidationError as exc:
            rejected.append(f"{tool_name}.{arg_name}: {exc}")

    assert not rejected, "null default but null rejected:\n" + "\n".join(rejected)
