"""Test backpressure — the loop's ground truth (P3).

Every round ends by *actually running the project's tests* and reading the
exit code. This is the load-bearing anti-pattern-avoidance of loop
engineering: progress is judged by a real test command, never by the model's
own say-so (the ClawTeam/AgentSpace lesson — §8, "验证真执行"). The result,
including a stable failure *signature*, feeds the stop policy (stall
detection) and the next round's prompt (so the agent fixes the real failure).

Pure mechanism: run a shell command in the workspace, classify by exit code,
fingerprint the failure. No agent, no LLM.
"""

from __future__ import annotations

import hashlib
import re
import shlex
import subprocess
from dataclasses import dataclass

_TAIL_CHARS = 3000  # how much failing output to carry back to the agent
_DEFAULT_TIMEOUT = 600


@dataclass(frozen=True)
class TestResult:
    """The outcome of one test-backpressure run."""

    ran: bool  # False when there is no test command to run
    passed: bool
    returncode: int
    summary: str  # one-line human summary
    output_tail: str  # trailing output (for the agent to read on failure)
    signature: str  # stable fingerprint of the failure (empty when passed/not-run)


# Volatile substrings that must not leak into a failure signature, or two
# identical failures would look different round to round (defeating stall
# detection): durations, hex addresses, temp paths, line-timing.
_VOLATILE = re.compile(
    r"\b\d+\.\d+s\b"  # "0.42s"
    r"|0x[0-9a-fA-F]+"  # addresses
    r"|/tmp/[^\s:]+|/var/folders/[^\s:]+|/private/[^\s:]+"  # temp paths
    r"|\bin \d+(\.\d+)?s\b"  # "in 3s"
)


def _signature(output: str) -> str:
    """A stable digest of failure output, robust to volatile noise.

    Prefers the set of pytest failure lines (``FAILED test::name``) when
    present; otherwise digests the normalized tail. Same failure → same
    signature across rounds.
    """
    failed_lines = sorted(set(re.findall(r"^FAILED .+$", output, flags=re.MULTILINE)))
    basis = "\n".join(failed_lines) if failed_lines else output[-_TAIL_CHARS:]
    normalized = _VOLATILE.sub("·", basis).strip()
    return hashlib.sha256(normalized.encode("utf-8", "replace")).hexdigest()[:16]


def run_tests(
    workspace: str,
    command: str,
    *,
    timeout: int = _DEFAULT_TIMEOUT,
) -> TestResult:
    """Run ``command`` in ``workspace``; classify pass/fail by exit code.

    A blank command means "no tests configured" — returns ``ran=False`` so the
    policy treats the round as unverifiable rather than passing.
    """
    if not command.strip():
        return TestResult(
            ran=False,
            passed=False,
            returncode=0,
            summary="no test command configured",
            output_tail="",
            signature="",
        )
    try:
        proc = subprocess.run(
            shlex.split(command),
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return TestResult(
            ran=True,
            passed=False,
            returncode=-1,
            summary=f"tests timed out after {timeout}s",
            output_tail="(timed out)",
            signature="timeout",
        )
    except (OSError, ValueError) as exc:
        return TestResult(
            ran=True,
            passed=False,
            returncode=-1,
            summary=f"could not run tests: {exc}",
            output_tail=str(exc),
            signature=_signature(str(exc)),
        )

    output = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    passed = proc.returncode == 0
    last_line = next((ln for ln in reversed(output.splitlines()) if ln.strip()), "")
    summary = f"tests passed ({last_line})" if passed else f"tests failed ({last_line})"
    return TestResult(
        ran=True,
        passed=passed,
        returncode=proc.returncode,
        summary=summary[:200],
        output_tail=output[-_TAIL_CHARS:],
        signature="" if passed else _signature(output),
    )
