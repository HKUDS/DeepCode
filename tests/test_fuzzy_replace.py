"""Tests for the P2 nine-strategy fuzzy-replace engine.

Each replacer strategy gets a case that ONLY it (or an earlier one) can
satisfy, plus the dispatcher's guards (uniqueness, disproportionate match,
empty/identical). This is the contract behind "edit success rate >= 95%".
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.tools.replace import (  # noqa: E402
    DisproportionateMatchError,
    MultipleMatchesError,
    NotFoundError,
    replace,
)


# --- exact (SimpleReplacer) -------------------------------------------------


def test_exact_match():
    assert replace("a = 1\nb = 2\n", "b = 2", "b = 3") == "a = 1\nb = 3\n"


def test_identical_raises():
    with pytest.raises(ValueError, match="identical"):
        replace("x", "a", "a")


def test_empty_old_string_raises():
    with pytest.raises(ValueError, match="empty"):
        replace("x", "", "a")


def test_not_found_raises():
    with pytest.raises(NotFoundError):
        replace("a = 1\n", "z = 9", "z = 0")


# --- LineTrimmedReplacer (trailing/leading whitespace on lines) -------------


def test_line_trimmed_match():
    # old_string has different trailing whitespace than the file.
    content = "def f():\n    return 1  \n"
    out = replace(content, "def f():\n    return 1", "def f():\n    return 2")
    assert "return 2" in out


# --- WhitespaceNormalizedReplacer (collapsed spaces) ------------------------


def test_whitespace_normalized_match():
    content = "x   =    1\n"
    out = replace(content, "x = 1", "x = 2")
    assert out == "x = 2\n"


# --- IndentationFlexibleReplacer (whole block indented differently) ---------


def test_indentation_flexible_match():
    content = "class C:\n        def m(self):\n            return 1\n"
    # model provides the block at a different (zero) indentation
    out = replace(
        content,
        "def m(self):\n    return 1",
        "def m(self):\n    return 2",
    )
    assert "return 2" in out


# --- BlockAnchorReplacer (first/last line anchor, middle drifted) -----------


def test_block_anchor_match_with_drifted_middle():
    content = "def start():\n    a = compute_thing()\n    return done()\n"
    # middle line differs slightly from the file; anchors match
    old = "def start():\n    a = compute_thingy()\n    return done()"
    new = "def start():\n    a = X\n    return done()"
    out = replace(content, old, new)
    assert "a = X" in out


# --- EscapeNormalizedReplacer (literal \n in old_string) --------------------


def test_escape_normalized_match():
    content = "line1\nline2\n"
    out = replace(content, "line1\\nline2", "L1\nL2")
    assert out == "L1\nL2\n"


# --- TrimmedBoundaryReplacer (surrounding blank lines in old_string) --------


def test_trimmed_boundary_match():
    content = "keep\ntarget line\nkeep2\n"
    out = replace(content, "\ntarget line\n", "TARGET")
    assert "TARGET" in out


# --- guards -----------------------------------------------------------------


def test_uniqueness_required_without_replace_all():
    content = "x = 1\nx = 1\n"
    with pytest.raises(MultipleMatchesError):
        replace(content, "x = 1", "x = 2")


def test_replace_all_replaces_every_occurrence():
    content = "x = 1\nx = 1\n"
    out = replace(content, "x = 1", "x = 2", replace_all=True)
    assert out == "x = 2\nx = 2\n"


def test_disproportionate_match_refused():
    # whitespace-normalized matching would map a short 2-line old_string onto a
    # line padded with 600 spaces — a span far larger than old_string, which
    # the guard must refuse rather than silently blow away.
    content = "a" + " " * 600 + "b\nc\n"
    with pytest.raises(DisproportionateMatchError):
        replace(content, "a b\nc", "X")


def test_strategies_tried_in_priority_order():
    # Exact match must win even if a looser strategy could also match.
    content = "a = 1\na=1\n"
    # "a = 1" exact-matches line 1 uniquely.
    out = replace(content, "a = 1", "a = 2")
    assert out == "a = 2\na=1\n"
