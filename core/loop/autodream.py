"""autodream — background consolidation of the agent's persistent memory (P3).

Over many sessions the memory under ``<workspace>/.deepcode/memory/`` drifts:
duplicate notes, stale facts, a MEMORY.md index that no longer matches the
files. autodream is a focused, off-the-critical-path agent pass that tidies
it — merge duplicates, drop the stale, keep MEMORY.md a tight index — using
the same ``memory`` tool the agent writes with.

Scope discipline (the plan's "沙箱强制写作用域"): the pass is driven by a
prompt that confines it to the memory tool, and — because the memory
directory lives inside the workspace — the P1 permission engine already
governs every write. It is a single agent turn (not a test-backed LoopTask):
memory tidiness has no test oracle, so there is nothing to backpressure on; a
before/after note-count check is the only mechanical signal we keep.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from core.agent_setup import build_agent_session
from core.events import UserInput
from core.harness.memory import memory_dir

_CONSOLIDATE_PROMPT = (
    "Consolidate your persistent memory. Use ONLY the `memory` tool — do not "
    "touch any other files. Steps: (1) `memory list`, then `memory read` each "
    "note; (2) merge duplicates and near-duplicates into a single note; "
    "(3) delete stale, obviously-wrong, or superseded notes; (4) rewrite "
    "`MEMORY.md` so it is a short, accurate index of the notes that remain. "
    "Keep every durable fact; only remove redundancy and staleness. Reply "
    "with a one-line summary of what you changed."
)


@dataclass
class AutodreamResult:
    ran: bool
    notes_before: int
    notes_after: int
    summary: str


def _note_count(workspace: str) -> int:
    d = memory_dir(workspace)
    if not d.is_dir():
        return 0
    return sum(1 for p in d.iterdir() if p.is_file())


async def consolidate_memory(
    workspace: str,
    *,
    model: str | None = None,
    connection_id: str | None = None,
    reasoning_effort: str | None = None,
    max_iterations: int = 20,
    prompt_runner: Callable[[str], Awaitable[tuple[str, str]]] | None = None,
) -> AutodreamResult:
    """Run one memory-consolidation pass over ``workspace``.

    A no-op (``ran=False``) when there is nothing stored yet.
    """
    before = _note_count(workspace)
    if before == 0:
        return AutodreamResult(False, 0, 0, "no memory to consolidate")

    if prompt_runner is not None:
        _stop_reason, final_text = await prompt_runner(_CONSOLIDATE_PROMPT)
        summary = final_text
    else:
        session, _model, _engine = build_agent_session(
            workspace=workspace,
            model=model,
            connection_id=connection_id,
            reasoning_effort=reasoning_effort,
            max_iterations=max_iterations,
        )
        summary = ""
        try:
            async for event in session.run_stream(UserInput(text=_CONSOLIDATE_PROMPT)):
                if event.msg.type == "task_complete":
                    summary = event.msg.final_text or ""
        finally:
            await session.aclose()
    first_line = summary.strip().splitlines()[:1]
    summary = first_line[0] if first_line else ""

    return AutodreamResult(
        ran=True,
        notes_before=before,
        notes_after=_note_count(workspace),
        summary=summary[:200],
    )
