"""Tests for action fusion (``run_steps``)."""

from __future__ import annotations

import pytest

from core.agent_runtime.tools.base import ToolResult
from core.harness.tools.run_steps import (
    CONTRACT_ERROR,
    CONTRACT_OK,
    CONTRACT_REFUSED,
    CONTRACT_STEP_FAILED,
    DEFAULT_MAX_STEPS,
    FusionOutcome,
    RunStepsTool,
    StepFusion,
    StepOutcome,
    StepRequest,
    parse_steps,
)

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _ok(text: str = "done", code: int = 0) -> ToolResult:
    return ToolResult(text, is_error=code != 0, metadata={"exit_code": code})


class _Recorder:
    """Executor stub that records what it was asked to run."""

    def __init__(self, results=None):
        self.seen: list[tuple[str, int | None]] = []
        self._results = results or {}

    async def __call__(self, command: str, timeout_s: int | None):
        self.seen.append((command, timeout_s))
        result = self._results.get(command)
        if callable(result):
            return result()
        return result if result is not None else _ok()


def _fusion(recorder: _Recorder, **kwargs) -> StepFusion:
    return StepFusion(recorder, **kwargs)


def _run(recorder: _Recorder, steps, **kwargs):
    import asyncio

    run_kwargs = kwargs.pop("run_kwargs", {})
    return asyncio.run(_fusion(recorder, **kwargs).run(steps, **run_kwargs))


def _steps(*commands: str) -> tuple[StepRequest, ...]:
    return tuple(StepRequest(command=command) for command in commands)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def test_parse_accepts_bare_strings():
    steps = parse_steps(["pytest -q", "ruff check"])
    assert [step.command for step in steps] == ["pytest -q", "ruff check"]
    assert steps[0].label == ""


def test_parse_accepts_objects_with_labels_and_timeouts():
    steps = parse_steps([{"command": "make", "label": "build", "timeout_s": 30}])
    assert steps[0].label == "build"
    assert steps[0].timeout_s == 30
    assert steps[0].display == "build"


def test_parse_rejects_non_list():
    with pytest.raises(ValueError, match="steps must be a list"):
        parse_steps("pytest")


def test_parse_rejects_empty():
    with pytest.raises(ValueError, match="must not be empty"):
        parse_steps([])


def test_parse_rejects_too_many_steps():
    with pytest.raises(ValueError, match="at most"):
        parse_steps(["echo"] * (DEFAULT_MAX_STEPS + 1))


def test_parse_rejects_unknown_keys():
    """Undeclared keys are refused rather than dropped: they may be an attack."""
    with pytest.raises(ValueError, match="unsupported keys: force"):
        parse_steps([{"command": "rm -rf /", "force": True}])


def test_parse_rejects_missing_command():
    with pytest.raises(ValueError, match="needs a string 'command'"):
        parse_steps([{"label": "oops"}])


def test_parse_rejects_bad_entry_type():
    with pytest.raises(ValueError, match="must be a string or an object"):
        parse_steps([42])


def test_parse_rejects_bad_label_type():
    with pytest.raises(ValueError, match="'label' must be a string"):
        parse_steps([{"command": "ls", "label": 3}])


def test_parse_rejects_bad_timeout_type():
    with pytest.raises(ValueError, match="'timeout_s' must be an integer"):
        parse_steps([{"command": "ls", "timeout_s": "30"}])


def test_step_request_rejects_empty_command_and_bad_timeout():
    with pytest.raises(ValueError, match="must not be empty"):
        StepRequest(command="   ")
    with pytest.raises(ValueError, match="timeout_s must be > 0"):
        StepRequest(command="ls", timeout_s=0)


def test_parse_accepts_exactly_the_cap():
    assert len(parse_steps(["echo"] * DEFAULT_MAX_STEPS)) == DEFAULT_MAX_STEPS


def test_max_steps_is_enforced_by_the_tool_configuration():
    with pytest.raises(ValueError, match="max_steps must be > 0"):
        StepFusion(lambda *_: None, max_steps=0)


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------


def test_successful_sequence_runs_every_step_in_order():
    recorder = _Recorder()
    outcome = _run(recorder, _steps("one", "two", "three"))
    assert outcome.contract == CONTRACT_OK
    assert outcome.ok
    assert outcome.stopped_at is None
    assert [step.index for step in outcome.steps] == [1, 2, 3]
    assert recorder.seen == [("one", None), ("two", None), ("three", None)]
    assert all(step.exit_code == 0 for step in outcome.steps)
    assert all(not step.skipped for step in outcome.steps)


