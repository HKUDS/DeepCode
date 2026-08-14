"""Tests for the SWE-bench harness (offline: real git + pytest, no model).

The pure parts (subset selection, record mapping) are checked directly. The
end-to-end loop (prepare → agent → apply → test → score) is exercised with an
injected fake "agent" that edits files deterministically, so the harness's
own logic is verified without a live model.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.swebench import dataset  # noqa: E402
from eval.swebench.evaluate import evaluate_local  # noqa: E402
from eval.swebench.instance import load_local_instances  # noqa: E402
from eval.swebench.predict import (  # noqa: E402
    capture_patch,
    generate_prediction,
    prepare_workspace,
)

_HAVE_GIT = shutil.which("git") is not None

import pytest  # noqa: E402

pytestmark = pytest.mark.skipif(not _HAVE_GIT, reason="git required")


# --- pure dataset logic ----------------------------------------------------


def test_select_subset_is_deterministic_and_sized():
    records = [{"instance_id": f"r{i:03d}"} for i in range(200)]
    a = dataset.select_subset(records, 50)
    b = dataset.select_subset(list(reversed(records)), 50)
    assert len(a) == 50
    assert [r["instance_id"] for r in a] == [r["instance_id"] for r in b]  # stable
    assert a[0]["instance_id"] == "r000"  # lexicographic first


def test_parse_test_list_handles_json_and_list():
    assert dataset._parse_test_list('["a::b", "c::d"]') == ["a::b", "c::d"]
    assert dataset._parse_test_list(["x"]) == ["x"]
    assert dataset._parse_test_list("") == []


def test_to_instance_maps_fields():
    rec = {
        "instance_id": "django__django-1",
        "repo": "django/django",
        "base_commit": "abc123",
        "problem_statement": "bug",
        "FAIL_TO_PASS": '["t::a"]',
        "PASS_TO_PASS": '["t::b"]',
    }
    inst = dataset.to_instance(rec)
    assert inst.repo == "django/django"
    assert inst.base_commit == "abc123"
    assert inst.fail_to_pass == ["t::a"] and inst.pass_to_pass == ["t::b"]


# --- local benchmark materialisation ---------------------------------------


def test_local_instances_are_git_repos_at_base(tmp_path):
    insts = load_local_instances(tmp_path)
    assert len(insts) == 3
    for inst in insts:
        assert inst.repo_path and (Path(inst.repo_path) / ".git").exists()
        assert len(inst.base_commit) >= 7
        assert inst.fail_to_pass and inst.pass_to_pass


# --- fake agents for the end-to-end loop -----------------------------------

_FIXES = {
    "local__mathlib-inclusive-sum": (
        "mathlib.py",
        "for i in range(a, b):  # bug: excludes b",
        "for i in range(a, b + 1):",
    ),
    "local__listutil-empty-guard": (
        "listutil.py",
        "return xs[0]  # bug: IndexError on []",
        "return xs[0] if xs else None",
    ),
}


def _fixing_runner(workspace: str, prompt: str, model: str) -> bool:
    # Identify the instance from a marker file present in the workspace.
    ws = Path(workspace)
    for inst_id, (fname, old, new) in _FIXES.items():
        target = ws / fname
        if target.exists():
            text = target.read_text()
            if old in text:
                target.write_text(text.replace(old, new))
                return True
    return False


def _noop_runner(workspace: str, prompt: str, model: str) -> bool:
    return True  # completes but changes nothing → empty patch


def test_capture_patch_reflects_edits(tmp_path):
    inst = next(
        i
        for i in load_local_instances(tmp_path / "repos")
        if i.instance_id == "local__mathlib-inclusive-sum"
    )
    ws = tmp_path / "ws"
    prepare_workspace(inst, ws)
    _fixing_runner(str(ws), "", "")
    patch = capture_patch(ws)
    assert "range(a, b + 1)" in patch
    assert patch.startswith("diff --git")


def test_correct_fix_resolves(tmp_path):
    inst = next(
        i
        for i in load_local_instances(tmp_path / "repos")
        if i.instance_id == "local__mathlib-inclusive-sum"
    )
    ws = tmp_path / "ws"
    prepare_workspace(inst, ws)
    pred = generate_prediction(inst, ws, model="fake", runner=_fixing_runner)
    row = evaluate_local(inst, pred, scratch_root=tmp_path / "score")
    assert row.resolved is True
    assert row.fail_to_pass_ok and row.pass_to_pass_ok
    assert row.patch_generated


def _fix_and_run_tests_runner(workspace: str, prompt: str, model: str) -> bool:
    """Fix the bug AND run pytest — reproducing the __pycache__ pollution that
    once made captured patches fail to apply (regression guard)."""
    import subprocess

    _fixing_runner(workspace, prompt, model)
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=workspace,
        capture_output=True,
    )
    return True


def test_patch_excludes_pytest_artifacts_and_resolves(tmp_path):
    # An agent that runs the tests leaves __pycache__/*.pyc behind; those must
    # not enter the model_patch, or `git apply` rejects the whole thing.
    inst = next(
        i
        for i in load_local_instances(tmp_path / "repos")
        if i.instance_id == "local__mathlib-inclusive-sum"
    )
    ws = tmp_path / "ws"
    prepare_workspace(inst, ws)
    pred = generate_prediction(inst, ws, model="fake", runner=_fix_and_run_tests_runner)
    assert "__pycache__" not in pred["model_patch"]
    assert ".pyc" not in pred["model_patch"]
    row = evaluate_local(inst, pred, scratch_root=tmp_path / "score")
    assert row.resolved is True  # patch applied cleanly despite the junk


def test_noop_does_not_resolve(tmp_path):
    inst = next(
        i
        for i in load_local_instances(tmp_path / "repos")
        if i.instance_id == "local__listutil-empty-guard"
    )
    ws = tmp_path / "ws"
    prepare_workspace(inst, ws)
    pred = generate_prediction(inst, ws, model="fake", runner=_noop_runner)
    row = evaluate_local(inst, pred, scratch_root=tmp_path / "score")
    assert row.resolved is False
    assert row.patch_generated is False  # empty diff
