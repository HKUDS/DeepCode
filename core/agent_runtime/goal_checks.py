"""Run a Goal's confirmed checks: commands the user approved, run by DeepCode.

When the agent claims a Goal complete, each check with a command runs in the
workspace as an argument list, with no shell: no pipes, redirections or
globbing, so a pipeline cannot hide a failure and the exit code is the
command's own. (A check the user wrote with shell syntax is stored as
``bash -o pipefail -c ...``, so a failing stage still fails it.) Each check
has a timeout and the output kept is capped. The claim stands only when every
check exits 0.

A check runs with exactly the rights of the agent's own commands: in the same
command sandbox (writes fenced to the workspace) and environment. The agent
may have written the code a check runs, so a check must not give that code
more than the agent has.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import shlex
import signal
import time
from dataclasses import dataclass

from core.agent_runtime.processes import start_process_group, terminate_process_tree

CHECK_TIMEOUT_SECONDS = 600
# All checks of one claim together; later checks are reported as not run.
CHECKS_TOTAL_SECONDS = 1_800
CHECK_OUTPUT_MAX_CHARS = 4_000
_READ_CHUNK = 65_536
_KEEP_BYTES = 64 * 1024
# After the command exits, how long its output may still arrive: a process it
# left running in the background may hold the pipe open for good.
_DRAIN_SECONDS = 2.0
# The requirements one shared command proves, as one run names them.
_REQUIREMENTS_MAX_CHARS = 1_000


@dataclass(frozen=True, slots=True)
class GoalCheckSpec:
    requirement: str
    command: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GoalCheckRun:
    """How one check went. ``exit_code`` is None when it did not finish."""

    requirement: str
    command: tuple[str, ...]
    exit_code: int | None
    output: str
    seconds: float
    problem: str = ""

    @property
    def passed(self) -> bool:
        return self.exit_code == 0

    def describe(self) -> str:
        command = " ".join(self.command)
        if self.passed:
            return f"`{command}` exited 0"
        if self.exit_code is None:
            return f"`{command}` {self.problem}"
        return f"`{command}` exited {self.exit_code}"


async def run_checks(
    checks: tuple[GoalCheckSpec, ...],
    *,
    cwd: str,
    env: dict[str, str] | None = None,
    timeout_seconds: float = CHECK_TIMEOUT_SECONDS,
    total_seconds: float = CHECKS_TOTAL_SECONDS,
    sandbox_enabled: bool | None = None,
) -> tuple[GoalCheckRun, ...]:
    """Run every distinct command once, in order; return how each went.

    Checks that share a command (several requirements proved by one test
    suite) are run once, as one run naming all of their requirements.
    """

    runs: list[GoalCheckRun] = []
    deadline = time.monotonic() + total_seconds
    for check in _merged(checks):
        left = deadline - time.monotonic()
        if left <= 0:
            runs.append(
                GoalCheckRun(
                    check.requirement,
                    check.command,
                    None,
                    "",
                    0.0,
                    problem=(
                        f"was not run: the checks used up their {int(total_seconds)}s"
                    ),
                )
            )
            continue
        runs.append(
            await _run_one(
                check,
                cwd=cwd,
                env=env,
                timeout_seconds=min(timeout_seconds, left),
                sandbox_enabled=sandbox_enabled,
            )
        )
    return tuple(runs)


def _merged(checks: tuple[GoalCheckSpec, ...]) -> list[GoalCheckSpec]:
    requirements: dict[tuple[str, ...], list[str]] = {}
    for check in checks:
        if check.command:
            requirements.setdefault(check.command, []).append(check.requirement)
    merged = []
    for command, names in requirements.items():
        joined = "; ".join(names)
        if len(joined) > _REQUIREMENTS_MAX_CHARS:
            joined = joined[: _REQUIREMENTS_MAX_CHARS - 1] + "…"
        merged.append(GoalCheckSpec(requirement=joined, command=command))
    return merged


async def _run_one(
    check: GoalCheckSpec,
    *,
    cwd: str,
    env: dict[str, str] | None,
    timeout_seconds: float,
    sandbox_enabled: bool | None,
) -> GoalCheckRun:
    from core.harness.sandbox import build_exec_command

    started = time.monotonic()

    def unfinished(problem: str, output: bytes | bytearray = b"") -> GoalCheckRun:
        return GoalCheckRun(
            check.requirement,
            check.command,
            None,
            _tail(output),
            time.monotonic() - started,
            problem=problem,
        )

    try:
        wrapped = build_exec_command(
            argv=_runnable(check.command, env),
            workspace=cwd,
            enabled=sandbox_enabled,
        )
    except OSError as exc:
        return unfinished(f"could not start: {exc}")
    try:
        try:
            process = await start_process_group(
                *wrapped.argv,
                cwd=cwd,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as exc:
            return unfinished(f"could not start: {exc}")
        kept = bytearray()

        async def read_output() -> None:
            assert process.stdout is not None
            while chunk := await process.stdout.read(_READ_CHUNK):
                kept.extend(chunk)
                if len(kept) > _KEEP_BYTES:
                    del kept[: len(kept) - _KEEP_BYTES]

        reader = asyncio.create_task(read_output())
        try:
            if not await _exits_within(process, timeout_seconds):
                return unfinished(f"timed out after {int(timeout_seconds)}s", kept)
            # At once: whatever the command left running goes with it, while
            # its process group still holds the number.
            _kill_group(process)
            try:
                await asyncio.wait_for(asyncio.shield(reader), timeout=_DRAIN_SECONDS)
            except TimeoutError:
                pass
            return GoalCheckRun(
                check.requirement,
                check.command,
                process.returncode,
                _tail(kept),
                time.monotonic() - started,
            )
        finally:
            reader.cancel()
            if process.returncode is None:
                await terminate_process_tree(process)
    finally:
        wrapped.cleanup()


async def _exits_within(process: asyncio.subprocess.Process, seconds: float) -> bool:
    """Whether the command exits in time.

    ``process.wait()`` also waits for its pipes to close, which a process the
    command left in the background can put off for good; the exit code is
    known as soon as the command itself exits.
    """

    deadline = time.monotonic() + seconds
    while process.returncode is None:
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.05)
    return True


# cmd.exe acts on these even inside an argument list2cmdline left unquoted.
_CMD_SPECIAL = frozenset('&|<>^%!"\n\r')


def _runnable(command: tuple[str, ...], env: dict[str, str] | None) -> list[str]:
    """The argument list to start.

    ``bash`` is the bash the agent's commands use (on Windows, Git's, never
    the WSL launcher). On Windows a ``.cmd`` or ``.bat`` program (``npm``,
    ``yarn``) cannot be started directly; it runs the way the agent runs it,
    through that bash, with its arguments quoted for bash. cmd.exe may still
    end up reading them, so an argument it would take for a command (``&``,
    ``%VAR%``...) is refused (:class:`OSError`).
    """

    from core.harness.sandbox import default_shell

    argv = list(command)
    if argv[0] == "bash":
        argv[0] = default_shell()
    if os.name != "nt":
        return argv
    found = shutil.which(argv[0], path=(env or os.environ).get("PATH"))
    if found and found.lower().endswith((".cmd", ".bat")):
        if any(character in _CMD_SPECIAL for part in argv[1:] for character in part):
            raise OSError(
                f"{argv[0]} runs through cmd.exe, which would read one of its "
                "arguments as a command; remove & | < > ^ % ! or quotes from them"
            )
        return [default_shell(), "-c", shlex.join(argv)]
    return [found, *argv[1:]] if found else argv


def _kill_group(process: asyncio.subprocess.Process) -> None:
    """End what an exited command left running in its process group.

    Windows has no process groups to signal; there the job-object sandbox
    ends the tree when it is on.
    """

    if os.name == "nt":
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _tail(raw: bytes | bytearray) -> str:
    text = bytes(raw).decode("utf-8", errors="replace")
    if len(text) <= CHECK_OUTPUT_MAX_CHARS:
        return text
    return "…" + text[-(CHECK_OUTPUT_MAX_CHARS - 1) :]


__all__ = [
    "CHECK_OUTPUT_MAX_CHARS",
    "CHECK_TIMEOUT_SECONDS",
    "CHECKS_TOTAL_SECONDS",
    "GoalCheckRun",
    "GoalCheckSpec",
    "run_checks",
]
