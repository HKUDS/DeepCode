"""Worktree isolation + merge with conflict detection (P4).

Each worker builds in its own ``git worktree`` off the base — real parallel
isolation, so two workers editing different files never see each other's
half-done state. When a worker finishes, its branch is merged back into the
base; git's own 3-way merge *is* the diff-hunk conflict detector (§4.5): if
two workers changed overlapping lines, the merge reports a conflict and we
surface it instead of silently clobbering one worker's work.

Complements the P2.d shadow snapshot (per-worker, in-round undo) — this is
between-worker isolation. Pure git plumbing; no agent, no LLM.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_GIT_IDENTITY = (
    "-c",
    "user.name=DeepCode Team",
    "-c",
    "user.email=team@deepcode.local",
)
_BRANCH_PREFIX = "team/"

# Paths a worker must never commit: the team's own scratch state (.deepcode/ —
# task board + per-worker loop state) and build-artifact noise a worker's test
# run leaves in its worktree. Committing either pollutes the user's history AND
# breaks merges — git aborts a merge whose incoming (committed) artifact would
# overwrite an untracked artifact of the same path in the base, a failure with
# no content conflict at all. We install these into the base repo's
# `.git/info/exclude`, which is:
#   • local (never committed or pushed),
#   • additive — it supplements, never overrides, the user's own .gitignore,
#   • effective only on UNTRACKED files, so it can never hide anything the
#     user's repo already tracks,
#   • shared by every linked worktree (it lives in the common git dir).
# The list is the footprint the team's own runs create plus universal noise —
# not project source, so nothing a worker legitimately authors is caught.
_TEAM_EXCLUDE = (
    ".deepcode/",
    "__pycache__/",
    "*.py[cod]",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".coverage",
    "node_modules/",
    ".DS_Store",
)
_EXCLUDE_BEGIN = "# >>> deepcode team (auto, local-only) >>>"
_EXCLUDE_END = "# <<< deepcode team (auto, local-only) <<<"


@dataclass
class MergeResult:
    worker_id: str
    clean: bool
    conflicts: list[str] = field(default_factory=list)
    detail: str = ""


class WorktreeError(RuntimeError):
    pass


class WorktreeManager:
    """Own the base repo + per-worker worktrees for one team run."""

    def __init__(self, base_workspace: str, worktrees_root: str | None = None):
        self.base = Path(base_workspace).resolve()
        # Worktrees live OUTSIDE the base tree (git forbids nesting a worktree
        # inside its main tree's tracked area); default to a sibling dir.
        self.worktrees_root = (
            Path(worktrees_root).resolve()
            if worktrees_root
            else self.base.parent / (self.base.name + ".worktrees")
        )
        self._branches: dict[str, str] = {}

    # -- git helpers -----------------------------------------------------------

    def _git(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd or self.base),
            capture_output=True,
            text=True,
            timeout=120,
        )

    def _git_ok(
        self, *args: str, cwd: Path | None = None
    ) -> subprocess.CompletedProcess:
        proc = self._git(*args, cwd=cwd)
        if proc.returncode != 0:
            raise WorktreeError(
                f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        return proc

    # -- lifecycle -------------------------------------------------------------

    def ensure_base(self) -> None:
        """Make the base a git repo with at least one commit (worktrees need a
        HEAD to branch from). Idempotent."""
        self.base.mkdir(parents=True, exist_ok=True)
        if not (self.base / ".git").exists():
            self._git_ok("init", "-q")
        self._install_team_exclude()
        # A worktree needs a commit to branch from; create one if HEAD is unborn.
        if self._git("rev-parse", "--verify", "HEAD").returncode != 0:
            self._git_ok(*_GIT_IDENTITY, "add", "-A")  # honors info/exclude
            self._git_ok(
                *_GIT_IDENTITY, "commit", "--allow-empty", "-q", "-m", "team base"
            )

    def _install_team_exclude(self) -> None:
        """Add the team's build-artifact ignore block to the repo's local
        ``.git/info/exclude`` (idempotent). See :data:`_TEAM_EXCLUDE`."""
        # Resolve the common git dir (works for both a normal repo and one we
        # just init'd); `.git` is a dir here since worktrees don't exist yet.
        proc = self._git("rev-parse", "--git-common-dir")
        if proc.returncode != 0:
            return
        common = Path(proc.stdout.strip())
        if not common.is_absolute():
            common = self.base / common
        info = common / "info"
        info.mkdir(parents=True, exist_ok=True)
        exclude = info / "exclude"
        existing = exclude.read_text() if exclude.exists() else ""
        if _EXCLUDE_BEGIN in existing:
            return  # already installed
        block = "\n".join((_EXCLUDE_BEGIN, *_TEAM_EXCLUDE, _EXCLUDE_END))
        sep = "" if not existing or existing.endswith("\n") else "\n"
        exclude.write_text(f"{existing}{sep}{block}\n")

    def create(self, worker_id: str) -> str:
        """Create an isolated worktree on a fresh branch for ``worker_id``."""
        self.ensure_base()
        branch = _BRANCH_PREFIX + worker_id
        path = self.worktrees_root / worker_id
        if path.exists():  # a re-run — drop the old one first
            self.cleanup(worker_id)
        self.worktrees_root.mkdir(parents=True, exist_ok=True)
        # -B resets the branch if it lingered from a crashed run.
        self._git_ok("worktree", "add", "-q", "-B", branch, str(path), "HEAD")
        self._branches[worker_id] = branch
        return str(path)

    def commit_worker(self, worker_id: str, message: str = "") -> bool:
        """Commit the worker's changes in its worktree. Returns True if there
        was anything to commit."""
        path = self.worktrees_root / worker_id
        self._git_ok(*_GIT_IDENTITY, "add", "-A", cwd=path)  # honors info/exclude
        # Look at what is actually STAGED (post-exclusion), not the raw working
        # tree — a worker that only wrote ignored state has nothing to merge.
        staged = self._git("diff", "--cached", "--name-only", cwd=path)
        if not staged.stdout.strip():
            return False  # worker produced no committable change
        self._git_ok(
            *_GIT_IDENTITY,
            "commit",
            "-q",
            "-m",
            message or f"worker {worker_id}",
            cwd=path,
        )
        return True

    def merge(self, worker_id: str) -> MergeResult:
        """Merge a worker's branch into the base; detect conflicts.

        Commits the worker's working changes first, then does a real 3-way
        merge into the base. On conflict the merge is aborted and the
        conflicting files are reported — nothing is clobbered.
        """
        branch = self._branches.get(worker_id, _BRANCH_PREFIX + worker_id)
        changed = self.commit_worker(worker_id)
        if not changed:
            return MergeResult(worker_id, clean=True, detail="no changes to merge")

        merge = self._git(
            *_GIT_IDENTITY,
            "merge",
            "--no-ff",
            "-m",
            f"merge {worker_id}",
            branch,
        )
        if merge.returncode == 0:
            return MergeResult(worker_id, clean=True, detail="merged")

        # The merge failed. Distinguish the two very different causes:
        #   • unmerged files present → a real 3-way content conflict;
        #   • none present → the working tree blocked the merge before it began
        #     (e.g. an untracked file in the base the branch also adds). Report
        #     that honestly rather than as a phantom "0-file conflict".
        # Either way abort so the base stays consistent (nothing half-merged).
        conflicts = self._git("diff", "--name-only", "--diff-filter=U").stdout.split()
        self._git("merge", "--abort")
        if conflicts:
            return MergeResult(
                worker_id,
                clean=False,
                conflicts=conflicts,
                detail=f"merge conflict in {len(conflicts)} file(s)",
            )
        reason = next(
            (ln.strip() for ln in merge.stderr.splitlines() if ln.strip()),
            "merge failed",
        )
        return MergeResult(worker_id, clean=False, detail=f"merge blocked: {reason}")

    def cleanup(self, worker_id: str) -> None:
        """Remove a worker's worktree (best-effort)."""
        path = self.worktrees_root / worker_id
        self._git("worktree", "remove", "--force", str(path))
        branch = self._branches.get(worker_id, _BRANCH_PREFIX + worker_id)
        self._git("branch", "-D", branch)

    def cleanup_all(self) -> None:
        for worker_id in list(self._branches):
            self.cleanup(worker_id)
        self._git("worktree", "prune")
        self._remove_team_exclude()  # leave the repo's config as we found it

    def _remove_team_exclude(self) -> None:
        """Remove the ignore block installed by :meth:`_install_team_exclude`,
        so the team leaves the user's repo exactly as it found it (only the
        intended merge commits remain). Idempotent / best-effort."""
        proc = self._git("rev-parse", "--git-common-dir")
        if proc.returncode != 0:
            return
        common = Path(proc.stdout.strip())
        if not common.is_absolute():
            common = self.base / common
        exclude = common / "info" / "exclude"
        if not exclude.exists():
            return
        text = exclude.read_text()
        if _EXCLUDE_BEGIN not in text:
            return
        before, _, rest = text.partition(_EXCLUDE_BEGIN)
        _, _, after = rest.partition(_EXCLUDE_END)
        cleaned = (before.rstrip("\n") + "\n" + after.lstrip("\n")).strip("\n")
        exclude.write_text(cleaned + "\n" if cleaned else "")
