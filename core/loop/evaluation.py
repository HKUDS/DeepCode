"""P0-4: evaluation isolation protocol (PenguinHarness agent-evaluation lesson).

PenguinHarness' evaluation skill keeps the *subject* (the agent being
evaluated) from ever seeing private evaluation data:

* only the public ``statement/`` is copied into the isolated workspace;
* the private ``rubric/`` / gold answers never reach the subject;
* a pre/post snapshot comparison detects if the benchmark changed mid-run
  (``version_changed`` → evaluation invalid);
* every evaluation binds to a unique root Trace (workspace / agent state /
  provider / model match), and contamination aborts the run.

DeepCode already has permissions (sensitive-path denylist) and sandboxing
(seatbelt/bwrap); this module adds the *evaluation-specific* protocol on top:
what to expose, what to hide, and how to detect mid-run changes. Pure
mechanism — no LLM, no subprocess.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path

# Files/dirs that are public (safe to expose to the evaluated agent).
_PUBLIC_NAMES = ("statement", "README.md", "task.md", "instructions.md")
# Files/dirs that are private (must never reach the evaluated agent).
_PRIVATE_NAMES = (
    "rubric",
    "gold",
    "answer",
    "solution",
    "scoring",
    "private",
    ".hidden",
)

EVAL_OK = "ok"
EVAL_VERSION_CHANGED = "version_changed"
EVAL_BENCHMARK_INVALID = "benchmark_invalid"
EVAL_CONTAMINATED = "contaminated"
EVAL_NOT_FOUND = "not_found"


@dataclass
class EvaluationSetup:
    """Result of preparing an isolated evaluation workspace."""

    workspace: Path
    status: str = EVAL_OK
    detail: str = ""
    exposed: list[str] = field(default_factory=list)  # public paths copied
    hidden: list[str] = field(default_factory=list)  # private paths excluded


def _is_public(path: Path) -> bool:
    return path.name.lower() in _PUBLIC_NAMES


def _is_private(path: Path) -> bool:
    return path.name.lower() in _PRIVATE_NAMES or path.name.startswith(".")


def _tree_digest(root: Path) -> str:
    """A stable digest of a directory tree (file paths + contents)."""
    hasher = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            hasher.update(str(path.relative_to(root)).encode("utf-8", errors="replace"))
            hasher.update(b"\0")
            try:
                hasher.update(path.read_bytes()[:4096])
            except OSError:
                pass
            hasher.update(b"\0")
    return hasher.hexdigest()


def prepare_evaluation_workspace(
    benchmark_dir: str | Path,
    target_dir: str | Path,
    *,
    force: bool = False,
) -> EvaluationSetup:
    """Copy only the public parts of a benchmark into an isolated workspace.

    The evaluated agent sees exactly what ``_is_public`` allows; private
    rubric/gold files are excluded. Returns a setup whose ``status`` is
    ``ok``, or ``not_found`` / ``benchmark_invalid`` when the source is
    missing or exposes nothing public.
    """
    src = Path(benchmark_dir).resolve()
    dst = Path(target_dir).resolve()
    if not src.is_dir():
        return EvaluationSetup(
            workspace=dst, status=EVAL_NOT_FOUND, detail=f"missing {src}"
        )

    if dst.exists():
        if force:
            shutil.rmtree(dst)
        else:
            return EvaluationSetup(
                workspace=dst,
                status=EVAL_BENCHMARK_INVALID,
                detail=f"target exists (use force=True to rebuild): {dst}",
            )
    dst.mkdir(parents=True, exist_ok=True)

    exposed: list[str] = []
    hidden: list[str] = []
    for entry in sorted(src.iterdir()):
        if _is_private(entry):
            hidden.append(entry.name)
            continue  # never copy private material
        if _is_public(entry):
            target = dst / entry.name
            if entry.is_dir():
                shutil.copytree(entry, target)
            else:
                shutil.copy2(entry, target)
            exposed.append(entry.name)

    if not exposed:
        return EvaluationSetup(
            workspace=dst,
            status=EVAL_BENCHMARK_INVALID,
            detail="benchmark exposes no public statement/",
        )
    return EvaluationSetup(
        workspace=dst,
        status=EVAL_OK,
        exposed=exposed,
        hidden=hidden,
    )


def snapshot_benchmark(benchmark_dir: str | Path) -> str | None:
    """Digest of the public + private benchmark tree, for change detection.

    Returns None when the directory is missing/unreadable. The digest is
    compared before/after an evaluation to detect ``version_changed``.
    """
    src = Path(benchmark_dir).resolve()
    if not src.is_dir():
        return None
    try:
        return _tree_digest(src)
    except OSError:
        return None


def evaluation_is_valid(before: str | None, after: str | None) -> bool:
    """Whether a benchmark stayed unchanged across an evaluation."""
    return before is not None and before == after


__all__ = [
    "EVAL_BENCHMARK_INVALID",
    "EVAL_CONTAMINATED",
    "EVAL_NOT_FOUND",
    "EVAL_OK",
    "EVAL_VERSION_CHANGED",
    "EvaluationSetup",
    "evaluation_is_valid",
    "prepare_evaluation_workspace",
    "snapshot_benchmark",
]
