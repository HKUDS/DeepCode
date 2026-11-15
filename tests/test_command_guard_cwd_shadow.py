"""Regression tests for the working-directory module-shadow screen.

A Python interpreter puts its cwd on ``sys.path``, so a same-named file there
shadows the standard library. A downloaded archive shipping ``struct.py``
executes code as soon as the caller types ``import base64`` — base64 imports
struct transitively. Every step of that chain (download, unpack, run) stays
inside the workspace, so the sandbox write fence cannot see it: the fence
governs where bytes may land, not whether what just landed may run.
"""

from __future__ import annotations

import pytest

from core.harness.command_guard import (
    PYTHON_SHADOW_WAIVER_ENV_VAR,
    _is_python_program,
    screen_all,
    screen_cwd_shadow,
    shadow_names_in,
)


# --------------------------------------------------------------------------- detection


def test_shadow_names_detects_the_published_payload(tmp_path):
    (tmp_path / "struct.py").write_text("x = 1\n", encoding="utf-8")
    assert shadow_names_in(str(tmp_path)) == ("struct.py",)


def test_shadow_names_is_case_insensitive(tmp_path):
    (tmp_path / "STRUCT.PY").write_text("x = 1\n", encoding="utf-8")
    assert shadow_names_in(str(tmp_path)) == ("struct.py",)


def test_shadow_names_ignores_unrelated_files(tmp_path):
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("", encoding="utf-8")
    assert shadow_names_in(str(tmp_path)) == ()


def test_shadow_names_ignores_a_directory_with_the_same_name(tmp_path):
    (tmp_path / "struct.py").mkdir()
    assert shadow_names_in(str(tmp_path)) == ()


def test_shadow_names_degrades_on_missing_directory(tmp_path):
    # Must never raise: a screening layer cannot block on a filesystem quirk.
    assert shadow_names_in(str(tmp_path / "does-not-exist")) == ()


# --------------------------------------------------------------------------- screening


def test_python_call_is_refused_when_cwd_shadows(tmp_path):
    (tmp_path / "struct.py").write_text("x = 1\n", encoding="utf-8")
    reason = screen_cwd_shadow("python3 -c 'import base64'", str(tmp_path))
    assert reason is not None
    assert "shadow" in reason


def test_python_call_allowed_without_shadow_file(tmp_path):
    assert screen_cwd_shadow("python3 -c 'import base64'", str(tmp_path)) is None


def test_non_python_command_is_not_screened(tmp_path):
    (tmp_path / "struct.py").write_text("x = 1\n", encoding="utf-8")
    assert screen_cwd_shadow("ls -la", str(tmp_path)) is None


def test_no_workspace_means_inert(tmp_path):
    (tmp_path / "struct.py").write_text("x = 1\n", encoding="utf-8")
    assert screen_cwd_shadow("python3 -c 'import base64'", None) is None


def test_cd_target_is_checked(tmp_path):
    """An unpack-and-run chain is ``cd <archive-dir> && python3 ...``.

    Checking only the workspace root would miss it, so the cd target is
    inspected too.
    """
    unpacked = tmp_path / "catalogue"
    unpacked.mkdir()
    (unpacked / "struct.py").write_text("x = 1\n", encoding="utf-8")

    reason = screen_cwd_shadow(
        "cd catalogue && python3 -c 'import base64'", str(tmp_path)
    )
    assert reason is not None
    assert "catalogue" in reason


def test_cd_into_clean_nested_dir_is_allowed(tmp_path):
    (tmp_path / "clean").mkdir()
    assert screen_cwd_shadow("cd clean && python3 -c 'print(1)'", str(tmp_path)) is None


def test_cd_into_missing_directory_does_not_raise(tmp_path):
    reason = screen_cwd_shadow("cd nope && python3 -c 'print(1)'", str(tmp_path))
    assert reason is None


def test_waiver_env_allows_deliberate_vendoring(tmp_path, monkeypatch):
    (tmp_path / "struct.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setenv(PYTHON_SHADOW_WAIVER_ENV_VAR, "1")
    assert screen_cwd_shadow("python3 -c 'import base64'", str(tmp_path)) is None


def test_waiver_ignores_falsey_spellings(tmp_path, monkeypatch):
    (tmp_path / "struct.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setenv(PYTHON_SHADOW_WAIVER_ENV_VAR, "off")
    assert screen_cwd_shadow("python3 -c 'import base64'", str(tmp_path)) is not None


@pytest.mark.parametrize("program", ["python", "python3", "python3.14", "py", "py3"])
def test_python_program_variants(program):
    assert _is_python_program(program) is True


@pytest.mark.parametrize("program", ["pip", "python-config", "node", "bash"])
def test_non_python_programs_are_rejected(program):
    assert _is_python_program(program) is False


# --------------------------------------------------------------------------- wiring


def test_screen_all_runs_shadow_before_strict_egress(tmp_path):
    """The shadow screen must not wait for DEEPCODE_COMMAND_SCREEN=strict."""
    (tmp_path / "sitecustomize.py").write_text("x = 1\n", encoding="utf-8")
    reason = screen_all("python -c 'print(1)'", workspace=str(tmp_path))
    assert reason is not None
    assert "shadow" in reason


def test_screen_all_keeps_destructive_screen_first(tmp_path):
    (tmp_path / "struct.py").write_text("x = 1\n", encoding="utf-8")
    reason = screen_all("rm -rf /", workspace=str(tmp_path))
    assert reason is not None
    assert "recursive force remove" in reason


def test_screen_all_without_workspace_is_unchanged(tmp_path):
    """Existing callers that pass no workspace keep the old behaviour."""
    (tmp_path / "struct.py").write_text("x = 1\n", encoding="utf-8")
    assert screen_all("python3 -c 'import base64'") is None
