"""Turn a permission ``ask`` into a human decision.

The permission engine (:mod:`core.harness.permissions`) only *decides*
allow / ask / deny. When it returns ``ask``, something has to resolve it.
This module provides a terminal approver: a callable
``(tool_name, arguments, reason) -> bool`` the kernel wires in as
``AgentRunSpec.approval_callback``.

Altitude note: the autonomous implementation phase runs FULL_AUTO (no asks),
so this approver is opt-in — attached only when a run uses ``default`` /
``plan`` mode (e.g. a security-conscious user sets
``DEEPCODE_PERMISSION_MODE=default``). Interactive-by-default belongs to the
general agent loop (P2), not to the batch reproduction pipeline.

Choices: ``y`` allow once · ``n`` deny · ``a``/``always`` allow and remember
this tool for the rest of the session. Non-interactive stdin → deny
(fail-closed): if there's no human, an ``ask`` cannot be granted.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Mapping
from typing import Any

_PROMPTABLE_ARG_KEYS = (
    "command",
    "cmd",
    "file_path",
    "path",
    "script",
    "target",
)


def _summarize_arguments(arguments: Mapping[str, Any]) -> str:
    for key in _PROMPTABLE_ARG_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            shown = value if len(value) <= 200 else value[:200] + "…"
            return f"{key}={shown!r}"
    if not arguments:
        return "(no arguments)"
    joined = ", ".join(f"{k}={v!r}" for k, v in list(arguments.items())[:3])
    return joined[:200]


class TerminalApprover:
    """Prompt a human on the terminal to approve a gated tool call.

    Parameters allow injecting IO for testing. ``always``-approved tool
    names are remembered on the instance for the session's lifetime.
    """

    def __init__(
        self,
        *,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = lambda s: print(s, flush=True),
        is_interactive: Callable[[], bool] | None = None,
    ) -> None:
        self._input = input_fn
        self._output = output_fn
        self._is_interactive = is_interactive or (
            lambda: sys.stdin is not None and sys.stdin.isatty()
        )
        self._always_allow: set[str] = set()

    def __call__(
        self,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
        reason: str | None = None,
    ) -> bool:
        arguments = arguments or {}
        if tool_name in self._always_allow:
            return True
        if not self._is_interactive():
            self._output(
                f"⚠️  '{tool_name}' needs approval but stdin is not interactive; "
                "denying (set DEEPCODE_PERMISSION_MODE=full_auto to run "
                "unattended)."
            )
            return False

        self._output("")
        self._output(f"🔐 Approval required for tool: {tool_name}")
        self._output(f"   {_summarize_arguments(arguments)}")
        if reason:
            self._output(f"   reason: {reason}")
        try:
            choice = self._input("   Allow? [y]es / [n]o / [a]lways: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            self._output("   → denied (input interrupted)")
            return False

        if choice in ("a", "always"):
            self._always_allow.add(tool_name)
            self._output(f"   → allowed (and remembered for {tool_name})")
            return True
        if choice in ("y", "yes"):
            self._output("   → allowed once")
            return True
        self._output("   → denied")
        return False

    def as_async(self) -> Callable[[str, Mapping[str, Any], str | None], Any]:
        """Return an async wrapper safe to use inside the agent loop.

        ``input()`` blocks, so the actual prompt runs in a worker thread to
        avoid stalling the event loop.
        """

        async def _acall(
            tool_name: str,
            arguments: Mapping[str, Any] | None = None,
            reason: str | None = None,
        ) -> bool:
            return await asyncio.to_thread(self.__call__, tool_name, arguments, reason)

        return _acall
