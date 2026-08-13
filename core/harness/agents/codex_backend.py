"""Codex CLI as a one-shot sub-agent backend.

Borrowed from dsh's ``subagent-codex`` provider contract: each accepted run
starts the official Codex CLI in the delegating session's workspace, submits
one self-contained text task, and returns ONLY the final answer. The child
inherits no parent conversation, persona, or tool filter — it is a separate
product with its own configuration, credentials, approval policy, and
sandbox, and this backend deliberately neither copies nor overrides any of
that (the dsh rule: read the host's normal settings, filter nothing).

Mechanically this rides ``codex exec`` headless mode with
``--output-last-message``: Codex writes the agent's final message to a file
we own, and does not write it on a failed turn — which makes "exit 0 AND a
non-blank file" the whole acceptance test. Anything else is an error; the
backend never fabricates a partial answer.

Process ownership follows the dsh subprocess service: the child runs in its
own process group, the environment is the credential-scrubbed parent
environment (Codex reads its own auth store under ``$HOME``), and teardown
escalates SIGTERM → grace → SIGKILL on the whole group, then waits for exit.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import signal
import tempfile
from pathlib import Path

# Credential-shaped environment names are not forwarded to the child — the
# harness's own provider keys must not leak into a spawned CLI implicitly.
# ``PATH``, ``HOME``, locale, and proxy variables survive, so the CLI runs
# normally and reads its own credential store; a deliberately forwarded
# secret goes through ``extra_env``, which merges after the scrub.
SENSITIVE_ENV_PATTERN = re.compile(r"KEY|PASSWORD|SECRET|TOKEN", re.IGNORECASE)

# Between SIGTERM and SIGKILL on teardown (dsh's disposeGraceMs default).
_DISPOSE_GRACE_S = 3.0

_STDERR_TAIL_CHARS = 500


class CodexBackendError(RuntimeError):
    """The Codex child did not produce an acceptable final answer."""


def scrubbed_parent_env(
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """The ambient environment minus credential-shaped names."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not SENSITIVE_ENV_PATTERN.search(key)
    }
    if extra_env:
        env.update(extra_env)
    return env


def resolve_codex_executable(executable: str = "codex") -> str:
    """Fail loud at spawn time, not minutes later inside a background task."""
    resolved = shutil.which(executable)
    if resolved is None:
        raise CodexBackendError(
            f"codex executable {executable!r} was not found on PATH — install "
            "the Codex CLI (or pass its full path) to use the codex backend"
        )
    return resolved


async def run_codex_subagent(
    task: str,
    workspace: str | Path,
    *,
    executable: str = "codex",
    extra_env: dict[str, str] | None = None,
    dispose_grace_s: float = _DISPOSE_GRACE_S,
) -> str:
    """Run one self-contained task through ``codex exec``; return its answer.

    Raises :class:`CodexBackendError` for every non-success outcome — a
    failed exit, a missing or blank final message — so the caller's
    errors-as-data envelope reports what actually happened instead of an
    empty string that reads like an answer.
    """
    resolved = resolve_codex_executable(executable)
    with tempfile.TemporaryDirectory(prefix="deepcode-codex-") as scratch:
        last_message = Path(scratch) / "last-message.txt"
        process = await asyncio.create_subprocess_exec(
            resolved,
            "exec",
            # The worktree a sub-agent runs in is a git checkout, but a plain
            # shared workspace need not be; Codex's repo check is the parent
            # session's concern, not the child's.
            "--skip-git-repo-check",
            "--output-last-message",
            str(last_message),
            # Read the task from stdin: argv has a platform length limit and
            # task texts are unbounded.
            "-",
            cwd=str(workspace),
            env=scrubbed_parent_env(extra_env),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,  # its own process group, for tree teardown
        )
        try:
            _stdout, stderr = await process.communicate(
                task.encode("utf-8", errors="replace")
            )
        except asyncio.CancelledError:
            await _terminate_group(process, dispose_grace_s)
            raise
        if process.returncode != 0:
            raise CodexBackendError(
                f"codex exec failed with exit code {process.returncode}"
                + _stderr_tail(stderr)
            )
        try:
            answer = last_message.read_text(encoding="utf-8").strip()
        except OSError:
            answer = ""
        # Codex leaves the file unwritten on a failed turn, so a blank answer
        # after exit 0 still means "no accepted final message".
        if not answer:
            raise CodexBackendError(
                "codex exec exited 0 but produced no final message"
                + _stderr_tail(stderr)
            )
        return answer


async def _terminate_group(process: asyncio.subprocess.Process, grace_s: float) -> None:
    """SIGTERM the child's process group, wait out the grace, SIGKILL, join."""
    if process.returncode is not None:
        return
    _signal_group(process, signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), timeout=grace_s)
        return
    except TimeoutError:
        pass
    _signal_group(process, signal.SIGKILL)
    await process.wait()


def _signal_group(process: asyncio.subprocess.Process, signum: int) -> None:
    try:
        os.killpg(process.pid, signum)
    except (ProcessLookupError, PermissionError, OSError):
        # The group is gone, or this platform cannot address it — fall back
        # to the direct child so teardown still converges.
        try:
            process.send_signal(signum)
        except ProcessLookupError:
            pass


def _stderr_tail(stderr: bytes | None) -> str:
    if not stderr:
        return ""
    text = stderr.decode("utf-8", errors="replace").strip()
    if not text:
        return ""
    return f"; stderr: {text[-_STDERR_TAIL_CHARS:]}"


__all__ = [
    "CodexBackendError",
    "resolve_codex_executable",
    "run_codex_subagent",
    "scrubbed_parent_env",
]
