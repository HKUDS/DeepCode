"""Planning-phase persistence and validation helpers.

This module keeps Phase 5 runtime bookkeeping out of the orchestration
function: attempt logs, latest runner checkpoint, final metadata, and cheap
plan-shape validation.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REQUIRED_PLAN_SECTIONS = (
    "file_structure",
    "implementation_components",
    "validation_approach",
    "environment_setup",
    "implementation_strategy",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def planning_paths(paper_dir: str | Path) -> dict[str, Path]:
    root = Path(paper_dir)
    return {
        "checkpoint": root / "planning_checkpoint.json",
        "attempts": root / "planning_attempts.jsonl",
        "meta": root / "planning_result_meta.json",
    }


def _json_default(value: Any) -> str:
    return str(value)


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    tmp.replace(target)


def read_json(path: str | Path) -> dict[str, Any] | None:
    target = Path(path)
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def append_jsonl(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=_json_default))
        handle.write("\n")


def write_planning_meta(paper_dir: str | Path, payload: dict[str, Any]) -> None:
    enriched = {**payload, "updated_at": utc_now_iso()}
    write_json(planning_paths(paper_dir)["meta"], enriched)


def read_planning_meta(paper_dir: str | Path) -> dict[str, Any] | None:
    return read_json(planning_paths(paper_dir)["meta"])


def append_planning_attempt(paper_dir: str | Path, payload: dict[str, Any]) -> None:
    enriched = {**payload, "updated_at": utc_now_iso()}
    append_jsonl(planning_paths(paper_dir)["attempts"], enriched)


def clear_planning_checkpoint(paper_dir: str | Path) -> None:
    checkpoint = planning_paths(paper_dir)["checkpoint"]
    try:
        checkpoint.unlink(missing_ok=True)
    except OSError:
        pass


def build_planning_checkpoint_callback(
    paper_dir: str | Path,
    *,
    attempt: int,
    mode: str,
):
    """Return an async callback suitable for ``AgentRunSpec.checkpoint_callback``."""
    checkpoint_path = planning_paths(paper_dir)["checkpoint"]

    async def _checkpoint(payload: dict[str, Any]) -> None:
        write_json(
            checkpoint_path,
            {
                "phase": "code_planning",
                "attempt": attempt,
                "mode": mode,
                "updated_at": utc_now_iso(),
                **payload,
            },
        )

    return _checkpoint


def extract_yaml_candidate(text: str) -> str:
    """Return the most likely YAML block from a planner response."""
    if not text:
        return ""
    fenced = re.search(r"```(?:yaml|yml)?\s*(.*?)```", text, re.IGNORECASE | re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    return text.strip()


def validate_plan_text(text: str) -> dict[str, Any]:
    """Validate the reproduction plan shape without requiring perfect YAML."""
    candidate = extract_yaml_candidate(text)
    lower_text = (text or "").lower()
    string_missing = [
        section for section in REQUIRED_PLAN_SECTIONS if f"{section}:" not in lower_text
    ]

    result: dict[str, Any] = {
        "yaml_valid": False,
        "yaml_error": None,
        "required_sections": list(REQUIRED_PLAN_SECTIONS),
        "missing_sections": list(string_missing),
        "sections_found": len(REQUIRED_PLAN_SECTIONS) - len(string_missing),
        "valid": False,
    }

    try:
        parsed = yaml.safe_load(candidate)
    except Exception as exc:
        result["yaml_error"] = f"{type(exc).__name__}: {exc}"
        result["valid"] = not string_missing
        return result

    if not isinstance(parsed, dict):
        result["yaml_error"] = f"parsed YAML is {type(parsed).__name__}, expected dict"
        result["valid"] = not string_missing
        return result

    result["yaml_valid"] = True
    section_source = parsed
    nested = parsed.get("complete_reproduction_plan")
    if isinstance(nested, dict):
        section_source = nested

    yaml_missing = [
        section for section in REQUIRED_PLAN_SECTIONS if section not in section_source
    ]
    result["missing_sections"] = yaml_missing
    result["sections_found"] = len(REQUIRED_PLAN_SECTIONS) - len(yaml_missing)
    result["valid"] = not yaml_missing
    return result


def coerce_text_to_minimal_plan(text: str, *, paper_dir: str | Path) -> str:
    """Wrap free-form planner output in the required YAML plan shape.

    Some providers answer the planning prompt with useful analysis but do not
    emit the exact YAML sections. This fallback preserves that analysis and
    creates a conservative plan that downstream code can consume instead of
    failing the entire workflow before implementation starts.
    """
    summary = (text or "").strip()
    if len(summary) > 6000:
        summary = summary[:6000].rstrip() + "\n...[truncated]"

    payload = {
        "file_structure": {
            "root": "generate_code",
            "files": [
                {
                    "path": "README.md",
                    "purpose": "Summarize the paper reproduction target and usage.",
                },
                {
                    "path": "src/main.py",
                    "purpose": "Provide an executable entrypoint for the reproduction scaffold.",
                },
                {
                    "path": "src/pipeline.py",
                    "purpose": "Implement the core algorithmic pipeline inferred from the paper.",
                },
                {
                    "path": "tests/test_pipeline.py",
                    "purpose": "Smoke-test the generated pipeline with minimal data.",
                },
            ],
        },
        "implementation_components": [
            {
                "name": "paper_interpretation",
                "description": "Convert the planner analysis into concrete modules and APIs.",
            },
            {
                "name": "core_pipeline",
                "description": "Implement the main method described by the paper at scaffold fidelity.",
            },
            {
                "name": "validation_smoke_test",
                "description": "Add a fast validation path that confirms imports and basic execution.",
            },
        ],
        "validation_approach": {
            "strategy": "Use lightweight unit and smoke tests because the model did not produce a full experimental protocol.",
            "commands": ["python -m pytest tests"],
        },
        "environment_setup": {
            "language": "python",
            "dependencies": ["pytest"],
            "notes": "Keep dependencies minimal unless the implementation step identifies explicit paper requirements.",
        },
        "implementation_strategy": {
            "approach": "Start from the preserved planner analysis, implement a small runnable scaffold, then expand only where the paper details are explicit.",
            "paper_dir": str(paper_dir),
            "planner_analysis": summary or "Planner did not return usable analysis.",
        },
    }
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


def is_existing_plan_usable(
    initial_plan_path: str | Path,
    *,
    paper_dir: str | Path,
    min_chars: int = 500,
) -> tuple[bool, dict[str, Any]]:
    """Return whether an existing ``initial_plan.txt`` can be reused."""
    path = Path(initial_plan_path)
    meta = read_planning_meta(paper_dir)
    if not path.exists():
        return False, {"reason": "missing_initial_plan", "meta": meta}

    text = path.read_text(encoding="utf-8")
    validation = validate_plan_text(text)
    reusable = len(text.strip()) >= min_chars and bool(validation["valid"])
    if meta and meta.get("status") == "success":
        reusable = reusable and bool(meta.get("plan_validation", {}).get("valid", True))

    return reusable, {
        "reason": "usable" if reusable else "invalid_existing_plan",
        "meta": meta,
        "plan_chars": len(text),
        "plan_validation": validation,
    }
