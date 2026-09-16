#!/usr/bin/env python
"""Verify the hash chain of a DeepCode transparency log (``llm.jsonl``).

Why this exists (P1-1 in ``docs/ROUTER_SUPPLY_CHAIN_HARDENING.md``): a
malicious model "router" between the harness and the provider can rewrite
the tool calls we receive and read every credential we send. Nothing the
client can compute proves such a relay is honest in real time — but each
LLM log entry is appended with ``entry_hash = sha256(canonical(entry) +
prev_hash)``, so *after the fact* we can still detect that the record was
edited, deleted, or reordered. That is the difference between "we cannot
tell" and "we can reconstruct which credentials went through which
endpoint in which session".

Usage::

    python scripts/verify_transparency_log.py <path-to-jsonl>

Exit codes:
    0  chain intact (an empty or zero-entry file is intact)
    1  chain broken — every problem is printed as ``<path>:<line>: why``
    2  the file could not be read at all (missing, a directory, no permission)

Only :mod:`core.observability.records` is imported for the canonicalization
and hash helpers — duplicating that logic here is exactly how the writer and
the verifier would drift apart and start producing false alarms.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Allow ``python scripts/verify_transparency_log.py`` from the repo root
# without an editable install. Discovery is not guaranteed to have run.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.observability.records import (
    canonical_json,
    compute_entry_hash,
)

_GENESIS = ""


@dataclass
class Problem:
    """One broken link, reported against its 1-based line in the file."""

    line_no: int
    reason: str


@dataclass
class VerifyResult:
    """Outcome of walking a whole log file."""

    checked: int = 0
    first_timestamp: str | None = None
    last_timestamp: str | None = None
    problems: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _entries_word(count: int) -> str:
    """Minor cosmetic helper so the summary reads naturally for 0/1/N."""
    return "entry" if count == 1 else "entries"


def _iter_entries(path: Path) -> Iterator[tuple[int, dict[str, Any] | str]]:
    """Yield ``(line_no, entry_or_error_text)`` for every non-blank line.

    Blank lines are skipped, not counted: a trailing newline or a stray CRLF
    is a file-format accident, not evidence. Line numbers stay physical so a
    report can be acted on with an editor.
    """
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line_no, raw in enumerate(fh, start=1):
            if not raw.strip():
                continue
            try:
                parsed = json.loads(raw)
            except ValueError as exc:
                yield line_no, f"not valid JSON ({exc})"
                continue
            if not isinstance(parsed, dict):
                yield line_no, "entry is not a JSON object"
                continue
            yield line_no, parsed


def verify_file(path: Path) -> VerifyResult:
    """Walk *path*, recompute every ``entry_hash`` and check the linkage.

    Two independent checks per entry, reported separately so the reason is
    precise instead of "chain broken somewhere":

    1. **Integrity** - recompute ``entry_hash`` from the entry's own fields
       (minus ``entry_hash``) over the canonical bytes the writer used. Any
       edited model/preview/endpoint/hash field changes the digest.
    2. **Linkage** - the stored ``prev_hash`` must equal the *stored*
       ``entry_hash`` of the previous entry (``""`` for line 1). Comparing
       stored-vs-stored keeps one tampered line from cascading into "every
       later line is broken", which would bury the real culprit.

    Note the chain is *tamper-evident*, not tamper-proof: an attacker who
    controls the file can rebuild the whole chain. What they cannot do is
    change one line and leave the rest untouched. Detection therefore relies
    on a copy of the chain (or the recovered ``entry_hash``) existing
    somewhere the writer does not control.
    """
    result = VerifyResult()
    expected_prev = _GENESIS
    first_line_seen = False

    for line_no, entry in _iter_entries(path):
        if isinstance(entry, str):
            result.problems.append(Problem(line_no, entry))
            # The chain cannot be followed past an unreadable line; keep
            # walking so the operator sees every bad line at once.
            continue

        result.checked += 1
        timestamp = entry.get("timestamp")
        if isinstance(timestamp, str):
            if not first_line_seen:
                result.first_timestamp = timestamp
            result.last_timestamp = timestamp

        stored_hash = entry.get("entry_hash")
        stored_prev = entry.get("prev_hash")

        if not isinstance(stored_hash, str) or not stored_hash:
            result.problems.append(
                Problem(
                    line_no,
                    "missing entry_hash - line is not part of the chain "
                    "(written before P1-1, or inserted/rewritten)",
                )
            )
        else:
            payload = {k: v for k, v in entry.items() if k != "entry_hash"}
            prev_for_hash = stored_prev if isinstance(stored_prev, str) else None
            recomputed = compute_entry_hash(payload, prev_for_hash)
            if recomputed != stored_hash:
                result.problems.append(
                    Problem(
                        line_no,
                        "entry_hash mismatch - entry content was modified "
                        f"(stored {stored_hash[:16]}..., recomputed "
                        f"{recomputed[:16]}...; canonical bytes "
                        f"{len(canonical_json(payload))} chars)",
                    )
                )

        if not isinstance(stored_prev, str):
            result.problems.append(
                Problem(line_no, "missing prev_hash - entry is not linked")
            )
        elif stored_prev != expected_prev:
            if first_line_seen:
                reason = (
                    "prev_hash does not match the previous entry's entry_hash "
                    f"(expected {expected_prev[:16]}..., got {stored_prev[:16]}...) "
                    "- a line was deleted, reordered, or replaced"
                )
            else:
                reason = (
                    "first entry must chain to the empty string, got "
                    f"{stored_prev[:16]}..."
                )
            result.problems.append(Problem(line_no, reason))

        # Follow the *stored* hash so an isolated edit is reported once.
        expected_prev = stored_hash if isinstance(stored_hash, str) else _GENESIS
        first_line_seen = True

    return result


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="verify_transparency_log.py",
        description=(
            "Verify the append-only hash chain of a DeepCode LLM transparency log."
        ),
    )
    parser.add_argument(
        "path",
        help="path to the JSONL log to verify (usually <task>/logs/llm.jsonl)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the process exit code."""
    args = _parse_args(argv)
    path = Path(args.path).expanduser()
    if path.is_dir():
        print(f"error: {path} is a directory, expected a JSONL file", file=sys.stderr)
        return 2
    if not path.exists():
        print(f"error: {path} does not exist", file=sys.stderr)
        return 2

    try:
        result = verify_file(path)
    except OSError as exc:
        print(f"error: cannot read {path}: {exc}", file=sys.stderr)
        return 2

    last = result.last_timestamp or "(none)"
    if result.problems:
        for problem in result.problems:
            print(f"{path}:{problem.line_no}: {problem.reason}", file=sys.stderr)
        print(
            f"FAIL: {len(result.problems)} problem(s) across "
            f"{result.checked} {_entries_word(result.checked)}",
            file=sys.stderr,
        )
        return 1

    print(
        f"OK: verified {result.checked} {_entries_word(result.checked)}; "
        f"first={result.first_timestamp or '(none)'}, last={last}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
