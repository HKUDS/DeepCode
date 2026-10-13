"""Post-edit diagnostics — the edit->verify loop (P2).

After a write/edit, run fast checkers on the changed file and feed any errors
straight back into the tool result, so the model fixes mistakes in the same
turn instead of discovering them later (opencode's edit->LSP idea, adapted).

No hardcoded ``if ext == ".py"`` branching: checkers live in a declarative
registry keyed by file extension. Adding a language = adding a ``Checker`` to
the registry. A checker that isn't installed (``is_available()`` false) is
silently skipped, so the loop degrades gracefully and never blocks an edit.

Checks are non-executing (syntax/lint only): ``compile()`` compiles without
running, ``node --check`` parses without running — safe to run on every edit.
They are async and never block the event loop; a checker's process is ended
when the turn that started it is interrupted.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass

from core.agent_runtime.processes import run_captured


@dataclass(frozen=True)
class Diagnostic:
    line: int | None
    column: int | None
    severity: str  # "error" | "warning"
    message: str
    source: str  # which checker produced it


class Checker:
    """A language checker. Subclasses declare extensions + how to run."""

    name: str = "checker"
    extensions: tuple[str, ...] = ()

    def applies_to(self, path: str) -> bool:
        return path.endswith(self.extensions)

    def is_available(self) -> bool:  # pragma: no cover - trivial
        return True

    async def check(self, path: str) -> list[Diagnostic]:  # pragma: no cover
        raise NotImplementedError


class PyCompileChecker(Checker):
    """Always-available Python syntax check (compiles, does not execute)."""

    name = "py_compile"
    extensions = (".py",)

    async def check(self, path: str) -> list[Diagnostic]:
        return await asyncio.to_thread(self._compile, path)

    def _compile(self, path: str) -> list[Diagnostic]:
        # compile() as py_compile does, minus writing the bytecode into a
        # __pycache__ beside the file, which is the user's project.
        with open(path, "rb") as f:
            source = f.read()
        try:
            compile(source, path, "exec", dont_inherit=True)
            return []
        except (SyntaxError, ValueError) as exc:
            return [
                Diagnostic(
                    line=getattr(exc, "lineno", None),
                    column=getattr(exc, "offset", None),
                    severity="error",
                    message=getattr(exc, "msg", None) or str(exc),
                    source=self.name,
                )
            ]


class NodeCheckChecker(Checker):
    """JavaScript syntax check via ``node --check`` (parses, does not execute)."""

    name = "node --check"
    extensions = (".js", ".mjs", ".cjs")

    def is_available(self) -> bool:
        return shutil.which("node") is not None

    async def check(self, path: str) -> list[Diagnostic]:
        try:
            result = await run_captured(["node", "--check", path], timeout=15)
        except OSError:
            return []
        if result is None or result[0] == 0:
            return []
        # stderr: "<file>:<line>\n<code>\n^\n\nSyntaxError: <message>"
        stderr = result[2].decode("utf-8", errors="replace")
        line = None
        message = stderr.strip().splitlines()[-1] if stderr.strip() else "syntax error"
        for token in stderr.splitlines():
            if ":" in token and token.strip().startswith(path):
                tail = token.rsplit(":", 1)[-1].strip()
                if tail.isdigit():
                    line = int(tail)
                    break
        return [
            Diagnostic(
                line=line,
                column=None,
                severity="error",
                message=message,
                source=self.name,
            )
        ]


class RuffChecker(Checker):
    """Optional richer Python lint via ruff (skipped when not installed)."""

    name = "ruff"
    extensions = (".py",)

    def is_available(self) -> bool:
        return shutil.which("ruff") is not None

    async def check(self, path: str) -> list[Diagnostic]:
        try:
            result = await run_captured(
                ["ruff", "check", "--output-format", "json", path], timeout=20
            )
        except OSError:
            return []
        if result is None:
            return []
        try:
            items = json.loads(result[1] or b"[]")
        except json.JSONDecodeError:
            return []
        out: list[Diagnostic] = []
        for it in items:
            loc = it.get("location") or {}
            out.append(
                Diagnostic(
                    line=loc.get("row"),
                    column=loc.get("column"),
                    severity="warning",
                    message=f"{it.get('code', '')} {it.get('message', '')}".strip(),
                    source=self.name,
                )
            )
        return out


# Declarative registry. Add a language by appending a Checker here.
DEFAULT_CHECKERS: tuple[Checker, ...] = (
    PyCompileChecker(),
    RuffChecker(),
    NodeCheckChecker(),
)


async def run_diagnostics(
    path: str, checkers: tuple[Checker, ...] = DEFAULT_CHECKERS
) -> list[Diagnostic]:
    """Run every applicable + available checker on ``path``; collect results."""
    out: list[Diagnostic] = []
    for checker in checkers:
        if not checker.applies_to(path) or not checker.is_available():
            continue
        try:
            out.extend(await checker.check(path))
        except Exception:  # noqa: BLE001 - a checker crash must not break the edit
            continue
    return out


def format_diagnostics(diagnostics: list[Diagnostic]) -> str:
    """Render diagnostics as a compact block appended to a tool result."""
    if not diagnostics:
        return ""
    errors = [d for d in diagnostics if d.severity == "error"]
    header = (
        "Diagnostics detected in this file — please fix:"
        if errors
        else "Lint warnings in this file:"
    )
    lines = [header]
    for d in diagnostics:
        loc = f"line {d.line}" if d.line else "?"
        lines.append(f"  [{d.severity}] {loc}: {d.message} ({d.source})")
    return "\n".join(lines)
