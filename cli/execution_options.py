"""Shared command-line options for selecting an LLM execution profile."""

from __future__ import annotations

import argparse
import re

from core.domain.execution_profile import (
    MAX_CONTEXT_WINDOW_TOKENS,
    MIN_CONTEXT_WINDOW_TOKENS,
)
from core.domain.execution_security import ExecutionAccessPreset

_CONTEXT_WINDOW = re.compile(r"^(\d+(?:\.\d+)?)\s*([km]?)$", re.IGNORECASE)


def add_reasoning_effort_argument(parser: argparse.ArgumentParser) -> None:
    """Add the model-aware reasoning override used by every CLI surface.

    The accepted levels are intentionally not an argparse ``choices`` list:
    providers advertise their own capabilities at runtime, and the shared
    execution-profile resolver is the single authority that validates them.
    """

    parser.add_argument(
        "--effort",
        dest="reasoning_effort",
        default=None,
        metavar="LEVEL",
        help=(
            "Reasoning effort for this session/command: auto, off, or a "
            "level supported by the selected model."
        ),
    )


def parse_context_window(value: str) -> int | None:
    """Parse a human context cap (``32k``, ``1m``), or ``auto`` to inherit."""

    clean = value.strip().lower()
    if clean in {"auto", "default", "inherit"}:
        return None
    match = _CONTEXT_WINDOW.fullmatch(clean)
    if match is None:
        raise ValueError("context window must be auto or a token count such as 32k")
    amount = float(match.group(1))
    multiplier = {"": 1, "k": 1_000, "m": 1_000_000}[match.group(2).lower()]
    tokens = amount * multiplier
    if not tokens.is_integer():
        raise ValueError("context window must resolve to a whole token count")
    parsed = int(tokens)
    if not MIN_CONTEXT_WINDOW_TOKENS <= parsed <= MAX_CONTEXT_WINDOW_TOKENS:
        raise ValueError(
            "context window must be between "
            f"{MIN_CONTEXT_WINDOW_TOKENS} and {MAX_CONTEXT_WINDOW_TOKENS} tokens"
        )
    return parsed


def add_access_preset_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--access",
        choices=("ask", "read-only", "full-access"),
        default=None,
        help=(
            "Tool access for this command: ask, read-only, or full-access. "
            "Full access disables approvals and the workspace sandbox."
        ),
    )


def add_workspace_trust_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--trust",
        action="store_true",
        help=(
            "Trust this canonical workspace for agent execution and remember "
            "the decision. This does not grant Full access."
        ),
    )


def parse_access_preset(value: str | None) -> ExecutionAccessPreset | None:
    if value is None:
        return None
    return ExecutionAccessPreset(value.replace("-", "_"))


__all__ = [
    "add_access_preset_argument",
    "add_reasoning_effort_argument",
    "add_workspace_trust_argument",
    "parse_access_preset",
    "parse_context_window",
]
