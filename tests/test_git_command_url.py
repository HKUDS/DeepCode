"""Regression cover for GitHub URL normalization in ``tools/git_command.py``.

``GitHubURLExtractor`` used ``str.rstrip(".git")`` to drop the ``.git``
suffix. ``rstrip`` takes a *set of characters*, not a suffix, so any
repository name ending in ``g``, ``i``, ``t`` or ``.`` was truncated —
``facebook/react`` became ``facebook/reac`` and ``deep-learning`` became
``deep-learnin`` — and the clone then targeted a non-existent (or wrong)
repository. These tests pin the suffix-only behaviour.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.git_command import GitHubURLExtractor


def test_extract_preserves_names_ending_in_strip_chars():
    urls = GitHubURLExtractor.extract_github_urls(
        "Clone https://github.com/facebook/react please"
    )
    assert urls == ["https://github.com/facebook/react"]


def test_extract_strips_only_real_git_suffix():
    urls = GitHubURLExtractor.extract_github_urls(
        "Download https://github.com/HKUDS/DeepCode.git"
    )
    assert urls == ["https://github.com/HKUDS/DeepCode"]


def test_infer_repo_name_keeps_full_name():
    assert (
        GitHubURLExtractor.infer_repo_name("https://github.com/foo/deep-learning")
        == "deep-learning"
    )
    assert GitHubURLExtractor.infer_repo_name("https://github.com/foo/bar.git") == "bar"


class _NoGit:
    """Records the argv ``git`` would have run, without running it."""

    def __init__(self):
        self.argv: list[str] | None = None

    async def __call__(self, *argv, **_kwargs):
        self.argv = list(argv)

        class _Done:
            returncode = 0

            async def communicate(self):
                return b"", b""

        return _Done()


@pytest.fixture
def no_git(monkeypatch):
    import tools.git_command as git_command

    async def installed():
        return True

    recorder = _NoGit()
    monkeypatch.setattr(git_command, "check_git_installed", installed)
    monkeypatch.setattr(git_command.asyncio, "create_subprocess_exec", recorder)
    return recorder


@pytest.mark.asyncio
async def test_git_clone_refuses_an_option_for_a_url(no_git, tmp_path):
    """``--upload-pack=<cmd>`` as the URL would run <cmd> on this machine."""

    from tools.git_command import git_clone

    result = await git_clone(
        "--upload-pack=touch pwned", target_path=str(tmp_path / "repo")
    )
    assert "Error" in result
    assert no_git.argv is None


@pytest.mark.asyncio
async def test_git_clone_ends_options_before_the_url(no_git, tmp_path):
    from tools.git_command import git_clone

    target = str(tmp_path / "repo")
    await git_clone("https://github.com/foo/bar", target_path=target, branch="main")
    assert no_git.argv == [
        "git",
        "clone",
        "-b",
        "main",
        "--",
        "https://github.com/foo/bar",
        target,
    ]


@pytest.mark.asyncio
async def test_download_target_cannot_become_an_option(no_git):
    """The target directory comes from free text ("clone x to <path>")."""

    from tools.git_command import clone_repository

    await clone_repository("https://github.com/foo/bar", "--upload-pack=touch pwned")
    assert no_git.argv[:3] == ["git", "clone", "--"]
