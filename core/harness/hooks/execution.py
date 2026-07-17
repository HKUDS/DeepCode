"""Hook command execution + output decoding (C3).

Ports the reference agent's ``command_runner.rs`` (spawn a shell command, feed
the JSON payload on stdin, capture stdout/stderr with a timeout) and the
decision semantics of ``output_parser.rs`` + each event's ``parse_completed``
(the exit-code protocol and the Claude-Code-compatible stdout-JSON contract).

Exit-code protocol (identical to the reference and to Claude Code):

- **spawn/timeout error** → the handler failed; not blocking.
- **exit 0** → parse stdout as JSON and apply its decision; empty stdout is a
  no-op; non-JSON stdout is a no-op; JSON-shaped-but-invalid stdout fails.
- **exit 2** → block, using stderr as the reason (empty stderr → failed).
- **any other code** → failed; not blocking.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Any

from core.harness.hooks.discovery import Handler


@dataclass(slots=True)
class CommandResult:
    exit_code: int | None
    stdout: str
    stderr: str
    error: str | None
    duration_ms: int


@dataclass(slots=True)
class HandlerDecision:
    """One handler's normalized contribution to an event outcome."""

    status: str = "completed"  # completed | blocked | failed
    block: bool = False
    block_reason: str | None = None
    additional_context: str | None = None
    updated_input: dict[str, Any] | None = None
    permission: str | None = None  # PermissionRequest: "allow" | "deny"
    system_message: str | None = None
    invalid_reason: str | None = None
    error: str | None = None


def _default_shell() -> list[str]:
    if os.name == "nt":  # pragma: no cover - posix CI
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        return [comspec, "/C"]
    shell = os.environ.get("SHELL", "/bin/sh")
    return [shell, "-lc"]


async def run_command(handler: Handler, payload_json: str, cwd: str) -> CommandResult:
    """Run one hook command, feeding ``payload_json`` on stdin, with a timeout."""
    started = time.monotonic()
    argv = [*_default_shell(), handler.command]
    env = {**os.environ, **handler.env}
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )
    except OSError as exc:
        return CommandResult(None, "", "", f"failed to spawn hook: {exc}", 0)

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(payload_json.encode()), timeout=handler.timeout_sec
        )
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await proc.communicate()
        except Exception:  # noqa: BLE001 - best-effort reap after kill
            pass
        elapsed = int((time.monotonic() - started) * 1000)
        return CommandResult(
            None, "", "", f"hook timed out after {handler.timeout_sec}s", elapsed
        )

    elapsed = int((time.monotonic() - started) * 1000)
    return CommandResult(
        exit_code=proc.returncode,
        stdout=stdout.decode(errors="replace"),
        stderr=stderr.decode(errors="replace"),
        error=None,
        duration_ms=elapsed,
    )


# -- stdout JSON decoding --------------------------------------------------


def _looks_like_json(stdout: str) -> bool:
    trimmed = stdout.lstrip()
    return trimmed.startswith("{") or trimmed.startswith("[")


def _parse_json_object(stdout: str) -> dict | None:
    trimmed = stdout.strip()
    if not trimmed:
        return None
    try:
        value = json.loads(trimmed)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _trimmed_reason(reason: Any) -> str | None:
    if not isinstance(reason, str):
        return None
    trimmed = reason.strip()
    return trimmed or None


@dataclass(slots=True)
class _Universal:
    continue_processing: bool = True
    stop_reason: str | None = None
    suppress_output: bool = False
    system_message: str | None = None


def _parse_universal(obj: dict) -> _Universal:
    return _Universal(
        continue_processing=obj.get("continue", True) is not False,
        stop_reason=obj.get("stopReason"),
        suppress_output=bool(obj.get("suppressOutput", False)),
        system_message=obj.get("systemMessage"),
    )


# Per-event extraction of a decision from a parsed stdout object. Each returns a
# HandlerDecision with only the event-relevant fields set (universal + status
# are applied by the shared envelope in ``parse_handler_output``).


def _block_from_decision(obj: dict, event_label: str) -> HandlerDecision:
    """Shared ``{decision: block, reason}`` semantics (PostToolUse/UserPromptSubmit/Stop)."""
    dec = HandlerDecision()
    if obj.get("decision") == "block":
        reason = _trimmed_reason(obj.get("reason"))
        if reason is None:
            dec.invalid_reason = (
                f"{event_label} hook returned decision:block without a reason"
            )
        else:
            dec.block = True
            dec.block_reason = reason
    hook_specific = obj.get("hookSpecificOutput")
    if isinstance(hook_specific, dict):
        dec.additional_context = _trimmed_reason(hook_specific.get("additionalContext"))
    return dec


def _decode_pre_tool_use(obj: dict) -> HandlerDecision:
    dec = HandlerDecision()
    hook_specific = obj.get("hookSpecificOutput")
    hs = hook_specific if isinstance(hook_specific, dict) else {}
    permission = hs.get("permissionDecision")
    if permission is not None:
        # Modern contract: hookSpecificOutput.permissionDecision.
        if permission == "deny":
            reason = _trimmed_reason(hs.get("permissionDecisionReason"))
            if reason is None:
                dec.invalid_reason = (
                    "PreToolUse hook returned permissionDecision:deny without a reason"
                )
            else:
                dec.block = True
                dec.block_reason = reason
        elif permission == "allow":
            updated = hs.get("updatedInput")
            if isinstance(updated, dict):
                dec.updated_input = updated
        elif permission == "ask":
            dec.invalid_reason = (
                "PreToolUse hook returned unsupported permissionDecision:ask"
            )
    else:
        # Legacy contract: top-level decision:block + reason.
        if obj.get("decision") == "block":
            reason = _trimmed_reason(obj.get("reason"))
            if reason is None:
                dec.invalid_reason = (
                    "PreToolUse hook returned decision:block without a reason"
                )
            else:
                dec.block = True
                dec.block_reason = reason
    dec.additional_context = _trimmed_reason(hs.get("additionalContext"))
    return dec


