"""Tests for P0-1 GOAL.yaml model-writable control file (PenguinHarness)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.loop.goal_file import (
    GOAL_ACTIVE,
    GOAL_BLOCKED,
    GOAL_COMPLETE,
    GoalFile,
    goal_file_path,
    parse_goal_file,
    read_goal_file,
    read_goal_status,
    serialize_goal_file,
    write_goal_file,
)

# ---- serialization / parsing ------------------------------------------------


def test_serialize_roundtrip():
    goal = GoalFile(objective="make all tests pass", status=GOAL_COMPLETE)
    text = serialize_goal_file(goal)
    parsed = parse_goal_file(text)
    assert parsed is not None
    assert parsed.objective == "make all tests pass"
    assert parsed.status == GOAL_COMPLETE


def test_parse_quoted_values():
    text = 'objective: "build a RAG app"\nstatus: active\n'
    parsed = parse_goal_file(text)
    assert parsed is not None and parsed.objective == "build a RAG app"


def test_parse_single_quoted_values():
    text = "objective: 'fix auth'\nstatus: complete\n"
    parsed = parse_goal_file(text)
    assert parsed is not None and parsed.objective == "fix auth"


def test_parse_ignores_comments_and_blank_lines():
    text = "# deepcode goal\n\nobjective: make tests pass\n\nstatus: active\n"
    parsed = parse_goal_file(text)
    assert parsed is not None and parsed.objective == "make tests pass"


def test_parse_json_fallback():
    parsed = parse_goal_file('{"objective": "json goal", "status": "blocked"}')
    assert parsed is not None
    assert parsed.objective == "json goal"
    assert parsed.status == GOAL_BLOCKED


def test_parse_garbage_returns_none():
    assert parse_goal_file("") is None
    assert parse_goal_file("not a control file") is None
    assert parse_goal_file("objective:") is None  # no value
    assert parse_goal_file("status: complete") is None  # no objective


def test_parse_status_defaults_active():
    parsed = parse_goal_file("objective: x\n")
    assert parsed is not None and parsed.status == GOAL_ACTIVE


# ---- file operations --------------------------------------------------------


def test_write_and_read_status(tmp_path):
    path = write_goal_file(tmp_path, GoalFile(objective="do the thing"))
    assert path == goal_file_path(tmp_path)
    assert path.is_file()
    assert read_goal_status(tmp_path) == GOAL_ACTIVE


def test_read_status_after_model_completes(tmp_path):
    write_goal_file(tmp_path, GoalFile(objective="do the thing"))
    # Simulate the model editing status to complete.
    path = goal_file_path(tmp_path)
    path.write_text("objective: do the thing\nstatus: complete\n", encoding="utf-8")
    assert read_goal_status(tmp_path) == GOAL_COMPLETE


def test_read_status_tolerates_corruption(tmp_path):
    write_goal_file(tmp_path, GoalFile(objective="do the thing"))
    goal_file_path(tmp_path).write_text("garbage{{{", encoding="utf-8")
    assert read_goal_status(tmp_path) == GOAL_BLOCKED  # broken → blocked


def test_read_status_missing_file_is_blocked(tmp_path):
    assert read_goal_status(tmp_path / "nope") == GOAL_BLOCKED


def test_read_status_out_of_protocol_normalizes(tmp_path):
    write_goal_file(tmp_path, GoalFile(objective="do the thing"))
    goal_file_path(tmp_path).write_text(
        "objective: do the thing\nstatus: whatever\n", encoding="utf-8"
    )
    assert read_goal_status(tmp_path) == GOAL_BLOCKED


def test_read_goal_file_returns_model_edits(tmp_path):
    write_goal_file(tmp_path, GoalFile(objective="original"))
    goal_file_path(tmp_path).write_text(
        "objective: tampered\nstatus: complete\n", encoding="utf-8"
    )
    goal = read_goal_file(tmp_path)
    assert goal is not None
    # Tolerant read: returns what the model wrote (the loop's canonical
    # objective lives elsewhere, so tampering is harmless).
    assert goal.objective == "tampered"
    assert goal.status == GOAL_COMPLETE


def test_read_goal_file_corrupt_returns_none(tmp_path):
    write_goal_file(tmp_path, GoalFile(objective="x"))
    goal_file_path(tmp_path).write_text("???", encoding="utf-8")
    assert read_goal_file(tmp_path) is None


def test_write_creates_parent_dirs(tmp_path):
    deep = tmp_path / "a" / "b" / "c"
    write_goal_file(deep, GoalFile(objective="x"))
    assert goal_file_path(deep).is_file()
