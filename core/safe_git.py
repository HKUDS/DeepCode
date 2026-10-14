"""Options for DeepCode's own git calls on a repository the agent writes to.

DeepCode runs git outside the sandbox: status, diff and discard for the
Changes view, Session worktrees, and the commits and merges of isolated
sub-agents. The repository may carry a config the agent wrote, one it
started with ``git init`` inside the sandbox (which only protects the
config of a repository that already existed), or one nested in the
workspace. Nothing that config names may run here: hooks, an fsmonitor,
filter or merge drivers, or a signing program. (Diff programs are kept off
by the one call that would run them: ``diff --no-ext-diff --no-textconv``.)
Nor may that config move the work tree git writes to (``core.worktree``)
away from the repository, out of the workspace.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

_ALWAYS = (
    *("-c", "core.hooksPath=/dev/null"),
    *("-c", "core.fsmonitor=false"),
    # git looks into a submodule's (or nested repository's) work tree by
    # running git there, under that repository's own config.
    *("-c", "diff.ignoreSubmodules=dirty"),
    *("-c", "submodule.recurse=false"),
)
_DRIVER = re.compile(r"^(filter\..+\.(clean|smudge|process)|merge\..+\.driver)$")
_SIGNING = re.compile(r"^gpg\.(.+\.)?program$|^gpg\.ssh\.defaultkeycommand$")


class MovedWorkTreeError(OSError):
    """The repository's own config puts its work tree somewhere else."""


# What `git lfs install --local` writes: kept, so LFS files stay pointers.
_GIT_LFS = {
    "filter.lfs.clean": "git-lfs clean -- %f",
    "filter.lfs.smudge": "git-lfs smudge -- %f",
    "filter.lfs.process": "git-lfs filter-process",
}


def safe_git_options(cwd: str | os.PathLike[str]) -> list[str]:
    """``-c`` options that keep git in ``cwd`` from running what its repository
    names. Programs from the user's own (global and system) config still run.

    Raises :class:`MovedWorkTreeError` for a repository whose own config puts
    its work tree elsewhere.
    """

    options = list(_ALWAYS)
    try:
        listed = subprocess.run(
            [
                *("git", "config", "--show-scope", "--get-regexp"),
                r"^(filter|merge|gpg)\.|^core\.worktree$",
            ],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return options
    signing = False
    for line in listed.stdout.splitlines():
        scope, _, entry = line.partition("\t")
        key, _, value = entry.partition(" ")
        if scope not in {"local", "worktree"}:
            continue
        if _DRIVER.match(key) and _GIT_LFS.get(key) != value:
            # An empty program is no program: git skips the filter, and a
            # merge falls back to reporting a conflict.
            options += ["-c", f"{key}="]
        elif _SIGNING.match(key):
            signing = True
        elif key == "core.worktree":
            _require_own_work_tree(cwd)
    if signing:
        options += ["-c", "commit.gpgSign=false", "-c", "merge.verifySignatures=false"]
    return options


def _require_own_work_tree(cwd: str | os.PathLike[str]) -> None:
    """Refuse a repository whose work tree is not where its ``.git`` is.

    git writes checkouts, restores and merges into ``core.worktree``, and
    ignores ``-c`` for it. An agent that ran ``git init`` in the workspace
    could point it at any directory, out of the workspace. A repository whose
    git dir lives apart (a submodule's, ``--separate-git-dir``) still names
    its own work tree, which holds a ``.git`` file pointing back at it.
    """
    try:
        found = subprocess.run(
            ["git", "rev-parse", "--absolute-git-dir", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise MovedWorkTreeError(
            "cannot check where the repository's work tree is (core.worktree)"
        ) from exc
    lines = found.stdout.splitlines()
    if found.returncode != 0 or len(lines) != 2:
        return  # no work tree git could use: every command needing one fails
    git_dir, top = (Path(line).resolve() for line in lines)
    dot_git = top / ".git"
    if dot_git.is_dir():
        points_back = dot_git.resolve() == git_dir
    elif dot_git.is_file():
        named = dot_git.read_text(encoding="utf-8", errors="replace").strip()
        points_back = (
            named.startswith("gitdir:")
            and (top / named[len("gitdir:") :].strip()).resolve() == git_dir
        )
    else:
        points_back = False
    if not points_back:
        raise MovedWorkTreeError(
            f"the repository at {git_dir} sets its work tree to {top} "
            "(core.worktree); DeepCode runs no git command there"
        )
