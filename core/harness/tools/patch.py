"""``apply_patch`` — multi-file semantic-anchor patches (P2, L2).

The ``edit`` tool changes one span in one file. Real changes often touch
several files together — add a module, update two call sites, delete a dead
helper — and want to land atomically or not at all. ``apply_patch`` takes the
Codex/OpenAI patch envelope (DEEPCODE_V2_MASTER_PLAN.md §7 P2, "apply_patch
语义锚点") and applies it as one unit::

    *** Begin Patch
    *** Add File: pkg/new.py
    +def f():
    +    return 1
    *** Update File: pkg/existing.py
    @@ def existing():
     def existing():
    -    return old()
    +    return new()
    *** Delete File: pkg/dead.py
    *** End Patch

Design decisions, all in service of reliability (mechanism, not model
judgement):

* **Anchors, not line numbers.** A hunk locates its span by surrounding
  context lines, so it survives the file drifting a few lines — the same
  motivation as the fuzzy ``edit``. We *reuse* that engine: each hunk's
  context+removed lines become ``old_string`` and its context+added lines
  become ``new_string``, fed to :func:`core.harness.tools.replace.replace`.
  No second matching implementation (anti-redundancy, /goal).
* **``@@`` headers are annotation, not literal content.** They name the
  enclosing scope (a function/class the hunk lives in) but are not adjacent
  to the changed lines, so using them as match context would break the
  match. They delimit hunks and are otherwise ignored; the context lines do
  the locating.
* **All-or-nothing.** Parse and compute every file's new content in memory
  first; only if *every* operation validates do we touch disk. A patch that
  fails on its third file leaves the first two untouched. Sections apply in
  order, each to what the earlier ones left, so two sections for one file
  compose. A write that still fails on disk (permissions, a file where a
  directory must go) puts back every file already changed.
* **Errors as data.** Every failure is returned as an ``"Error: ..."``
  string (never raised past the tool), matching the kernel contract.

Paths are fenced to the workspace exactly like ``write``/``edit``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.agent_runtime.tools.base import Tool, tool_parameters
from core.harness.tools.diagnostics import format_diagnostics, run_diagnostics
from core.harness.tools.files import _resolve, _within
from core.harness.tools.replace import ReplaceError, replace

_BEGIN = "*** Begin Patch"
_END = "*** End Patch"
_ADD = "*** Add File: "
_UPDATE = "*** Update File: "
_DELETE = "*** Delete File: "
_MOVE = "*** Move to: "


class PatchError(ValueError):
    """A malformed patch envelope or hunk (surfaced to the model)."""


@dataclass(frozen=True)
class Hunk:
    """One contiguous change: context+removed → context+added."""

    before: str
    after: str


@dataclass(frozen=True)
class FileOp:
    """A single file operation parsed from the envelope."""

    kind: str  # "add" | "update" | "delete"
    path: str
    move_to: str | None = None
    add_content: str | None = None
    hunks: list[Hunk] = field(default_factory=list)


def _hunk_from_lines(lines: list[tuple[str, str]]) -> Hunk | None:
    """Build a :class:`Hunk` from classified ``(op, text)`` lines.

    ``op`` is ``" "`` (context), ``"-"`` (removed) or ``"+"`` (added).
    ``before`` keeps context+removed; ``after`` keeps context+added. Returns
    ``None`` for a no-op hunk (only context, nothing changed).
    """
    before: list[str] = []
    after: list[str] = []
    changed = False
    for op, text in lines:
        if op == " ":
            before.append(text)
            after.append(text)
        elif op == "-":
            before.append(text)
            changed = True
        elif op == "+":
            after.append(text)
            changed = True
    if not changed:
        return None
    return Hunk(before="\n".join(before), after="\n".join(after))


def _classify(line: str) -> tuple[str, str]:
    """Split a body line into ``(op, text)``.

    Context lines carry a leading space; removals ``-``; additions ``+``. A
    genuinely empty line represents a blank context line.
    """
    if line == "":
        return " ", ""
    head, rest = line[0], line[1:]
    if head in (" ", "-", "+"):
        return head, rest
    # A body line with no recognised prefix is treated as context; this is
    # lenient toward models that drop the leading space on unchanged lines.
    return " ", line


def parse_patch(text: str) -> list[FileOp]:
    """Parse a patch envelope into a list of :class:`FileOp`.

    Pure and independently testable. Raises :class:`PatchError` on a missing
    envelope, an unknown directive, or a file section with no usable hunk.
    """
    raw = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.split("\n")

    # Tolerate leading/trailing chatter around the sentinels.
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == _BEGIN)
    except StopIteration:
        raise PatchError(f"patch must start with '{_BEGIN}'")
    end = next(
        (i for i in range(len(lines) - 1, -1, -1) if lines[i].strip() == _END),
        len(lines),
    )
    body = lines[start + 1 : end]

    ops: list[FileOp] = []
    i = 0
    n = len(body)
    while i < n:
        line = body[i]
        if line.startswith(_ADD):
            path = line[len(_ADD) :].strip()
            i += 1
            added: list[str] = []
            while i < n and not body[i].startswith("*** "):
                op, txt = _classify(body[i])
                # Add File bodies are all "+"; accept context too, defensively.
                added.append(txt if op in ("+", " ") else body[i])
                i += 1
            ops.append(FileOp(kind="add", path=path, add_content="\n".join(added)))
            continue

        if line.startswith(_UPDATE):
            path = line[len(_UPDATE) :].strip()
            i += 1
            move_to: str | None = None
            if i < n and body[i].startswith(_MOVE):
                move_to = body[i][len(_MOVE) :].strip()
                i += 1
            hunk_lines: list[tuple[str, str]] = []
            hunks: list[Hunk] = []

            def _flush() -> None:
                if hunk_lines:
                    h = _hunk_from_lines(hunk_lines)
                    if h is not None:
                        hunks.append(h)

            while i < n and not body[i].startswith("*** "):
                cur = body[i]
                if cur.startswith("@@"):
                    _flush()
                    hunk_lines = []
                    i += 1
                    continue
                hunk_lines.append(_classify(cur))
                i += 1
            _flush()
            if not hunks:
                raise PatchError(
                    f"update for {path!r} has no change hunks (only context?)."
                )
            ops.append(FileOp(kind="update", path=path, move_to=move_to, hunks=hunks))
            continue

        if line.startswith(_DELETE):
            path = line[len(_DELETE) :].strip()
            ops.append(FileOp(kind="delete", path=path))
            i += 1
            continue

        if line.strip() == "":
            i += 1
            continue

        raise PatchError(f"unexpected patch directive: {line!r}")

    if not ops:
        raise PatchError("patch contains no file operations.")
    return ops


def _restore(done: list[tuple[Path, bytes | None]]) -> list[Path]:
    """Put each file back as it was, newest change first; return failures."""
    failed: list[Path] = []
    for target, original in reversed(done):
        try:
            if original is None:
                if target.exists():
                    target.unlink()
            elif not target.exists() or target.read_bytes() != original:
                target.write_bytes(original)
        except OSError:
            failed.append(target)
    return failed


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "patch": {
                "type": "string",
                "description": (
                    "A patch in the '*** Begin Patch' / '*** End Patch' envelope "
                    "with '*** Add File:', '*** Update File:' (with @@ hunks of "
                    "space/-/+ lines), '*** Delete File:', and optional "
                    "'*** Move to:' directives."
                ),
            }
        },
        "required": ["patch"],
    }
)
class ApplyPatchTool(Tool):
    """Apply a multi-file patch atomically, with fuzzy hunk matching."""

    def __init__(
        self,
        workspace: str,
        diagnostics=run_diagnostics,
        *,
        allow_outside_workspace: bool = False,
    ):
        self._workspace = str(workspace)
        self._diagnostics = diagnostics
        self._allow_outside_workspace = allow_outside_workspace

    @property
    def name(self) -> str:
        return "apply_patch"

    @property
    def description(self) -> str:
        scope = (
            " Absolute paths outside the workspace are allowed by Full Access."
            if self._allow_outside_workspace
            else ""
        )
        return (
            "Apply a multi-file patch (Add/Update/Delete/Move) atomically. "
            "Hunks locate their span by surrounding context, so they tolerate "
            "small drift. Prefer this over several edit calls when a change "
            f"spans files or must land all-or-nothing.{scope}"
        )

    async def execute(self, **kwargs: Any) -> Any:
        patch_text = kwargs.get("patch", "")
        if not isinstance(patch_text, str) or not patch_text.strip():
            return "Error: patch is empty."
        try:
            ops = parse_patch(patch_text)
        except PatchError as exc:
            return f"Error: {exc}"

        # -- Phase 1: apply every op, in order, to an in-memory view of the --
        # -- files it touches (None = deleted); abort on the first problem. --
        planned: dict[Path, str | None] = {}
        displays: dict[Path, str] = {}

        def exists(target: Path) -> bool:
            if target in planned:
                return planned[target] is not None
            return target.exists()

        for op in ops:
            target = _resolve(self._workspace, op.path)
            if not self._allow_outside_workspace and not _within(
                self._workspace, target
            ):
                return (
                    f"Error: refusing to touch a path outside the workspace: {op.path}."
                )

            if op.kind == "add":
                if exists(target):
                    return (
                        f"Error: cannot add {op.path}: it already exists. Use an "
                        "Update File hunk to change it."
                    )
                planned[target] = op.add_content or ""
                displays[target] = op.path
                continue

            if op.kind == "delete":
                if not exists(target):
                    return f"Error: cannot delete {op.path}: file not found."
                planned[target] = None
                displays[target] = op.path
                continue

            # update (optionally a move)
            if not exists(target):
                return (
                    f"Error: cannot update {op.path}: file not found. Use "
                    "Add File to create it."
                )
            content = planned.get(target)
            if content is None:
                try:
                    content = target.read_text(encoding="utf-8")
                except OSError as exc:
                    return f"Error: could not read {op.path}: {exc}"
            for hunk in op.hunks:
                try:
                    content = replace(content, hunk.before, hunk.after)
                except ReplaceError as exc:
                    return f"Error: in {op.path}: {exc}"
                except ValueError as exc:
                    return f"Error: in {op.path}: {exc}"
            if op.move_to:
                dest = _resolve(self._workspace, op.move_to)
                if not self._allow_outside_workspace and not _within(
                    self._workspace, dest
                ):
                    return (
                        f"Error: refusing to move {op.path} outside the "
                        f"workspace: {op.move_to}."
                    )
                # Set before the destination, so a move onto itself keeps it.
                planned[target] = None
                displays[target] = op.path
                planned[dest] = content
                displays[dest] = op.move_to
            else:
                planned[target] = content
                displays[target] = op.path

        # -- Phase 2: commit, remembering what each file held so a failure --
        # -- part way can put everything back.                              --
        done: list[tuple[Path, bytes | None]] = []
        touched: list[str] = []
        try:
            for target, content in planned.items():
                original = target.read_bytes() if target.exists() else None
                if content is None and original is None:
                    continue  # added and deleted within this patch
                done.append((target, original))
                if content is None:
                    target.unlink()
                    touched.append(f"{displays[target]} (deleted)")
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                    touched.append(displays[target])
        except OSError as exc:
            failed = _restore(done)
            if failed:
                names = ", ".join(displays[path] for path in failed)
                return (
                    f"Error: patch failed while writing: {exc}. These files "
                    f"could not be restored: {names}"
                )
            return f"Error: patch not applied, nothing was changed: {exc}"

        # -- Phase 3: diagnostics on every file we wrote. --
        reports: list[str] = []
        for target, content in planned.items():
            if content is None:
                continue
            # Checkers run subprocesses (ruff up to 20s); keep them off the loop.
            diagnostics = await asyncio.to_thread(self._diagnostics, str(target))
            report = format_diagnostics(diagnostics)
            if report:
                reports.append(f"{displays[target]}:\n{report}")
        summary = f"Applied patch: {len(touched)} change(s) — " + ", ".join(touched)
        if reports:
            return summary + "\n\n" + "\n\n".join(reports)
        return summary
