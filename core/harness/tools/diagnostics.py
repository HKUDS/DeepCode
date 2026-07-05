"""Post-edit diagnostics — the edit->verify loop (P2).

After a write/edit, run fast checkers on the changed file and feed any errors
straight back into the tool result, so the model fixes mistakes in the same
turn instead of discovering them later (opencode's edit->LSP idea, adapted).

No hardcoded ``if ext == ".py"`` branching: checkers live in a declarative
registry keyed by file extension. Adding a language = adding a ``Checker`` to
the registry. A checker that isn't installed (``is_available()`` false) is
silently skipped, so the loop degrades gracefully and never blocks an edit.

Checks are non-executing (syntax/lint only): ``py_compile`` compiles without
running, ``node --check`` parses without running — safe to run on every edit.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass


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

    def check(self, path: str) -> list[Diagnostic]:  # pragma: no cover - abstract
        raise NotImplementedError


class PyCompileChecker(Checker):
    """Always-available Python syntax check (compiles, does not execute)."""

    name = "py_compile"
    extensions = (".py",)

    def check(self, path: str) -> list[Diagnostic]:
        import py_compile

        try:
            py_compile.compile(path, doraise=True)
            return []
        except py_compile.PyCompileError as exc:
            inner = getattr(exc, "exc_value", None)
            line = getattr(inner, "lineno", None)
            col = getattr(inner, "offset", None)
            msg = getattr(inner, "msg", None) or str(exc)
            return [
                Diagnostic(
                    line=line,
                    column=col,
                    severity="error",
                    message=msg,
                    source=self.name,
                )
            ]
        except (SyntaxError, ValueError) as exc:  # defensive
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

    def check(self, path: str) -> list[Diagnostic]:
        try:
            proc = subprocess.run(
                ["node", "--check", path],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if proc.returncode == 0:
            return []
        # stderr: "<file>:<line>\n<code>\n^\n\nSyntaxError: <message>"
        line = None
        message = (
            proc.stderr.strip().splitlines()[-1]
            if proc.stderr.strip()
            else "syntax error"
        )
        for token in proc.stderr.splitlines():
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

    def check(self, path: str) -> list[Diagnostic]:
        try:
            proc = subprocess.run(
                ["ruff", "check", "--output-format", "json", path],
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        try:
            items = json.loads(proc.stdout or "[]")
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


def run_diagnostics(
    path: str, checkers: tuple[Checker, ...] = DEFAULT_CHECKERS
) -> list[Diagnostic]:
    """Run every applicable + available checker on ``path``; collect results."""
    out: list[Diagnostic] = []
    for checker in checkers:
        if not checker.applies_to(path) or not checker.is_available():
            continue
        try:
            out.extend(checker.check(path))
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
