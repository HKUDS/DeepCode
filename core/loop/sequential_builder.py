"""P2-A9 (GenAI lesson 17): sequential chain builder.

Lesson 17's Agent Framework provides a ``SequentialBuilder`` — a linear
pipeline where context flows along the chain (each stage sees its
predecessor's outcome). DeepCode's ``AgentControl.spawn`` already supports
``fork_turns`` context inheritance and concurrent fan-out; this module adds
the explicit *sequential* abstraction on top: define stages, each stage's
task may reference the previous stage's result, and the chain is executed in
order with results flowing forward.

Pure orchestration description + executor contract — no I/O, no subprocess.
The executor is a callable the host supplies (e.g. wired to ``AgentControl``
or ``workflow_service``), keeping this module host-agnostic and testable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# Placeholder syntax for "previous stage's result" inside a task string.
_RESULT_TOKEN = "{previous_result}"
# Result of the first stage when referenced (no predecessor).
_FIRST_RESULT = "(no previous stage)"


@dataclass(frozen=True, slots=True)
class ChainStage:
    """One step in a sequential chain."""

    name: str
    task: str
    persona: str | None = None
    tools: tuple[str, ...] | None = None
    output_schema: dict[str, Any] | None = None
    isolate: bool = True

    def render_task(self, previous_result: str | None) -> str:
        """Substitute the previous stage's result into the task template."""
        if not previous_result:
            return self.task
        return self.task.replace(_RESULT_TOKEN, previous_result)


@dataclass(slots=True)
class SequentialChain:
    """An ordered pipeline of stages with forward context flow."""

    name: str
    stages: list[ChainStage] = field(default_factory=list)

    def add(self, stage: ChainStage) -> SequentialChain:
        self.stages.append(stage)
        return self

    @property
    def stage_count(self) -> int:
        return len(self.stages)

    def validate(self) -> list[str]:
        """Static validation: unique stage names, non-empty tasks."""
        errors: list[str] = []
        seen: set[str] = set()
        for stage in self.stages:
            if not stage.name.strip():
                errors.append("stage name must not be empty")
            elif stage.name in seen:
                errors.append(f"duplicate stage name: {stage.name!r}")
            seen.add(stage.name)
            if not str(stage.task or "").strip():
                errors.append(f"stage {stage.name!r} has an empty task")
        return errors


async def run_sequential(
    chain: SequentialChain,
    executor: Callable[..., Any],
    *,
    on_stage_done: Callable[[ChainStage, Any], None] | None = None,
) -> list[Any]:
    """Execute the chain in order, threading each result forward.

    Parameters
    ----------
    chain:
        The pipeline to run.
    executor:
        ``(stage: ChainStage, task: str, previous_result: Any | None) -> Any``
        — the host's spawn/run primitive. Called once per stage with the
        rendered task (previous result substituted where referenced).
    on_stage_done:
        Optional observer ``(stage, result)`` for progress/observability.

    Returns the list of stage results in order. A stage failure raises through
    the executor (the host decides retry/abort semantics); results so far are
    lost unless the host captured them via ``on_stage_done``.
    """
    errors = chain.validate()
    if errors:
        raise ValueError("invalid sequential chain: " + "; ".join(errors))

    results: list[Any] = []
    previous_result: Any = None
    for index, stage in enumerate(chain.stages):
        rendered = stage.render_task(
            str(previous_result) if index > 0 else _FIRST_RESULT
        )
        result = await executor(stage, rendered, previous_result)
        results.append(result)
        if on_stage_done is not None:
            on_stage_done(stage, result)
        previous_result = result
    return results


__all__ = [
    "ChainStage",
    "SequentialChain",
    "run_sequential",
]
