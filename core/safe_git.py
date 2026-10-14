"""Options for DeepCode's own git calls on a repository the agent writes to.

DeepCode runs git outside the sandbox: status, diff and discard for the
Changes view, Session worktrees, and the commits and merges of isolated
sub-agents. The repository may carry a config the agent wrote, one it
started with ``git init`` inside the sandbox (which only protects the
config of a repository that already existed), or one nested in the
workspace. Nothing that config names may run here: hooks, an fsmonitor,
filter or merge drivers, or a signing program. (Diff programs are kept off
by the one call that would run them: ``diff --no-ext-diff --no-textconv``.)
"""

from __future__ import annotations

import os
import re
import subprocess

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
# What `git lfs install --local` writes: kept, so LFS files stay pointers.
_GIT_LFS = {
    "filter.lfs.clean": "git-lfs clean -- %f",
    "filter.lfs.smudge": "git-lfs smudge -- %f",
    "filter.lfs.process": "git-lfs filter-process",
}


def safe_git_options(cwd: str | os.PathLike[str]) -> list[str]:
    """``-c`` options that keep git in ``cwd`` from running what its repository
    names. Programs from the user's own (global and system) config still run."""

    options = list(_ALWAYS)
    try:
        listed = subprocess.run(
            ["git", "config", "--show-scope", "--get-regexp", r"^(filter|merge|gpg)\."],
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
    if signing:
        options += ["-c", "commit.gpgSign=false", "-c", "merge.verifySignatures=false"]
    return options
