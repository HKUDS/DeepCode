"""Regression cover for the workspace containment check in
``tools/code_implementation_server.py``.

``validate_path`` guarded file operations with
``str(full_path).startswith(str(WORKSPACE_DIR))``. String-prefix matching
ignores path boundaries, so a sibling directory whose name starts with the
workspace name passed the check: with workspace ``.../generate_code`` the
path ``../generate_code_evil/x.py`` resolved *outside* the workspace yet
was accepted, letting the read/write tools escape the sandbox.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.code_implementation_server as cis


@pytest.fixture()
def workspace(tmp_path):
    workspace_dir = tmp_path / "generate_code"
    cis.initialize_workspace(str(workspace_dir))
    return workspace_dir


def test_validate_path_rejects_sibling_prefix_escape(workspace):
    (workspace.parent / "generate_code_evil").mkdir()
    with pytest.raises(ValueError, match="outside workspace"):
        cis.validate_path("../generate_code_evil/x.py")


def test_validate_path_rejects_plain_escape(workspace):
    with pytest.raises(ValueError, match="outside workspace"):
        cis.validate_path("../elsewhere/x.py")


def test_validate_path_accepts_paths_inside_workspace(workspace):
    assert cis.validate_path("src/main.py") == (workspace / "src/main.py").resolve()
    assert cis.validate_path("./a/../b.py") == (workspace / "b.py").resolve()
