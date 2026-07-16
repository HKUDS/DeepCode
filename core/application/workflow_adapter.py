"""Frontend-neutral boundary around the legacy Paper2Code execution kernel."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from core.domain.common import JsonObject


WorkflowOutcomeStatus = Literal["completed", "incomplete", "cancelled"]
ProgressCallback = Callable[[str, int, int, str, JsonObject], Awaitable[None]]
InteractionCallback = Callable[[JsonObject], Awaitable[JsonObject]]


@dataclass(frozen=True, slots=True)
class WorkflowExecutionRequest:
    run_id: str
    kind: str
    source_type: str
    source: str
    options: JsonObject
    workspace: Path
    checkpoint: JsonObject = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ArtifactSpec:
    kind: str
    name: str
    media_type: str
    path: Path
    metadata: JsonObject = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkflowOutcome:
    status: WorkflowOutcomeStatus
    summary: str
    result: JsonObject = field(default_factory=dict)
    artifacts: tuple[ArtifactSpec, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkflowCallbacks:
    progress: ProgressCallback
    interact: InteractionCallback


class WorkflowRunner(Protocol):
    async def run(
        self,
        request: WorkflowExecutionRequest,
        callbacks: WorkflowCallbacks,
    ) -> WorkflowOutcome: ...


class DefaultWorkflowRunner:
    """Lazy adapter; importing workflow/Docling code never slows App Server startup."""

    async def run(
        self,
        request: WorkflowExecutionRequest,
        callbacks: WorkflowCallbacks,
    ) -> WorkflowOutcome:
        from workflows.agent_orchestration_engine import (
            execute_chat_based_planning_pipeline,
            execute_multi_agent_research_pipeline,
        )
        from workflows.plan_review_runtime import PlanReviewCancelled

        workflow_root = request.workspace / ".deepcode" / "workflows"
        task_id = str(request.checkpoint.get("taskId") or request.run_id)
        await callbacks.progress(
            "preparing",
            1,
            100,
            "Preparing isolated workflow workspace",
            {
                "taskId": task_id,
                "workflowRoot": str(workflow_root),
            },
        )

        progress_tail: asyncio.Task[None] | None = None

        def report(percent: int, message: str, *_extra: Any) -> None:
            nonlocal progress_tail
            stage = _stage_for_progress(percent)
            previous = progress_tail

            async def persist() -> None:
                if previous is not None:
                    await previous
                await callbacks.progress(
                    stage,
                    max(0, min(int(percent), 100)),
                    100,
                    str(message),
                    {"taskId": task_id, "workflowRoot": str(workflow_root)},
                )

            progress_tail = asyncio.create_task(persist())

        logger = logging.getLogger(f"deepcode.workflow.{request.run_id}")
        source = _resume_source(request, workflow_root, task_id)
        try:
            if request.source_type in {"local", "url"}:
                raw_result = await execute_multi_agent_research_pipeline(
                    source,
                    logger,
                    progress_callback=report,
                    enable_indexing=bool(request.options.get("enableIndexing", False)),
                    task_id=task_id,
                    plan_review_callback=(
                        callbacks.interact
                        if bool(request.options.get("planReview", True))
                        else None
                    ),
                    workflow_root=workflow_root,
                    strict_outcomes=True,
                )
            else:
                requirement = request.source
                if request.source_type == "repository":
                    requirement = (
                        "Analyze and improve or reproduce the following repository. "
                        f"Use it as the authoritative source: {request.source}"
                    )
                raw_result = await execute_chat_based_planning_pipeline(
                    requirement,
                    logger,
                    progress_callback=report,
                    enable_indexing=bool(request.options.get("enableIndexing", False)),
                    task_id=task_id,
                    plan_review_callback=(
                        callbacks.interact
                        if bool(request.options.get("planReview", True))
                        else None
                    ),
                    workflow_root=workflow_root,
                    return_details=True,
                    strict_outcomes=True,
                )
        except PlanReviewCancelled as exc:
            return WorkflowOutcome(status="cancelled", summary=str(exc))
        finally:
            if progress_tail is not None:
                await progress_tail

        normalized = _normalize_result(raw_result, workflow_root, task_id)
        status = str(normalized.get("status") or "error")
        completed = status == "completed"
        summary = str(
            normalized.get("summary") or normalized.get("message") or raw_result
        )
        artifacts = _artifact_specs(normalized, workflow_root, task_id)
        return WorkflowOutcome(
            status="completed" if completed else "incomplete",
            summary=summary[:4_000],
            result=_json_result(normalized),
            artifacts=artifacts,
        )


def _stage_for_progress(percent: int) -> str:
    if percent < 25:
        return "preparing"
    if percent < 40:
        return "acquiring_input"
    if percent < 50:
        return "extracting_document"
    if percent < 65:
        return "segmenting_document"
    if percent < 70:
        return "planning"
    if percent < 80:
        return "collecting_references"
    if percent < 85:
        return "indexing"
    if percent < 100:
        return "implementing"
    return "finalizing"


def _resume_source(
    request: WorkflowExecutionRequest, workflow_root: Path, task_id: str
) -> str:
    if not request.checkpoint:
        return request.source
    prefix = "paper" if request.source_type in {"local", "url"} else "chat"
    task_dir = workflow_root / "tasks" / f"{prefix}_{task_id}"
    for name in ("paper.md", "paper.pdf", "paper.docx", "paper.txt", "paper.html"):
        candidate = task_dir / name
        if candidate.is_file():
            return str(candidate)
    return request.source


def _normalize_result(
    raw_result: Any, workflow_root: Path, task_id: str
) -> dict[str, Any]:
    if isinstance(raw_result, dict):
        return dict(raw_result)
    task_dirs = [
        workflow_root / "tasks" / f"paper_{task_id}",
        workflow_root / "tasks" / f"chat_{task_id}",
    ]
    task_dir = next((path for path in task_dirs if path.is_dir()), task_dirs[0])
    return {
        # Legacy string results carry no machine-verifiable completion signal.  Treat
        # them as incomplete instead of inferring success from human-facing prose.
        "status": "incomplete",
        "summary": str(raw_result),
        "paper_dir": str(task_dir),
        "code_directory": str(task_dir / "generate_code"),
    }


def _artifact_specs(
    result: dict[str, Any], workflow_root: Path, task_id: str
) -> tuple[ArtifactSpec, ...]:
    candidates: list[ArtifactSpec] = []
    task_value = result.get("paper_dir") or result.get("taskDirectory")
    task_dir = (
        Path(str(task_value))
        if task_value
        else workflow_root / "tasks" / f"paper_{task_id}"
    )
    known = (
        ("source", "paper.md", "text/markdown"),
        ("source", "paper.markdown", "text/markdown"),
        ("source", "paper.pdf", "application/pdf"),
        (
            "source",
            "paper.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        ("source", "paper.txt", "text/plain"),
        ("source", "paper.html", "text/html"),
        ("source", "paper.htm", "text/html"),
        ("plan", "initial_plan.txt", "text/plain"),
        ("report", "code_implementation_report.txt", "text/plain"),
        ("report", "codebase_index_report.txt", "text/plain"),
    )
    for kind, name, media_type in known:
        path = task_dir / name
        if path.is_file():
            candidates.append(ArtifactSpec(kind, name, media_type, path))
    code_value = result.get("code_directory")
    if not code_value and isinstance(result.get("implementation"), dict):
        code_value = result["implementation"].get("code_directory")
    code_dir = Path(str(code_value)) if code_value else task_dir / "generate_code"
    if code_dir.is_dir():
        candidates.append(
            ArtifactSpec(
                "codebase",
                "Generated code",
                "inode/directory",
                code_dir,
            )
        )
    return tuple(candidates)


def _json_result(result: dict[str, Any]) -> JsonObject:
    return {
        str(key): clean_value
        for key, value in result.items()
        if (clean_value := _json_value(value)) is not _UNSUPPORTED
    }


_UNSUPPORTED = object()


def _json_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return _json_result(value)
    if isinstance(value, (list, tuple)):
        return [
            clean
            for item in value[:200]
            if (clean := _json_value(item)) is not _UNSUPPORTED
        ]
    return _UNSUPPORTED
