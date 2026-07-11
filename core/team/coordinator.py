"""Coordinator — the lead that decomposes a feature and merges the workers (P4).

The team's control flow, as deterministic code (not English prose in a prompt
— §8 anti-pattern #4):

1. **decompose** — one agent turn splits the feature into a task board (each
   task: a spec, a verification command, dependencies);
2. **dispatch** — N workers run concurrently, each work-stealing ready tasks
   off the board, building in its own git worktree (isolation), then merging
   back; base-repo git ops are serialised, the actual building runs in
   parallel;
3. **verify** — a final CriteriaExecutedGate runs the overall test command on
   the merged result (verify by real execution — §8 anti-pattern #2).

Everything expensive (a worker) is a P3 LoopTask; everything durable (the
board) is SQLite; isolation is git worktrees. The coordinator only wires them.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from core.loop.backpressure import TestResult, run_tests
from core.team.board import Task, TaskBoard
from core.team.worker import LoopWorker, WorkerBackend, WorkerResult
from core.team.worktree import MergeResult, WorktreeManager

# (goal, num_workers, default_test_command) -> list of task dicts.
Decomposer = Callable[[str, int, str], Awaitable[list[dict]]]


@dataclass
class TeamEvent:
    """A lifecycle event the coordinator emits so a caller can stream progress.

    ``phase`` is one of ``decomposed`` / ``task_started`` / ``task_done`` /
    ``gate``; the other fields are populated for the phases they belong to.
    """

    phase: str
    task: Task | None = None
    result: WorkerResult | None = None
    merge: MergeResult | None = None
    tasks: list[Task] | None = None
    gate: TestResult | None = None


# One structured hook for the whole lifecycle (decompose → workers → gate).
TeamEventHook = Callable[[TeamEvent], None]


@dataclass
class TeamResult:
    succeeded: bool
    tasks_total: int
    tasks_done: int
    conflicts: list[str] = field(default_factory=list)
    gate: TestResult | None = None
    detail: str = ""


class Coordinator:
    """Run a feature to completion with a team of workers."""

    def __init__(
        self,
        *,
        goal: str,
        workspace: str,
        test_command: str = "",
        model: str | None = None,
        num_workers: int = 3,
        worker_max_rounds: int = 5,
        decomposer: Decomposer | None = None,
        worker_backend: WorkerBackend | None = None,
        on_event: TeamEventHook | None = None,
    ) -> None:
        self.goal = goal
        self.workspace = str(Path(workspace).resolve())
        self.test_command = test_command
        self.model = model
        self.num_workers = max(1, num_workers)
        self._decomposer = decomposer or _make_agent_decomposer(self.workspace, model)
        self._backend: WorkerBackend = worker_backend or LoopWorker(
            model=model, max_rounds=worker_max_rounds
        )
        self._on_event = on_event

    def _emit(self, event: TeamEvent) -> None:
        if self._on_event is not None:
            self._on_event(event)

    async def run(self) -> TeamResult:
        # 1) Decompose the feature into a validated task board.
        specs = await self._decomposer(self.goal, self.num_workers, self.test_command)
        board = TaskBoard(Path(self.workspace) / ".deepcode" / "team" / "board.db")
        for i, spec in enumerate(specs):
            board.add_task(
                spec.get("id") or f"t{i}",
                title=spec.get("title", ""),
                spec=spec.get("spec", spec.get("title", "")),
                test_command=spec.get("test_command", self.test_command),
                deps=spec.get("deps", []),
            )
        board.validate()  # raises on a dependency cycle / unknown dep
        self._emit(TeamEvent("decomposed", tasks=board.all_tasks()))

        # 2) Dispatch workers over isolated worktrees; serialise base git ops.
        wt = WorktreeManager(self.workspace)
        wt.ensure_base()
        git_lock = asyncio.Lock()
        conflicts: list[str] = []

        async def worker_loop(worker_id: str) -> None:
            while not board.all_settled():
                task = board.claim(worker_id)
                if task is None:
                    await asyncio.sleep(0.05)  # wait for a dependency to finish
                    continue
                self._emit(TeamEvent("task_started", task=task))
                try:
                    async with git_lock:
                        tree = wt.create(task.id)
                    result = await self._backend.run(task, tree, worker_id=worker_id)
                    async with git_lock:
                        if result.succeeded:
                            merge = wt.merge(task.id)
                        else:
                            merge = MergeResult(
                                task.id, clean=False, detail="worker failed"
                            )
                        wt.cleanup(task.id)
                except Exception as exc:  # noqa: BLE001
                    # A worker or git failure must fail only THIS task (so its
                    # dependents cascade-fail and the board still settles) — never
                    # crash the whole team or orphan the worktree.
                    async with git_lock:
                        try:
                            wt.cleanup(task.id)
                        except Exception:  # noqa: BLE001 - best-effort
                            pass
                    result = WorkerResult(worker_id, task.id, False, f"error: {exc}", 0)
                    merge = MergeResult(task.id, clean=False, detail="worker error")
                if not merge.clean and merge.conflicts:
                    conflicts.extend(merge.conflicts)
                success = result.succeeded and merge.clean
                board.complete(
                    task.id,
                    success=success,
                    result=f"{result.detail}; merge={merge.detail}",
                )
                self._emit(
                    TeamEvent("task_done", task=task, result=result, merge=merge)
                )

        await asyncio.gather(*(worker_loop(f"w{i}") for i in range(self.num_workers)))

        # 3) Final gate: run the overall tests on the merged result.
        gate = (
            run_tests(self.workspace, self.test_command) if self.test_command else None
        )
        self._emit(TeamEvent("gate", gate=gate))
        wt.cleanup_all()

        counts = board.counts()
        tasks_done = counts.get("done", 0)
        board_ok = board.all_succeeded()
        gate_ok = gate is None or gate.passed
        succeeded = board_ok and gate_ok
        detail = (
            "all tasks merged and tests pass"
            if succeeded
            else f"board_ok={board_ok} gate_ok={gate_ok}"
        )
        board.close()
        return TeamResult(
            succeeded=succeeded,
            tasks_total=len(specs),
            tasks_done=tasks_done,
            conflicts=sorted(set(conflicts)),
            gate=gate,
            detail=detail,
        )


# --------------------------------------------------------------------------
# Default decomposer — one agent turn that returns a JSON task list.
# --------------------------------------------------------------------------

_DECOMPOSE_PROMPT = (
    "You are the lead of a small engineering team. Break the following goal "
    "into {n} or fewer INDEPENDENT sub-tasks that different workers can build "
    "in parallel, plus any that must come after others.\n\n"
    "GOAL: {goal}\n\n"
    "Reply with ONLY a JSON array; each element: "
    '{{"id": "short-id", "title": "...", "spec": "a precise, self-contained '
    "instruction for one worker including exactly what file(s) to create and "
    'what to implement", "test_command": "{test}", "deps": ["ids this depends '
    'on"]}}. Prefer tasks that touch DIFFERENT files so they merge cleanly. '
    "IMPORTANT: if two sub-tasks must edit the SAME file, do NOT run them in "
    "parallel — give one a dependency on the other (via deps) so they are "
    "applied in sequence and never collide. Keep it to a handful of tasks."
)


def _extract_json_array(text: str) -> list[dict]:
    """Pull the first JSON array out of a model reply, tolerantly."""
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start, end = text.find("["), text.rfind("]")
        candidate = text[start : end + 1] if start != -1 and end > start else "[]"
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        try:
            import json_repair

            data = json_repair.loads(candidate)
        except Exception:
            return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def _make_agent_decomposer(workspace: str, model: str | None) -> Decomposer:
    async def decompose(goal: str, num_workers: int, test_command: str) -> list[dict]:
        from core.agent_setup import build_agent_session
        from core.events import UserInput

        session, _m, _e = build_agent_session(
            workspace=workspace, model=model, max_iterations=4
        )
        prompt = _DECOMPOSE_PROMPT.format(n=num_workers, goal=goal, test=test_command)
        final = ""
        async for event in session.run_stream(UserInput(text=prompt)):
            if event.msg.type == "task_complete":
                final = event.msg.final_text or ""
        tasks = _extract_json_array(final)
        # Fallback: if decomposition produced nothing usable, run the whole
        # goal as a single task rather than doing nothing.
        if not tasks:
            tasks = [
                {
                    "id": "t0",
                    "title": goal[:60],
                    "spec": goal,
                    "test_command": test_command,
                    "deps": [],
                }
            ]
        return tasks

    return decompose
