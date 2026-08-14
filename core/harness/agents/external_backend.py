"""External CLIs (Codex, Claude Code) as one-shot sub-agent backends.

Borrowed from dsh's ``subagent-codex`` / ``subagent-claude-code`` provider
contracts: each accepted run starts the official CLI in the delegating
session's workspace, submits one self-contained text task, and returns ONLY
the final answer. The child is a separate product with its own model,
credentials, approval policy, and sandbox — this module deliberately neither
copies nor overrides any of that (the dsh rule: read the host's normal
settings, filter nothing). Acceptance is strict and per-CLI; anything short
of an explicit success raises, so the parent's errors-as-data envelope
reports what happened instead of passing a blank string off as an answer.

Environment: children start from the credential-scrubbed parent environment
(their own auth stores under ``$HOME`` keep working; the harness's provider
keys never leak), then the user's ``subagents.<backend>.env`` config layer
merges on top — the supported way to hand a CLI the same proxy variables the
user's shell functions give it interactively.

Every run has a wall-clock budget (``subagents.<backend>.timeoutS``, default
30 minutes): an external CLI that hangs would otherwise hold one of the
session's few concurrent sub-agent slots forever. Expiry and cancellation
both tear down the child's whole process tree and wait for exit.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Protocol

from core.agent_runtime.processes import (
    subprocess_group_kwargs,
    terminate_process_tree,
)
from core.config import home_config_path

# Credential-shaped environment names are not forwarded to children — the
# harness's own provider keys must not leak into a spawned CLI implicitly.
# ``PATH``, ``HOME``, locale, and proxy variables survive, so the CLI runs
# normally and reads its own credential store; a deliberately forwarded
# secret goes through the config env layer, which merges after the scrub.
SENSITIVE_ENV_PATTERN = re.compile(r"KEY|PASSWORD|SECRET|TOKEN", re.IGNORECASE)

# Wall-clock budget for one external run, unless config overrides it. Long
# enough for a real subtask; short enough that a hung CLI frees its slot.
_DEFAULT_RUN_TIMEOUT_S = 1800.0

# Escalation grace between SIGTERM and SIGKILL on teardown.
_DISPOSE_GRACE_S = 3.0

_STDERR_TAIL_CHARS = 500


class ExternalBackendError(RuntimeError):
    """The external child did not produce an acceptable final answer."""


class ExternalCli(Protocol):
    """One CLI's spawn-and-accept contract; the runner owns everything else."""

    name: str
    executable: str

    def argv(self, answer_path: Path) -> list[str]:
        """Arguments after the executable; the task itself travels on stdin."""
        ...

    def accept(
        self, returncode: int, stdout: bytes, stderr: bytes, answer_path: Path
    ) -> str:
        """Return the final answer, or raise :class:`ExternalBackendError`."""
        ...


class CodexCli:
    """``codex exec`` headless mode.

    Codex writes the agent's final message to ``--output-last-message`` and
    does not write it on a failed turn, so "exit 0 AND a non-blank file" is
    the whole acceptance test.
    """

    name = "codex"
    executable = "codex"

    def argv(self, answer_path: Path) -> list[str]:
        return [
            "exec",
            # The worktree a sub-agent runs in is a git checkout, but a plain
            # shared workspace need not be; the repo check is the parent
            # session's concern, not the child's.
            "--skip-git-repo-check",
            "--output-last-message",
            str(answer_path),
            "-",  # read the task from stdin (argv has a platform length limit)
        ]

    def accept(
        self, returncode: int, stdout: bytes, stderr: bytes, answer_path: Path
    ) -> str:
        if returncode != 0:
            raise ExternalBackendError(
                f"codex exec failed with exit code {returncode}" + _stderr_tail(stderr)
            )
        try:
            answer = answer_path.read_text(encoding="utf-8").strip()
        except OSError:
            answer = ""
        if not answer:
            raise ExternalBackendError(
                "codex exec exited 0 but produced no final message"
                + _stderr_tail(stderr)
            )
        return answer


