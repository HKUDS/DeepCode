"""Shadow git snapshots for undo / checkpoint / diff (P2).

Gives a coding agent per-step checkpoints, diffs, and undo WITHOUT touching
the user's own git history or index. The trick (from opencode): keep a
separate git directory and point it at the real working tree —

    git --git-dir=<shadow> --work-tree=<workspace> ...

so ``add``/``commit``/``checkout`` operate on the workspace files while all
metadata lands in the shadow dir. The workspace's ``.git`` is never created
or modified.

Mechanism only, pure subprocess git. Degrades gracefully: if git is missing
the snapshotter reports unavailable and callers skip snapshotting.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Identity for shadow commits (never uses the user's git identity/config).
_GIT_IDENTITY = (
    "-c",
    "user.name=DeepCode",
    "-c",
    "user.email=snapshots@deepcode.local",
    "-c",
    "commit.gpgsign=false",
)


def _default_shadow_dir(workspace: str) -> Path:
    digest = hashlib.sha1(os.path.abspath(workspace).encode()).hexdigest()[:12]
    base = Path(
        os.environ.get(
            "DEEPCODE_SNAPSHOT_DIR",
            str(Path.home() / ".local" / "share" / "deepcode" / "snapshots"),
        )
    )
    return base / digest


@dataclass(frozen=True)
class SnapshotInfo:
    id: str  # short commit hash
    label: str


class Snapshotter:
    """Manage checkpoints of a workspace in a shadow git dir."""

    def __init__(self, workspace: str, shadow_dir: str | None = None):
        self._workspace = os.path.abspath(workspace)
        self._shadow = (
            Path(shadow_dir) if shadow_dir else _default_shadow_dir(self._workspace)
        )
        self._initialized = False

    # -- availability ------------------------------------------------------

    @staticmethod
    def git_available() -> bool:
        try:
            subprocess.run(
                ["git", "--version"], capture_output=True, timeout=5, check=True
            )
            return True
        except (OSError, subprocess.SubprocessError):
            return False

    # -- internal git plumbing --------------------------------------------

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                "git",
                "--git-dir",
                str(self._shadow),
                "--work-tree",
                self._workspace,
                *args,
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=check,
        )

    def _ensure_init(self) -> None:
        if self._initialized:
            return
        self._shadow.parent.mkdir(parents=True, exist_ok=True)
        if not (self._shadow / "HEAD").exists():
            subprocess.run(
                ["git", "init", "--bare", str(self._shadow)],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
        self._initialized = True

    # -- public API --------------------------------------------------------

    def snapshot(self, label: str = "snapshot") -> SnapshotInfo:
        """Checkpoint the current workspace; return the snapshot handle."""
        self._ensure_init()
        self._git("add", "-A")
        # --allow-empty so a no-op checkpoint still advances history.
        self._git(
            *_GIT_IDENTITY,
            "commit",
            "--allow-empty",
            "-m",
            label,
        )
        rev = self._git("rev-parse", "--short", "HEAD").stdout.strip()
        return SnapshotInfo(id=rev, label=label)

    def diff_since(self, snapshot_id: str) -> str:
        """Unified diff of the workspace vs a snapshot (staged + unstaged)."""
        self._ensure_init()
        self._git("add", "-A")
        proc = self._git("diff", "--cached", snapshot_id, check=False)
        return proc.stdout

    def restore(self, snapshot_id: str) -> None:
        """Revert tracked files in the workspace to a snapshot's state."""
        self._ensure_init()
        self._git("checkout", snapshot_id, "--", ".")

    def list(self) -> list[SnapshotInfo]:
        """Most-recent-first list of snapshots."""
        self._ensure_init()
        proc = self._git("log", "--pretty=format:%h\t%s", check=False)
        out: list[SnapshotInfo] = []
        for line in proc.stdout.splitlines():
            if "\t" in line:
                rev, label = line.split("\t", 1)
                out.append(SnapshotInfo(id=rev, label=label))
        return out
