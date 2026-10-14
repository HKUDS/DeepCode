"""DeepCode's own git calls ignore the programs a repository names for itself."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.safe_git import safe_git_options  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git required")


def _repository(path: Path, **config: str) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    for key, value in config.items():
        subprocess.run(["git", "config", key, value], cwd=path, check=True)
    return path


def test_programs_from_an_included_file_are_overridden_too(tmp_path):
    repo = _repository(tmp_path / "repo")
    (repo / ".git" / "extra.cfg").write_text('[filter "x"]\n\tclean = evil\n')
    subprocess.run(["git", "config", "include.path", "extra.cfg"], cwd=repo, check=True)

    assert "filter.x.clean=" in safe_git_options(repo)


def test_git_lfs_installed_for_the_repository_keeps_its_filter(tmp_path):
    repo = _repository(
        tmp_path / "repo",
        **{
            "filter.lfs.clean": "git-lfs clean -- %f",
            "filter.lfs.process": "git-lfs filter-process; touch /tmp/x",
        },
    )

    options = safe_git_options(repo)

    assert "filter.lfs.clean=" not in options
    assert "filter.lfs.process=" in options


def test_a_repository_signing_program_turns_signing_off(tmp_path):
    repo = _repository(tmp_path / "repo", **{"gpg.ssh.program": "evil"})

    options = safe_git_options(repo)

    assert "commit.gpgSign=false" in options
    assert "merge.verifySignatures=false" in options


def test_outside_a_repository_only_the_fixed_options_apply(tmp_path):
    options = safe_git_options(tmp_path)

    assert options[options.index("core.hooksPath=/dev/null") - 1] == "-c"
    assert "core.fsmonitor=false" in options