class ClaudeCodeCli:
    """``claude -p`` headless mode with JSON output.

    Acceptance mirrors dsh's claude-code provider: only a result envelope
    with ``subtype == "success"``, ``is_error`` false, and a non-blank
    ``result`` is an answer. Every error subtype, an error-marked success,
    or a missing answer maps to a loud error — never a fabricated reply.
    """

    name = "claude-code"
    executable = "claude"

    def argv(self, answer_path: Path) -> list[str]:
        del answer_path  # the answer rides stdout JSON, not a file
        return ["-p", "--output-format", "json"]

    def accept(
        self, returncode: int, stdout: bytes, stderr: bytes, answer_path: Path
    ) -> str:
        del answer_path
        if returncode != 0:
            raise ExternalBackendError(
                f"claude -p failed with exit code {returncode}" + _stderr_tail(stderr)
            )
        try:
            envelope = json.loads(stdout.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise ExternalBackendError(
                f"claude -p produced unparseable output: {exc}" + _stderr_tail(stderr)
            ) from exc
        subtype = envelope.get("subtype")
        answer = str(envelope.get("result") or "").strip()
        if (
            envelope.get("type") != "result"
            or subtype != "success"
            or envelope.get("is_error")
            or not answer
        ):
            raise ExternalBackendError(
                f"claude -p returned no accepted answer (subtype={subtype!r}, "
                f"is_error={envelope.get('is_error')!r})" + _stderr_tail(stderr)
            )
        return answer


BACKENDS: dict[str, ExternalCli] = {
    CodexCli.name: CodexCli(),
    ClaudeCodeCli.name: ClaudeCodeCli(),
}


def resolve_backend(name: str) -> ExternalCli:
    try:
        return BACKENDS[name]
    except KeyError:
        raise ExternalBackendError(
            f"unknown external backend {name!r}; "
            f"available: {', '.join(sorted(BACKENDS))}"
        ) from None


def resolve_executable(cli: ExternalCli) -> str:
    """Fail loud at spawn time, not minutes later inside a background task."""
    resolved = shutil.which(cli.executable)
    if resolved is None:
        raise ExternalBackendError(
            f"{cli.executable!r} was not found on PATH — install the "
            f"{cli.name} CLI to use this backend"
        )
    return resolved


def backend_settings(name: str) -> dict[str, Any]:
    """The user's ``subagents.<name>`` config block, ``{}`` when absent.

    Read from the home config only: which CLIs exist and how they reach the
    network is a per-machine fact, like the provider keys stored beside it.
    """
    try:
        with open(home_config_path(), encoding="utf-8") as fh:
            config = json.load(fh) or {}
    except (OSError, json.JSONDecodeError):
        return {}
    block = (config.get("subagents") or {}).get(name)
    return block if isinstance(block, dict) else {}


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


async def run_external_subagent(
    backend_name: str,
    task: str,
    workspace: str | Path,
    *,
    timeout_s: float | None = None,
    extra_env: dict[str, str] | None = None,
) -> str:
    """Run one self-contained task through an external CLI; return its answer.

    Raises :class:`ExternalBackendError` for every non-success outcome —
    a failed exit, a rejected envelope, a blank answer, or an exhausted
    run budget.
    """
    cli = resolve_backend(backend_name)
    executable = resolve_executable(cli)
    settings = backend_settings(cli.name)

    config_env = settings.get("env")
    merged_env: dict[str, str] = {}
    if isinstance(config_env, dict):
        merged_env.update({str(k): str(v) for k, v in config_env.items()})
    if extra_env:
        merged_env.update(extra_env)

    budget = timeout_s
    if budget is None:
        raw = settings.get("timeoutS")
        budget = (
            float(raw)
            if isinstance(raw, (int, float)) and raw > 0
            else _DEFAULT_RUN_TIMEOUT_S
        )

    with tempfile.TemporaryDirectory(prefix="deepcode-subagent-") as scratch:
        answer_path = Path(scratch) / "last-message.txt"
        process = await asyncio.create_subprocess_exec(
            executable,
            *cli.argv(answer_path),
            cwd=str(workspace),
            env=scrubbed_parent_env(merged_env),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **subprocess_group_kwargs(),
        )
        try:
            async with asyncio.timeout(budget):
                stdout, stderr = await process.communicate(
                    task.encode("utf-8", errors="replace")
                )
        except TimeoutError:
            await terminate_process_tree(process, grace_seconds=_DISPOSE_GRACE_S)
            raise ExternalBackendError(
                f"{cli.name} run exceeded its {budget:g}s budget and was terminated"
            ) from None
        except asyncio.CancelledError:
            await terminate_process_tree(process, grace_seconds=_DISPOSE_GRACE_S)
            raise
        return cli.accept(process.returncode or 0, stdout, stderr, answer_path)


def _stderr_tail(stderr: bytes | None) -> str:
    if not stderr:
        return ""
    text = stderr.decode("utf-8", errors="replace").strip()
    if not text:
        return ""
    return f"; stderr: {text[-_STDERR_TAIL_CHARS:]}"


__all__ = [
    "BACKENDS",
    "ExternalBackendError",
    "backend_settings",
    "resolve_backend",
    "resolve_executable",
    "run_external_subagent",
    "scrubbed_parent_env",
]
