from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from core.application.workflow_adapter import (
    DefaultWorkflowRunner,
    WorkflowCallbacks,
    WorkflowExecutionRequest,
)
from workflows import agent_orchestration_engine as engine
from workflows.planning_runtime import validate_plan_text


@pytest.mark.asyncio
async def test_legacy_planner_fallback_is_available_but_strict_mode_rejects_it(
    tmp_path, monkeypatch
) -> None:
    freeform = "Detailed planner analysis without the required YAML sections. " * 20

    async def generate(*_args, **_kwargs):
        return SimpleNamespace(
            final_content=freeform,
            stop_reason="stop",
            tools_used=[],
            usage={},
            error=None,
        )

    monkeypatch.setattr(engine, "_generate_plan_with_single_agent", generate)
    monkeypatch.setattr(
        engine,
        "_load_paper_markdown_content",
        lambda *_args: (str(tmp_path / "paper.md"), freeform),
    )

    compatible = await engine.run_code_analyzer(
        str(tmp_path),
        logging.getLogger("compat-planner"),
        use_segmentation=False,
    )
    assert validate_plan_text(compatible)["valid"] is True

    with pytest.raises(RuntimeError, match="without producing a usable plan"):
        await engine.run_code_analyzer(
            str(tmp_path),
            logging.getLogger("strict-planner"),
            use_segmentation=False,
            strict_plan_validation=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_type", "function_name"),
    [
        ("local", "execute_multi_agent_research_pipeline"),
        ("requirement", "execute_chat_based_planning_pipeline"),
    ],
)
async def test_desktop_workflow_adapter_always_requests_strict_outcomes(
    tmp_path, monkeypatch, source_type, function_name
) -> None:
    captured = {}

    async def run_pipeline(*_args, **kwargs):
        captured.update(kwargs)
        return {
            "status": "completed",
            "summary": "verified",
            "paper_dir": str(tmp_path),
        }

    monkeypatch.setattr(engine, function_name, run_pipeline)

    async def progress(*_args):
        return None

    async def interact(_request):
        return {"decision": "approve"}

    request = WorkflowExecutionRequest(
        run_id="run-1",
        kind="paper2code",
        source_type=source_type,
        source=str(tmp_path / "paper.pdf")
        if source_type == "local"
        else "Build a verified implementation",
        options={"planReview": False},
        workspace=tmp_path,
    )
    outcome = await DefaultWorkflowRunner().run(
        request,
        WorkflowCallbacks(progress=progress, interact=interact),
    )

    assert outcome.status == "completed"
    assert captured["strict_outcomes"] is True
