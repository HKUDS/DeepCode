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
