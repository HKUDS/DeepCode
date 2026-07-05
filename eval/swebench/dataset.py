"""Load the official SWE-bench Verified set and pick a reproducible subset.

The heavy lifting (HuggingFace download) is isolated in :func:`load_verified`
so the deterministic parts — subset selection and record→:class:`Instance`
mapping — stay pure and unit-testable without network or the ``datasets``
dependency.
"""

from __future__ import annotations

import json
from typing import Any

from eval.swebench.instance import Instance

_DATASET = "princeton-nlp/SWE-bench_Verified"
_SUBSET_SIZE = 50


def load_verified(split: str = "test") -> list[dict[str, Any]]:
    """Return the Verified records, or raise with install guidance.

    Requires the ``datasets`` package (and network on first download). Kept
    thin and side-effectful; everything downstream operates on plain dicts.
    """
    try:
        from datasets import load_dataset
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            "Loading SWE-bench Verified needs the 'datasets' package. Install "
            "it (pip install datasets) or run the harness with "
            "`uv run --with datasets ...`."
        ) from exc
    ds = load_dataset(_DATASET, split=split)
    return [dict(row) for row in ds]


def select_subset(
    records: list[dict[str, Any]], n: int = _SUBSET_SIZE
) -> list[dict[str, Any]]:
    """Deterministically pick ``n`` records, sorted by ``instance_id``.

    Lexicographic order is stable across runs and machines, so the baseline is
    reproducible: the same 50 instances every time, no RNG seed to track.
    """
    ordered = sorted(records, key=lambda r: r.get("instance_id", ""))
    return ordered[: max(0, n)]


def _parse_test_list(value: Any) -> list[str]:
    """SWE-bench stores FAIL_TO_PASS / PASS_TO_PASS as JSON-encoded lists."""
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(v) for v in parsed]
        except json.JSONDecodeError:
            return [value]
    return []


def to_instance(record: dict[str, Any]) -> Instance:
    """Map one SWE-bench record to an :class:`Instance` (official mode).

    ``fail_to_pass`` / ``pass_to_pass`` are carried for reference; official
    scoring is done by the ``swebench`` Docker harness, not by us.
    """
    return Instance(
        instance_id=str(record.get("instance_id", "")),
        problem_statement=str(record.get("problem_statement", "")),
        base_commit=str(record.get("base_commit", "")),
        repo=str(record.get("repo", "")),
        fail_to_pass=_parse_test_list(record.get("FAIL_TO_PASS")),
        pass_to_pass=_parse_test_list(record.get("PASS_TO_PASS")),
    )


def load_subset_instances(n: int = _SUBSET_SIZE, split: str = "test") -> list[Instance]:
    """Convenience: load Verified, take the deterministic subset, map to Instances."""
    return [to_instance(r) for r in select_subset(load_verified(split), n)]
