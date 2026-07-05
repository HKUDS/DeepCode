"""Local (Docker-free) scorer for self-contained instances.

Faithful to SWE-bench's resolution rule, minus the container: apply the
model's patch to a **clean** checkout at ``base_commit`` (so we measure the
patch, not whatever else the agent left in its scratch dir), then run the
tests. An instance is *resolved* iff

* every ``fail_to_pass`` test now passes, **and**
* every ``pass_to_pass`` test still passes.

The official Verified set is scored by the ``swebench`` Docker harness
instead (:mod:`eval.swebench.run`); this path is for fast iteration on the
local benchmark where a real resolved-rate can be produced without Docker.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from eval.swebench.instance import Instance
from eval.swebench.predict import prepare_workspace
from eval.swebench.report import ResultRow


def _pytest(workspace: str, node_ids: list[str]) -> tuple[bool, str]:
    """Run the given pytest node ids in ``workspace``; True iff all pass."""
    if not node_ids:
        return True, "no tests"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *node_ids],
        cwd=workspace,
        capture_output=True,
        text=True,
    )
    ok = proc.returncode == 0
    tail = (proc.stdout or proc.stderr).strip().splitlines()[-1:] or [""]
    return ok, tail[0]


def _apply_patch(workspace: str, patch: str) -> bool:
    if not patch.strip():
        return False
    with tempfile.NamedTemporaryFile(
        "w", suffix=".patch", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(patch)
        patch_file = fh.name
    proc = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", patch_file],
        cwd=workspace,
        capture_output=True,
        text=True,
    )
    Path(patch_file).unlink(missing_ok=True)
    return proc.returncode == 0


def evaluate_local(
    instance: Instance,
    prediction: dict,
    *,
    scratch_root: str | Path,
) -> ResultRow:
    """Score one prediction against a fresh checkout. Never raises."""
    patch = prediction.get("model_patch", "") or ""
    patch_generated = bool(patch.strip())
    eval_dir = Path(scratch_root) / f"eval_{instance.instance_id}"
    try:
        prepare_workspace(instance, eval_dir)
    except Exception as exc:  # clone/checkout failure → unresolved, explained
        return ResultRow(
            instance_id=instance.instance_id,
            resolved=False,
            fail_to_pass_ok=False,
            pass_to_pass_ok=False,
            patch_generated=patch_generated,
            detail=f"setup failed: {exc}",
        )

    applied = _apply_patch(str(eval_dir), patch)
    if not applied:
        return ResultRow(
            instance_id=instance.instance_id,
            resolved=False,
            fail_to_pass_ok=False,
            pass_to_pass_ok=False,
            patch_generated=patch_generated,
            detail="empty patch" if not patch_generated else "patch did not apply",
        )

    f2p_ok, f2p_detail = _pytest(str(eval_dir), instance.fail_to_pass)
    p2p_ok, p2p_detail = _pytest(str(eval_dir), instance.pass_to_pass)
    resolved = f2p_ok and p2p_ok
    return ResultRow(
        instance_id=instance.instance_id,
        resolved=resolved,
        fail_to_pass_ok=f2p_ok,
        pass_to_pass_ok=p2p_ok,
        patch_generated=patch_generated,
        detail=f"FAIL_TO_PASS: {f2p_detail} | PASS_TO_PASS: {p2p_detail}",
    )
