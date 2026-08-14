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

from core.agent_runtime.tools import ToolRegistry
from core.agent_setup import _wire_code_mode
from core.domain.execution_security import (
    ExecutionAccessPreset,
    ExecutionSecurityProfile,
)
from core.harness.code_mode import (
    CodeModeTool,
    ToolAPISpec,
    api_from_definitions,
)
from core.harness.permissions import PermissionEngine, PermissionMode
from core.harness.tools.files import WriteTool

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


def test_code_mode_forwards_frozen_sandbox_choice(monkeypatch):
    import core.harness.code_mode.tool as code_mode_module

    workspace = tempfile.mkdtemp()
    captured = []
    real_build = code_mode_module.build_exec_command

    def capture_build(**kwargs):
        captured.append(kwargs["enabled"])
        return real_build(**kwargs)

    monkeypatch.setenv("DEEPCODE_SANDBOX", "1")
    monkeypatch.setattr(code_mode_module, "build_exec_command", capture_build)
    tool = CodeModeTool(
        workspace,
        lambda *_args: None,
        _API,
        sandbox_enabled=False,
    )

    out = asyncio.run(tool.execute(code="print('ok')\n"))

    assert captured == [False]
    assert "ok" in out
    assert "sandbox disabled" in tool.description


def _wired_write_code_tool(
    workspace: str,
    *,
    engine: PermissionEngine,
    profile: ExecutionSecurityProfile,
    approval_callback=None,
):
    registry = ToolRegistry()
    registry.register(
        WriteTool(
            workspace,
            diagnostics=lambda _path: [],
            allow_outside_workspace=(
                profile.access_preset is ExecutionAccessPreset.FULL_ACCESS
            ),
        )
    )
    _wire_code_mode(
        registry,
        workspace,
        engine,
        None,
        profile,
        approval_callback,
    )
    tool = registry.get("code")
    assert tool is not None
    return tool


def test_code_mode_ask_uses_shared_approver(tmp_path):
    calls = []

    async def approve(tool_name, arguments, reason):
        calls.append((tool_name, arguments, reason))
        return True

    profile = ExecutionSecurityProfile.for_preset(ExecutionAccessPreset.ASK)
    tool = _wired_write_code_tool(
        str(tmp_path),
        engine=PermissionEngine(
            mode=PermissionMode.DEFAULT,
            approval_policy=profile.approval_policy,
            cwd=str(tmp_path),
        ),
        profile=profile,
        approval_callback=approve,
    )

    out = asyncio.run(tool.execute(code="write('approved.txt', 'ok')\n"))

    assert (tmp_path / "approved.txt").read_text() == "ok"
    assert len(calls) == 1
    assert calls[0][0] == "write"
    assert calls[0][1] == {"file_path": "approved.txt", "content": "ok"}
    assert "requires confirmation" in calls[0][2]
    assert "permission denied" not in out


def test_code_mode_ask_rejection_is_fail_closed(tmp_path):
    profile = ExecutionSecurityProfile.for_preset(ExecutionAccessPreset.ASK)
    tool = _wired_write_code_tool(
        str(tmp_path),
        engine=PermissionEngine(
            mode=PermissionMode.DEFAULT,
            approval_policy=profile.approval_policy,
            cwd=str(tmp_path),
        ),
        profile=profile,
        approval_callback=lambda *_args: False,
    )

    out = asyncio.run(tool.execute(code="print(write('rejected.txt', 'no'))\n"))

    assert not (tmp_path / "rejected.txt").exists()
    assert "permission denied" in out
    assert "user rejected" in out


def test_code_mode_ask_approval_error_is_fail_closed(tmp_path):
    def broken_approver(*_args):
        raise RuntimeError("approval transport failed")

    profile = ExecutionSecurityProfile.for_preset(ExecutionAccessPreset.ASK)
    tool = _wired_write_code_tool(
        str(tmp_path),
        engine=PermissionEngine(
            mode=PermissionMode.DEFAULT,
            approval_policy=profile.approval_policy,
            cwd=str(tmp_path),
        ),
        profile=profile,
        approval_callback=broken_approver,
    )

    out = asyncio.run(tool.execute(code="print(write('errored.txt', 'no'))\n"))

    assert not (tmp_path / "errored.txt").exists()
    assert "approval request errored" in out
    assert "fail-closed" in out


def test_code_mode_read_only_denies_without_approval(tmp_path):
    approvals = []
    profile = ExecutionSecurityProfile.for_preset(ExecutionAccessPreset.READ_ONLY)
    tool = _wired_write_code_tool(
        str(tmp_path),
        engine=PermissionEngine(
            mode=PermissionMode.PLAN,
            approval_policy=profile.approval_policy,
            enforce_read_only=True,
            cwd=str(tmp_path),
        ),
        profile=profile,
        approval_callback=lambda *_args: approvals.append(True) or True,
    )

    out = asyncio.run(tool.execute(code="print(write('blocked.txt', 'no'))\n"))

    assert not (tmp_path / "blocked.txt").exists()
    assert approvals == []
    assert "permission denied" in out


def test_code_mode_full_access_does_not_request_approval(tmp_path):
    approvals = []
    profile = ExecutionSecurityProfile.for_preset(ExecutionAccessPreset.FULL_ACCESS)
    tool = _wired_write_code_tool(
        str(tmp_path),
        engine=PermissionEngine(
            mode=PermissionMode.FULL_AUTO,
            approval_policy=profile.approval_policy,
            protect_sensitive_paths=False,
            cwd=str(tmp_path),
        ),
        profile=profile,
        approval_callback=lambda *_args: approvals.append(True) or False,
    )

    out = asyncio.run(tool.execute(code="write('allowed.txt', 'yes')\n"))

    assert (tmp_path / "allowed.txt").read_text() == "yes"
    assert approvals == []
    assert "permission denied" not in out
