"""How long a client waits for each method's answer.

The service sends these in ``initialize`` so the Desktop host and the browser
wait as long as the native client does, instead of keeping their own copies.
"""

from __future__ import annotations

# Requests that wait on a model or an external process (a provider probe, a
# /compact summary, an MCP server start that may take up to its configured
# 300s) legitimately outlast the default budget; the server keeps working on
# them either way, so timing out early only hides a result that is coming.
SLOW_METHOD_TIMEOUTS = {
    "provider/test": 120.0,
    "thread/context/compact": 600.0,
    "mcp/probe": 330.0,
}
DEFAULT_TIMEOUT = 25.0


def request_timeout(method: str) -> float:
    return SLOW_METHOD_TIMEOUTS.get(method, DEFAULT_TIMEOUT)


def timeout_capabilities() -> dict:
    return {
        "defaultSeconds": DEFAULT_TIMEOUT,
        "methodSeconds": dict(SLOW_METHOD_TIMEOUTS),
    }
