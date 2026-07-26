"""Shared command-line options for selecting an LLM execution profile."""

from __future__ import annotations

import argparse


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


__all__ = ["add_reasoning_effort_argument"]