def _decode_additional_context_only(obj: dict) -> HandlerDecision:
    """SessionStart / SubagentStart: only additionalContext is meaningful."""
    dec = HandlerDecision()
    hook_specific = obj.get("hookSpecificOutput")
    if isinstance(hook_specific, dict):
        dec.additional_context = _trimmed_reason(hook_specific.get("additionalContext"))
    return dec


def _decode_permission_request(obj: dict) -> HandlerDecision:
    """PermissionRequest: ``hookSpecificOutput.decision.behavior`` allow/deny."""
    dec = HandlerDecision()
    hook_specific = obj.get("hookSpecificOutput")
    decision = (
        hook_specific.get("decision") if isinstance(hook_specific, dict) else None
    )
    if not isinstance(decision, dict):
        return dec
    behavior = decision.get("behavior")
    if behavior == "allow":
        dec.permission = "allow"
    elif behavior == "deny":
        dec.permission = "deny"
        dec.block_reason = (
            _trimmed_reason(decision.get("message"))
            or "PermissionRequest hook denied approval"
        )
    return dec


_DECODERS = {
    "PreToolUse": _decode_pre_tool_use,
    "PostToolUse": lambda o: _block_from_decision(o, "PostToolUse"),
    "UserPromptSubmit": lambda o: _block_from_decision(o, "UserPromptSubmit"),
    "Stop": lambda o: _block_from_decision(o, "Stop"),
    "SubagentStop": lambda o: _block_from_decision(o, "SubagentStop"),
    "SessionStart": _decode_additional_context_only,
    "SubagentStart": _decode_additional_context_only,
    "PermissionRequest": _decode_permission_request,
}

# Events where a bare exit-2 (with a stderr reason) means "block".
_EXIT2_BLOCK_EVENTS = frozenset(
    {"PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop", "SubagentStop"}
)

# Events whose plain-text (non-JSON) stdout is injected verbatim as context —
# the canonical ``echo "some context"`` hook. Other events ignore plain stdout.
_TEXT_CONTEXT_EVENTS = frozenset({"SessionStart", "SubagentStart", "UserPromptSubmit"})
# Events where ``continue: false`` halts the action (turn stop, or "skip
# compaction" for PreCompact) — mapped to a block.
_STOP_ON_DISCONTINUE_EVENTS = frozenset(
    {"SessionStart", "SubagentStart", "UserPromptSubmit", "PreCompact"}
)
# Events where ``continue: false`` is unsupported and fails the handler.
_REJECT_DISCONTINUE_EVENTS = frozenset({"PreToolUse", "PermissionRequest"})


def parse_handler_output(event_name: str, result: CommandResult) -> HandlerDecision:
    """Apply the exit-code protocol + stdout-JSON decision for one handler run."""
    if result.error is not None:
        return HandlerDecision(status="failed", error=result.error)

    if result.exit_code == 2:
        reason = _trimmed_reason(result.stderr)
        if reason is None:
            return HandlerDecision(
                status="failed",
                error=f"{event_name} hook exited with code 2 without a stderr reason",
            )
        if event_name == "PermissionRequest":
            return HandlerDecision(
                status="blocked", permission="deny", block_reason=reason
            )
        if event_name in _EXIT2_BLOCK_EVENTS:
            return HandlerDecision(status="blocked", block=True, block_reason=reason)
        # SessionStart / SubagentStart have no block channel — exit 2 is a failure.
        return HandlerDecision(
            status="failed", error=f"{event_name} hook exited with code 2"
        )

    if result.exit_code != 0:
        code = result.exit_code
        msg = (
            "hook exited without a status code"
            if code is None
            else f"hook exited with code {code}"
        )
        return HandlerDecision(status="failed", error=msg)

    # exit 0: decode stdout, if any.
    obj = _parse_json_object(result.stdout)
    if obj is None:
        stripped = result.stdout.strip()
        if not stripped:
            return HandlerDecision(status="completed")
        if _looks_like_json(result.stdout):
            return HandlerDecision(
                status="failed", error=f"{event_name} hook returned invalid JSON output"
            )
        # Plain-text stdout becomes context for the context events; ignored else.
        if event_name in _TEXT_CONTEXT_EVENTS:
            return HandlerDecision(status="completed", additional_context=stripped)
        return HandlerDecision(status="completed")

    decoder = _DECODERS.get(event_name)
    dec = decoder(obj) if decoder else HandlerDecision()
    universal = _parse_universal(obj)
    dec.system_message = universal.system_message
    # `continue: false`: halt the turn for the context events; unsupported (and
    # thus a handler failure) for PreToolUse / PermissionRequest.
    if not universal.continue_processing and dec.invalid_reason is None:
        if event_name in _STOP_ON_DISCONTINUE_EVENTS:
            dec.block = True
            dec.block_reason = (
                _trimmed_reason(universal.stop_reason)
                or f"{event_name} hook requested stop"
            )
        elif event_name in _REJECT_DISCONTINUE_EVENTS:
            dec.invalid_reason = (
                f"{event_name} hook returned unsupported continue:false"
            )
    if dec.invalid_reason is not None:
        dec.status = "failed"
        dec.block = False
        dec.block_reason = None
        dec.updated_input = None
    elif dec.block:
        dec.status = "blocked"
    return dec
