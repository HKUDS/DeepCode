from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from core.application.workflow_adapter import (
    DefaultWorkflowRunner,
    WorkflowCallbacks,
    WorkflowExecutionRequest,
)
from core.compat.runtime import DeepCodeRuntime, use_runtime
from core.config import ConfigError, DeepCodeConfig
from workflows import agent_orchestration_engine as engine
from workflows.planning_runtime import extract_yaml_candidate, validate_plan_text


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
    with use_runtime(
        _runtime_with_mcp_servers("code-implementation", "command-executor")
    ):
        outcome = await DefaultWorkflowRunner().run(
            request,
            WorkflowCallbacks(progress=progress, interact=interact),
        )

    assert outcome.status == "completed"
    assert captured["strict_outcomes"] is True


def _runtime_with_mcp_servers(*names: str) -> DeepCodeRuntime:
    servers = {name: {"command": "python", "args": ["-V"]} for name in names}
    return DeepCodeRuntime(
        DeepCodeConfig.model_validate({"tools": {"mcpServers": servers}})
    )


_ALWAYS = ["code-implementation", "command-executor"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_type", "options", "missing"),
    [
        ("local", {}, _ALWAYS),
        ("requirement", {}, _ALWAYS),
        ("requirement", {"enableIndexing": True}, [*_ALWAYS, "code-reference-indexer"]),
        (
            "url",
            {"enableIndexing": True},
            [
                *_ALWAYS,
                "code-reference-indexer",
                "fetch",
                "filesystem",
                "github-downloader",
            ],
        ),
    ],
)
async def test_workflow_refuses_to_start_without_the_mcp_servers_it_uses(
    tmp_path, monkeypatch, source_type, options, missing
) -> None:
    """Unconfigured servers were only logged deep in the run, whose agents
    then worked with no tools and wrote nothing."""
    started = []

    async def run_pipeline(*args, **_kwargs):
        started.append(args)
        return {"status": "completed", "summary": "", "paper_dir": str(tmp_path)}

    monkeypatch.setattr(engine, "execute_multi_agent_research_pipeline", run_pipeline)
    monkeypatch.setattr(engine, "execute_chat_based_planning_pipeline", run_pipeline)
    reported = []

    async def progress(*args):
        reported.append(args)

    async def interact(_request):
        return {"decision": "approve"}

    request = WorkflowExecutionRequest(
        run_id="run-1",
        kind="paper2code",
        source_type=source_type,
        source="https://example.com/paper.pdf" if source_type == "url" else "Build it",
        options=options,
        workspace=tmp_path,
    )
    with use_runtime(_runtime_with_mcp_servers()):
        with pytest.raises(ConfigError) as refused:
            await DefaultWorkflowRunner().run(
                request, WorkflowCallbacks(progress=progress, interact=interact)
            )

    message = str(refused.value)
    assert "tools" in message and "mcpServers" in message
    for name in missing:
        assert f'"{name}"' in message
    assert message.count('"command"') == len(missing)
    assert started == [] and reported == []

    # Configured, the same request runs.
    with use_runtime(_runtime_with_mcp_servers(*missing)):
        await DefaultWorkflowRunner().run(
            request, WorkflowCallbacks(progress=progress, interact=interact)
        )
    assert len(started) == 1


def test_the_chat_plan_format_passes_plan_validation() -> None:
    """Plan review validates chat plans too; the format the chat planner is
    told to write has to be one it accepts."""
    from prompts.code_prompts import CHAT_AGENT_PLANNING_PROMPT

    template = extract_yaml_candidate(CHAT_AGENT_PLANNING_PROMPT)
    validation = validate_plan_text(template)

    assert validation["yaml_valid"], validation
    assert validation["valid"], validation["missing_sections"]
