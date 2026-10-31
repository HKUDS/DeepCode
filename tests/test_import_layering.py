"""Regression guard: the core stack must import without order-dependent cycles.

Topology
--------
The core layering has three nodes that historically formed a 3-way import cycle::

    core.config  ──imports──▶  core.agent_runtime.tools.mcp
          ▲                            │
          │                            ▼
    core.compat.runtime ◀──core.compat.agent── core.compat (``__init__``)

``core/compat/parallel.py`` defers its ``core.compat.agent`` import under
``TYPE_CHECKING`` precisely because a module-level import closes the cycle
above and turns ``import core.config`` into
``ImportError: cannot import name 'DeepCodeConfig'`` — a failure that used to
surface only as a confusing *collection* error in ``test_config_layering.py``.

``core/agent_runtime/parallel.py`` likewise defers its ``core.compat.parallel``
import into ``run_parallel_llm`` (call time) so that re-exporting the entry
point from ``core/agent_runtime/__init__.py`` does not re-close the cycle.

Two tests pin the invariant, both cheap and fully local:

1. ``test_entrypoints_import_in_fresh_interpreter`` — symptom guard: each
   entrypoint must import in a brand-new interpreter (circular imports are
   order-sensitive, so an already-warmed session proves nothing).
2. ``test_agent_runtime_has_no_module_level_compat_import`` — cause guard:
   nothing under ``core/agent_runtime/`` may import ``core.compat`` at module
   level, even when the cycle is not currently closed (catches the landmine
   before it detonates).
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PACKAGE = ROOT / "core" / "agent_runtime"

ENTRYPOINTS = ("core.config", "core.compat", "core.agent_runtime")


@pytest.mark.parametrize("module", ENTRYPOINTS)
def test_entrypoints_import_in_fresh_interpreter(module: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert result.returncode == 0, (
        f"import {module} failed in a fresh interpreter (rc={result.returncode})."
        " This is usually the core.config <-> core.agent_runtime <-> core.compat"
        " circular import regressing: grep for a new module-level `core.compat`"
        f" import under core/agent_runtime/.\nstderr: {result.stderr.strip()}"
    )


def _is_deferred(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """True when the import runs at call time or never (function / TYPE_CHECKING)."""
    cur = parents.get(node)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return True
        if isinstance(cur, ast.If) and "TYPE_CHECKING" in ast.dump(cur.test):
            return True
        cur = parents.get(cur)
    return False


def test_agent_runtime_has_no_module_level_compat_import() -> None:
    offenders: list[str] = []
    for path in sorted(RUNTIME_PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                refs = (node.module or "",)
            elif isinstance(node, ast.Import):
                refs = tuple(alias.name for alias in node.names)
            else:
                continue
            mentions_compat = any(ref.startswith("core.compat") for ref in refs)
            if mentions_compat and not _is_deferred(node, parents):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, (
        "core/agent_runtime must not import core.compat at module level (the"
        " new core stack must not depend on the legacy shim at import time;"
        " that edge closes the core.config cycle). Move the import under"
        " `if TYPE_CHECKING:` (annotations only) or into the calling function."
        f" Offenders: {offenders}"
    )
