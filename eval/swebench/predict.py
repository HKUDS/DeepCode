"""Prediction stage — turn an instance into a ``model_patch`` via the agent.

The flow mirrors what a real SWE-bench prediction run does:

1. Check the repo out at ``base_commit`` into a scratch workspace.
2. Run ``deepcode exec`` there with the issue as the prompt (the agent edits
   files with the native tools).
3. Capture ``git diff`` of the workspace as the ``model_patch``, emitted in
   the SWE-bench predictions schema
   ``{instance_id, model_name_or_path, model_patch}``.

The agent invocation is injected (:func:`default_exec_runner` shells out to
``python -m cli.exec_cli``) so the pure diff/prepare logic is testable without
a model — mechanism over hardcoding (DEEPCODE_V2_MASTER_PLAN.md §3.4).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Callable

from eval.swebench.instance import Instance

# A runner takes (workspace, prompt, model) and returns whether the agent
# turn completed cleanly. Swapped for a fake in tests.
ExecRunner = Callable[[str, str, str], bool]

_EXEC_TIMEOUT_S = 900


def _run(cmd: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def prepare_workspace(instance: Instance, dest: str | Path) -> str:
    """Check ``instance`` out at ``base_commit`` into ``dest``; return its path.

    Local instances clone from their prepared ``repo_path`` (offline); official
    instances clone ``https://github.com/<repo>``. Either way the workspace is
    a git repo at ``base_commit`` so a later ``git diff`` is exactly the model's
    change.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if instance.repo_path:
        source = instance.repo_path
    elif instance.repo:
        source = f"https://github.com/{instance.repo}.git"
    else:
        raise ValueError(f"instance {instance.instance_id} has no repo to clone")

    clone = _run(["git", "clone", "-q", source, str(dest)])
    if clone.returncode != 0:
        raise RuntimeError(f"clone failed for {instance.instance_id}: {clone.stderr}")
    if instance.base_commit:
        co = _run(["git", "checkout", "-q", instance.base_commit], cwd=str(dest))
        if co.returncode != 0:
            raise RuntimeError(f"checkout {instance.base_commit} failed: {co.stderr}")
    return str(dest)


# Bytecode/cache junk an agent leaves behind when it runs the tests. If these
# land in the model_patch, `git apply` chokes on the binary .pyc hunks and the
# whole patch is rejected — so they are excluded from the captured diff.
_DIFF_EXCLUDES = (
    ":(exclude)**/__pycache__/**",
    ":(exclude)**/*.pyc",
    ":(exclude)**/*.pyo",
    ":(exclude)**/.pytest_cache/**",
    ":(exclude)**/*.egg-info/**",
)


def capture_patch(workspace: str | Path) -> str:
    """Return the workspace's diff vs HEAD, including new files.

    ``git add -A`` then ``git diff --cached`` captures adds, edits, deletes and
    renames in one unified diff — the SWE-bench ``model_patch`` format —
    excluding Python bytecode/cache artifacts the agent generates when it runs
    the tests (they would make the patch fail to apply).
    """
    ws = str(workspace)
    _run(["git", "add", "-A"], cwd=ws)
    diff = _run(["git", "diff", "--cached", "--", ".", *_DIFF_EXCLUDES], cwd=ws)
    return diff.stdout


def default_exec_runner(workspace: str, prompt: str, model: str) -> bool:
    """Drive ``deepcode exec`` as a subprocess; True if the turn completed.

    Uses the current interpreter so the harness's environment (proxy, deps,
    config) flows through. Reads the final NDJSON event's stop_reason.
    """
    cmd = [
        sys.executable,
        "-m",
        "cli.exec_cli",
        "--workspace",
        workspace,
        "--json",
    ]
    if model:
        cmd += ["--model", model]
    cmd.append(prompt)
    repo_root = str(Path(__file__).resolve().parents[2])
    try:
        proc = subprocess.run(
            cmd,
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=_EXEC_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return False
    stop_reason = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("msg", {}).get("type") == "task_complete":
            stop_reason = event["msg"].get("stop_reason")
    return stop_reason == "completed"


def generate_prediction(
    instance: Instance,
    workspace: str | Path,
    *,
    model: str,
    runner: ExecRunner = default_exec_runner,
) -> dict:
    """Run the agent on ``instance`` and return a SWE-bench prediction dict.

    The returned dict is exactly the predictions schema plus a private
    ``_completed`` flag the caller may use for diagnostics.
    """
    completed = runner(str(workspace), instance.problem_statement, model)
    patch = capture_patch(workspace)
    return {
        "instance_id": instance.instance_id,
        "model_name_or_path": model or "deepcode",
        "model_patch": patch,
        "_completed": completed,
    }
