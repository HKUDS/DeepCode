"""Layering rule: ``core/agent_runtime`` must not depend on ``core.compat``.

There is no import cycle on the current tree. Each entrypoint imports cleanly as
the first import of a fresh interpreter, and the dependency graph runs
``core.compat -> {core.config, core.agent_runtime}`` plus
``core.config -> core.agent_runtime`` -- a DAG, not a loop.

``core.agent_runtime`` is the current runtime; ``core.compat`` is the legacy
shim that adapts the old ``mcp_agent`` surface. The dependency must not point
from the new runtime back into the shim.

This test pins that direction statically, so the edge cannot be re-introduced
while the graph still happens to load: a module-level ``core.compat`` import
anywhere under ``core/agent_runtime/`` fails here, at the line that adds it,
instead of surfacing later as an order-dependent ``ImportError`` at import time.

Two styles stay allowed, because neither executes at module import time:

* ``if TYPE_CHECKING:`` -- annotations only, erased at runtime.
* inside a function body -- deferred until the call.

A class body is still module-level execution, so an import there counts as an
offender.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PACKAGE = ROOT / "core" / "agent_runtime"


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
        "core/agent_runtime must not import core.compat at module level: the"
        " runtime is the current stack and core.compat is the legacy shim, so"
        " the edge would point backwards. Move the import under"
        " `if TYPE_CHECKING:` (annotations only) or into the calling function."
        f" Offenders: {offenders}"
    )