def test_step_timeout_is_forwarded():
    recorder = _Recorder()
    steps = (StepRequest(command="slow", timeout_s=7),)
    _run(recorder, steps)
    assert recorder.seen == [("slow", 7)]


def test_failure_stops_the_sequence_by_default():
    recorder = _Recorder({"two": lambda: _ok("boom", code=1)})
    outcome = _run(recorder, _steps("one", "two", "three"))
    assert outcome.contract == CONTRACT_STEP_FAILED
    assert outcome.stopped_at == 2
    assert outcome.steps[1].exit_code == 1
    assert outcome.steps[2].skipped
    # The step after the failure was never dispatched.
    assert recorder.seen == [("one", None), ("two", None)]


def test_continue_on_error_runs_the_rest():
    recorder = _Recorder({"two": lambda: _ok("boom", code=1)})
    outcome = _run(
        recorder, _steps("one", "two", "three"), run_kwargs={"stop_on_error": False}
    )
    assert outcome.contract == CONTRACT_OK
    assert outcome.stopped_at is None
    assert recorder.seen == [("one", None), ("two", None), ("three", None)]
    # The failure is still attributed even though the sequence continued.
    assert outcome.failed_step is not None
    assert outcome.failed_step.index == 2


def test_refusal_is_attributed_without_an_exit_code():
    refusal = ToolResult("Error: command blocked by policy screen (x)", is_error=True)
    recorder = _Recorder({"bad": lambda: refusal})
    outcome = _run(recorder, _steps("bad"))
    step = outcome.steps[0]
    assert step.exit_code is None
    assert step.is_error
    assert not step.ok
    assert outcome.contract == CONTRACT_REFUSED


def test_refusal_stops_even_when_continue_on_error_is_set():
    """Continuing past a policy refusal would walk a blocked workflow in."""
    refusal = ToolResult("Error: command blocked by policy screen (x)", is_error=True)
    recorder = _Recorder({"bad": lambda: refusal})
    outcome = _run(
        recorder, _steps("bad", "harmless"), run_kwargs={"stop_on_error": False}
    )
    assert outcome.contract == CONTRACT_REFUSED
    assert outcome.stopped_at == 1
    assert outcome.steps[1].skipped
    assert recorder.seen == [("bad", None)]


def test_plain_string_result_is_not_claimed_as_success():
    """A string carries no status, so the fusion must not invent one."""
    recorder = _Recorder({"x": lambda: "just text"})
    outcome = _run(recorder, _steps("x"))
    assert outcome.steps[0].exit_code is None
    assert not outcome.steps[0].is_error


def test_none_result_is_a_harness_fault_not_a_refusal():
    """Nothing came back at all: uninterpretable, and never a policy decision."""
    recorder = _Recorder({"x": lambda: None})
    outcome = _run(recorder, _steps("x"))
    assert outcome.steps[0].is_error
    assert outcome.contract == CONTRACT_ERROR


def test_bash_error_string_is_read_as_a_refusal_not_a_success():
    """The bash tool reports pre-execution refusals as plain "Error: ..." text."""
    blocked = "Error: command blocked by policy screen (destructive rm). Use a waiver."
    recorder = _Recorder({"rm -rf /": lambda: blocked})
    outcome = _run(recorder, _steps("rm -rf /", "echo next"))
    assert outcome.steps[0].is_error
    assert not outcome.steps[0].ok
    assert outcome.contract == CONTRACT_REFUSED
    assert outcome.steps[1].skipped
    assert recorder.seen == [("rm -rf /", None)]


def test_non_error_string_is_not_a_failure():
    recorder = _Recorder({"x": lambda: "plain output"})
    outcome = _run(recorder, _steps("x"))
    assert not outcome.steps[0].is_error
    assert outcome.contract == CONTRACT_OK


def test_unrunnable_error_stops_even_without_stop_on_error():
    recorder = _Recorder({"x": lambda: "Error: command timed out after 5s: x"})
    outcome = _run(recorder, _steps("x", "y"), run_kwargs={"stop_on_error": False})
    assert outcome.contract == CONTRACT_REFUSED
    assert outcome.steps[1].skipped


