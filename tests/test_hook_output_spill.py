"""Hook context is capped, and the overflow is recoverable rather than lost."""

from __future__ import annotations

from pathlib import Path

from core.harness.hooks import execution


def test_short_context_passes_through_unchanged() -> None:
    assert execution._bounded_context("  build is green  ") == "build is green"


def test_empty_context_is_none() -> None:
    assert execution._bounded_context("   ") is None
    assert execution._bounded_context(None) is None


def test_long_context_is_capped_and_spilled() -> None:
    limit = execution._CONTEXT_CHAR_LIMIT
    payload = "x" * (limit + 500)
    bounded = execution._bounded_context(payload)
    assert bounded is not None
    assert len(bounded) < len(payload)
    assert bounded.startswith("x" * limit)
    assert "truncated at" in bounded
    assert "full text saved to" in bounded

    # The path in the message must be the file that holds the whole payload.
    marker = "full text saved to "
    path = Path(bounded.split(marker, 1)[1].rstrip("]"))
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == payload


def test_truncation_keeps_the_prefix_the_hook_emitted() -> None:
    prefix = "SUMMARY: 3 files changed\n"
    bounded = execution._bounded_context(
        prefix + "y" * (execution._CONTEXT_CHAR_LIMIT * 2)
    )
    assert bounded is not None
    assert bounded.startswith(prefix)
