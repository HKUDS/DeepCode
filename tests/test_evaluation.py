"""Tests for P0-4 evaluation isolation protocol (PenguinHarness)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.loop.evaluation import (
    EVAL_BENCHMARK_INVALID,
    EVAL_NOT_FOUND,
    EVAL_OK,
    evaluation_is_valid,
    prepare_evaluation_workspace,
    snapshot_benchmark,
)


def _make_benchmark(root: Path) -> Path:
    bench = root / "bench"
    (bench / "statement").mkdir(parents=True)
    (bench / "statement" / "README.md").write_text("public task", encoding="utf-8")
    (bench / "rubric").mkdir(parents=True)
    (bench / "rubric" / "README.md").write_text("PRIVATE scoring", encoding="utf-8")
    (bench / "gold").mkdir(parents=True)
    (bench / "gold" / "answer.txt").write_text("PRIVATE gold", encoding="utf-8")
    (bench / "benchmark_config.toml").write_text("runs = 1", encoding="utf-8")
    return bench


def test_public_only_copied(tmp_path):
    bench = _make_benchmark(tmp_path)
    out = prepare_evaluation_workspace(bench, tmp_path / "eval")
    assert out.status == EVAL_OK
    # Public statement is exposed.
    assert (out.workspace / "statement" / "README.md").read_text() == "public task"
    # Private rubric / gold never copied.
    assert not (out.workspace / "rubric").exists()
    assert not (out.workspace / "gold").exists()
    assert "rubric" in out.hidden and "gold" in out.hidden


def test_missing_benchmark(tmp_path):
    out = prepare_evaluation_workspace(tmp_path / "nope", tmp_path / "eval")
    assert out.status == EVAL_NOT_FOUND


def test_no_public_content_invalid(tmp_path):
    bench = tmp_path / "bench"
    (bench / "rubric").mkdir(parents=True)
    (bench / "rubric" / "x.txt").write_text("secret", encoding="utf-8")
    out = prepare_evaluation_workspace(bench, tmp_path / "eval")
    assert out.status == EVAL_BENCHMARK_INVALID


def test_target_exists_requires_force(tmp_path):
    bench = _make_benchmark(tmp_path)
    (tmp_path / "eval").mkdir()
    out = prepare_evaluation_workspace(bench, tmp_path / "eval")
    assert out.status == EVAL_BENCHMARK_INVALID
    out2 = prepare_evaluation_workspace(bench, tmp_path / "eval", force=True)
    assert out2.status == EVAL_OK


def test_snapshot_detects_change(tmp_path):
    bench = _make_benchmark(tmp_path)
    before = snapshot_benchmark(bench)
    assert before is not None
    assert evaluation_is_valid(before, snapshot_benchmark(bench)) is True
    # Tamper with the benchmark → digest changes → invalid.
    (bench / "statement" / "README.md").write_text("changed task", encoding="utf-8")
    assert evaluation_is_valid(before, snapshot_benchmark(bench)) is False


def test_snapshot_missing_is_invalid(tmp_path):
    assert snapshot_benchmark(tmp_path / "nope") is None
    assert evaluation_is_valid(None, None) is False


def test_hidden_dotfiles_excluded(tmp_path):
    bench = tmp_path / "bench"
    (bench / "statement").mkdir(parents=True)
    (bench / "statement" / "README.md").write_text("task", encoding="utf-8")
    (bench / ".secrets").write_text("secret", encoding="utf-8")
    out = prepare_evaluation_workspace(bench, tmp_path / "eval")
    assert out.status == EVAL_OK
    assert not (out.workspace / ".secrets").exists()