def test_executor_exception_becomes_an_attributed_error():
    async def boom(command: str, timeout_s):
        raise RuntimeError("nope")

    import asyncio

    outcome = asyncio.run(StepFusion(boom).run(_steps("x", "y")))
    assert outcome.contract == CONTRACT_ERROR
    assert outcome.stopped_at == 1
    assert "RuntimeError: nope" in outcome.steps[0].output
    assert outcome.steps[1].skipped


def test_nonzero_exit_without_error_flag_still_counts_as_failure():
    recorder = _Recorder(
        {"x": lambda: ToolResult("out", is_error=False, metadata={"exit_code": 2})}
    )
    outcome = _run(recorder, _steps("x"))
    assert outcome.steps[0].is_error is False
    assert not outcome.steps[0].ok
    assert outcome.contract == CONTRACT_STEP_FAILED


def test_exit_code_metadata_is_read_defensively():
    recorder = _Recorder({"x": lambda: ToolResult("out", metadata={"exit_code": "1"})})
    outcome = _run(recorder, _steps("x"))
    assert outcome.steps[0].exit_code is None


# --------------------------------------------------------------------------
# Bounds
# --------------------------------------------------------------------------


def test_per_step_output_is_truncated_and_flagged():
    recorder = _Recorder({"x": lambda: _ok("z" * 5_000)})
    outcome = _run(recorder, _steps("x"), max_step_chars=100)
    step = outcome.steps[0]
    assert len(step.output) == 100
    assert step.truncated


def test_total_output_budget_is_shared_across_steps():
    recorder = _Recorder({f"s{i}": lambda: _ok("z" * 1_000) for i in range(4)})
    outcome = _run(
        recorder,
        _steps("s0", "s1", "s2", "s3"),
        max_total_chars=250,
        max_step_chars=1_000,
    )
    assert outcome.output_chars <= 250
    # Every step keeps some evidence: the budget is shared, not first-come.
    assert all(step.output for step in outcome.steps)
    assert all(step.truncated for step in outcome.steps)


def test_small_output_is_not_flagged_as_truncated():
    recorder = _Recorder()
    outcome = _run(recorder, _steps("x"), max_step_chars=100)
    assert not outcome.steps[0].truncated


def test_render_is_bounded():
    recorder = _Recorder({f"s{i}": lambda: _ok("y" * 2_000) for i in range(3)})
    outcome = _run(
        recorder, _steps("s0", "s1", "s2"), max_total_chars=600, max_step_chars=600
    )
    assert len(outcome.render(max_chars=600)) <= 600 + len(
        "\n...[run_steps output truncated]"
    )


def test_invalid_bounds_are_rejected():
    with pytest.raises(ValueError, match="max_total_chars must be > 0"):
        StepFusion(lambda *_: None, max_total_chars=0)
    with pytest.raises(ValueError, match="max_step_chars must be > 0"):
        StepFusion(lambda *_: None, max_step_chars=0)


# --------------------------------------------------------------------------
# Rendering and contract
# --------------------------------------------------------------------------


def test_render_attributes_every_step():
    recorder = _Recorder({"two": lambda: _ok("boom", code=1)})
    outcome = _run(recorder, _steps("one", "two", "three"))
    text = outcome.render()
    assert "contract=step_failed" in text
    assert "stopped at step 2" in text
    assert "[exit 0] one" in text
    assert "[exit 1] two" in text
    assert "[skipped] three" in text
    assert "boom" in text


def test_render_reports_executed_over_total():
    recorder = _Recorder()
    outcome = _run(recorder, _steps("a", "b"))
    assert "2/2 executed" in outcome.render()


def test_outcome_serialises_with_per_step_evidence():
    recorder = _Recorder({"b": lambda: _ok("boom", code=1)})
    outcome = _run(recorder, _steps("a", "b", "c"))
    payload = outcome.to_dict()
    assert payload["contract"] == CONTRACT_STEP_FAILED
    assert payload["stopped_at"] == 2
    assert payload["executed"] == 2
    assert payload["total_steps"] == 3
    assert payload["steps"][1]["exit_code"] == 1
    assert payload["steps"][2]["skipped"] is True
    assert "output_chars" in payload["steps"][0]


def test_step_outcome_ok_semantics():
    assert StepOutcome(1, "c", "", 0, False, False, "", False).ok
    assert not StepOutcome(1, "c", "", 1, False, False, "", False).ok
    assert not StepOutcome(1, "c", "", None, False, True, "", False).ok


