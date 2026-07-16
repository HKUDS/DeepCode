"""Tests for C5b — code mode (the ``code`` tool).

The model's Python program runs in a real subprocess (sandboxed when a backend
is available); the governed tool executor is stubbed so we exercise the real
RPC bridge, dispatch, capture, and error handling without an LLM.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.code_mode import (  # noqa: E402
    CodeModeTool,
    ToolAPISpec,
    api_from_definitions,
)

_API = [
    ToolAPISpec(
        "write", ["file_path", "content"], "write(file_path, content)", "write a file"
    ),
    ToolAPISpec("read", ["file_path"], "read(file_path)", "read a file"),
    ToolAPISpec("boom", [], "boom()", "always fails"),
]


def _tool(workspace, calls):
    async def _execute(name, args):
        calls.append((name, dict(args)))
        if name == "write":
            (Path(workspace) / args["file_path"]).write_text(args["content"])
            return f"wrote {args['file_path']}"
        if name == "read":
            return (Path(workspace) / args["file_path"]).read_text()
        if name == "boom":
            raise RuntimeError("kaboom")
        return "ok"

    return CodeModeTool(workspace, _execute, _API, timeout_s=30)


def _run(code):
    ws = tempfile.mkdtemp()
    calls = []
    out = asyncio.run(_tool(ws, calls).execute(code=code))
    return out, calls, ws


def test_api_from_definitions_filters_and_orders():
    defs = [
        {
            "function": {
                "name": "write",
                "description": "Write a file. More.",
                "parameters": {
                    "properties": {"file_path": {}, "content": {}, "mode": {}},
                    "required": ["file_path", "content"],
                },
            }
        },
        {"function": {"name": "spawn_agent", "parameters": {}}},  # not exposed
    ]
    specs = api_from_definitions(defs, frozenset({"write"}))
    assert len(specs) == 1
    spec = specs[0]
    assert spec.name == "write"
    assert spec.params == [
        "file_path",
        "content",
        "mode",
    ]  # required first, then optional
    assert spec.signature == "write(file_path, content, mode=None)"
    assert spec.doc == "Write a file"


def test_code_mode_batches_tool_calls_in_one_run():
    code = (
        "created = []\n"
        "for i in range(3):\n"
        "    name = f'mod{i}.py'\n"
        "    write(name, f'VALUE = {i}\\n')\n"
        "    created.append(name)\n"
        "back = read('mod1.py')\n"
        "print('built', len(created))\n"
        "result = {'created': created, 'ok': 'VALUE = 1' in back}\n"
    )
    out, calls, ws = _run(code)
    assert [c[0] for c in calls] == ["write", "write", "write", "read"]
    assert all(
        (Path(ws) / f"mod{i}.py").is_file() for i in range(3)
    )  # tools really ran
    assert "built 3" in out
    assert "'ok': True" in out


def test_positional_and_keyword_args():
    out, calls, _ = _run("write('a.py', 'x')\nwrite(file_path='b.py', content='y')\n")
    assert calls == [
        ("write", {"file_path": "a.py", "content": "x"}),
        ("write", {"file_path": "b.py", "content": "y"}),
    ]


def test_tool_error_becomes_catchable_exception():
    code = "try:\n    boom()\nexcept RuntimeError as e:\n    print('caught', e)\n"
    out, calls, _ = _run(code)
    assert calls == [("boom", {})]
    assert "caught" in out and "kaboom" in out


def test_code_exception_returns_traceback():
    out, _calls, _ = _run("raise ValueError('bad in code')\n")
    assert "raised an error" in out and "ValueError: bad in code" in out


def test_unexposed_tool_is_a_plain_nameerror():
    # A tool that is not exposed to code mode simply isn't a defined name.
    out, _calls, _ = _run(
        "try:\n    edit('x')\nexcept NameError as e:\n    print('err', e)\n"
    )
    assert "err" in out and "'edit' is not defined" in out


def test_dispatch_refuses_unexposed_tool():
    # Security guard: even a rogue call for a non-exposed tool is refused, never
    # executed by the parent.
    ws = tempfile.mkdtemp()
    ran = []

    async def _execute(name, args):
        ran.append(name)
        return "should-not-run"

    tool = CodeModeTool(ws, _execute, _API)
    value, error = asyncio.run(tool._dispatch("spawn_agent", {}))
    assert value is None and "unknown tool" in error
    assert ran == []  # the parent never invoked the executor


def test_missing_code_arg():
    ws = tempfile.mkdtemp()
    out = asyncio.run(_tool(ws, []).execute(code="   "))
    assert "required" in out


def test_large_tool_arg_round_trips_past_default_stream_limit():
    # A write whose content exceeds asyncio's default 64KB readline limit must
    # still cross the RPC bridge intact (regression for the raised _STREAM_LIMIT).
    out, _calls, ws = _run(
        "write('big.py', 'H' * 100000)\nprint('len', len(read('big.py')))\n"
    )
    assert (Path(ws) / "big.py").read_text() == "H" * 100000
    assert "len 100000" in out


def test_runaway_code_times_out():
    ws = tempfile.mkdtemp()
    tool = CodeModeTool(ws, lambda *a: None, _API, timeout_s=2)
    out = asyncio.run(tool.execute(code="while True:\n    pass\n"))
    assert "timed out" in out


def test_tool_definition_shape():
    ws = tempfile.mkdtemp()
    tool = _tool(ws, [])
    assert tool.name == "code"
    assert "code" in tool.parameters["properties"]
    assert "write(file_path, content)" in tool.description
    assert "read(file_path)" in tool.description
