"""Code-mode child harness (C5b) — runs inside the sandboxed subprocess.

The parent (``CodeModeTool``) spawns ``python -m core.harness.code_mode._runner``
under the platform sandbox and speaks a line-delimited JSON RPC over this
process's stdin/stdout. This harness:

1. reads an init message ``{"code", "tools"}`` — the model's Python source and
   the exposed tool specs;
2. binds each tool to a callable that RPCs the parent to run the *real* tool
   (so permission checks and hooks stay in the parent) and returns its result;
3. ``exec``s the model's code with those tools in scope, capturing its stdout
   into a buffer so it never corrupts the RPC channel on real stdout;
4. sends a final ``{"done", "output", "result", "error"}`` message.

Stdlib only — it must import cleanly in a minimal sandboxed interpreter. The
real stdout/stdin are captured up front; tool calls and the final message use
those saved handles while the model's ``print`` goes to the buffer.
"""

from __future__ import annotations

import io
import json
import sys
import traceback

# Saved before anything can reassign sys.stdout: RPC always uses these.
_RPC_OUT = sys.stdout
_RPC_IN = sys.stdin

# Bound the captured output so a runaway print cannot build a huge RPC line.
_MAX_CAPTURE = 200_000


def _cap(text: str | None) -> str | None:
    if text is None or len(text) <= _MAX_CAPTURE:
        return text
    return text[:_MAX_CAPTURE] + "\n… (output truncated)"


def _send(obj: dict) -> None:
    _RPC_OUT.write(json.dumps(obj) + "\n")
    _RPC_OUT.flush()


def _recv() -> dict | None:
    line = _RPC_IN.readline()
    if not line:
        return None
    return json.loads(line)


def _make_tool(name: str, params: list[str]):
    """A callable that maps (args, kwargs) to the tool's argument dict, RPCs the
    parent to execute the real tool, and returns its result string."""

    def _tool(*args, **kwargs):
        arguments = dict(kwargs)
        for index, value in enumerate(args):
            if index < len(params):
                arguments[params[index]] = value
            else:
                raise TypeError(f"{name}() got too many positional arguments")
        _send({"call": name, "args": arguments})
        response = _recv()
        if response is None:
            raise RuntimeError("code-mode bridge closed unexpectedly")
        if not response.get("ok", False):
            raise RuntimeError(response.get("error") or f"{name} failed")
        return response.get("value")

    _tool.__name__ = name
    return _tool


def main() -> int:
    init = _recv()
    if not init or "code" not in init:
        _send({"done": True, "output": "", "result": None, "error": "no code received"})
        return 1

    namespace: dict = {"__name__": "__code_mode__"}
    for spec in init.get("tools", []):
        namespace[spec["name"]] = _make_tool(spec["name"], spec.get("params", []))

    buffer = io.StringIO()
    result_repr = None
    error = None
    sys.stdout = buffer
    try:
        exec(compile(init["code"], "<code_mode>", "exec"), namespace)  # noqa: S102
        if "result" in namespace:
            try:
                result_repr = repr(namespace["result"])
            except Exception:
                result_repr = "<unrepresentable result>"
    except BaseException:  # noqa: BLE001 - report any failure back to the model
        error = traceback.format_exc()
    finally:
        sys.stdout = _RPC_OUT

    _send(
        {
            "done": True,
            "output": _cap(buffer.getvalue()),
            "result": _cap(result_repr),
            "error": error,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