def test_fusion_outcome_executed_counts_only_real_runs():
    outcome = FusionOutcome(
        steps=(
            StepOutcome(1, "a", "", 0, False, False, "", False),
            StepOutcome(2, "b", "", None, False, True, "", False),
        ),
        contract=CONTRACT_STEP_FAILED,
        stopped_at=1,
        output_chars=0,
    )
    assert outcome.executed == 1
    assert not outcome.ok


# --------------------------------------------------------------------------
# The tool
# --------------------------------------------------------------------------


class _StubBash:
    """Stands in for BashTool: records calls, returns scripted results."""

    def __init__(self, results=None):
        self.seen: list[dict] = []
        self._results = results or {}

    async def execute(self, **kwargs):
        self.seen.append(kwargs)
        command = kwargs.get("command")
        result = self._results.get(command)
        return result if result is not None else _ok()


def test_tool_reports_the_same_execution_path_as_bash():
    """Fusion must not own a second exec path: screening lives in one place."""
    bash = _StubBash()
    tool = RunStepsTool(bash)
    assert tool.uses(bash)
    assert not tool.uses(_StubBash())


def test_tool_is_exclusive_and_not_read_only():
    tool = RunStepsTool(_StubBash())
    assert tool.read_only is False
    assert tool.exclusive is True


def test_tool_description_and_schema_are_declared():
    tool = RunStepsTool(_StubBash())
    assert tool.name == "run_steps"
    assert "run" in tool.description.lower()
    schema = tool.parameters
    assert schema["required"] == ["steps"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["steps"]["maxItems"] == DEFAULT_MAX_STEPS
    assert "stop_on_error" in schema["properties"]


def test_tool_executes_a_sequence_end_to_end():
    import asyncio

    bash = _StubBash()
    result = asyncio.run(RunStepsTool(bash).execute(steps=["pytest -q", "ruff check"]))
    assert result.is_error is False
    assert result.metadata["contract"] == CONTRACT_OK
    assert result.metadata["executed"] == 2
    assert [call["command"] for call in bash.seen] == ["pytest -q", "ruff check"]


def test_tool_returns_an_error_result_for_bad_arguments():
    import asyncio

    result = asyncio.run(RunStepsTool(_StubBash()).execute(steps="pytest"))
    assert result.is_error
    assert result.metadata["contract"] == "invalid"
    assert "steps must be a list" in str(result)


def test_tool_surfaces_a_blocked_step_as_an_error_result():
    import asyncio

    blocked = ToolResult(
        "Error: command blocked by policy screen (destructive)", is_error=True
    )
    bash = _StubBash({"rm -rf /": blocked})
    result = asyncio.run(
        RunStepsTool(bash).execute(steps=["rm -rf /", "echo still-here"])
    )
    assert result.is_error
    assert result.metadata["contract"] == CONTRACT_REFUSED
    assert result.metadata["stopped_at"] == 1
    # The follow-up command never reached the executor.
    assert [call["command"] for call in bash.seen] == ["rm -rf /"]


def test_tool_coerces_a_non_boolean_stop_on_error():
    import asyncio

    bash = _StubBash()
    result = asyncio.run(RunStepsTool(bash).execute(steps=["a"], stop_on_error="yes"))
    assert result.metadata["contract"] == CONTRACT_OK


def test_tool_forwards_step_timeouts():
    import asyncio

    bash = _StubBash()
    asyncio.run(
        RunStepsTool(bash).execute(steps=[{"command": "slow", "timeout_s": 11}])
    )
    assert bash.seen[0]["timeout"] == 11


def test_tool_omits_timeout_when_not_declared():
    import asyncio

    bash = _StubBash()
    asyncio.run(RunStepsTool(bash).execute(steps=["a"]))
    assert "timeout" not in bash.seen[0]


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def test_run_steps_is_registered_and_shares_the_bash_instance(tmp_path):
    """The registered tool must delegate to the registered bash tool."""
    from core.harness.tools import default_coding_tools

    registry = default_coding_tools(str(tmp_path))
    names = (
        registry.tool_names
        if isinstance(registry.tool_names, list)
        else registry.tool_names()
    )
    assert "run_steps" in names
    bash = registry.get("bash")
    tool = registry.get("run_steps")
    assert isinstance(tool, RunStepsTool)
    assert tool.uses(bash)
