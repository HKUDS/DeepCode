"""Per-child sub-agent composition: persona, tool allowlist, output schema.

The dsh capabilities under test (``SubagentCapabilities``: persona,
toolFilter, outputSchema), in DeepCode form:

- persona and the capture contract ride the system prompt as ADDITIONAL
  sections — the base prompt survives;
- a tool allowlist only narrows, and can never lock out the mandatory
  capture tool;
- ``output_schema`` forces a conforming ``submit_result`` submission; a
  delegation that never submits is a failure, not prose standing in;
- every capability is rejected loud on external backends, and a malformed
  schema is rejected at spawn — never accepted-then-ignored.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_setup import _compose_tool_filters
from core.harness.agents.control import AgentControl, AgentLimitError
from core.harness.agents.structured_result import (
    CAPTURE_TOOL_NAME,
    SchemaError,
    StructuredResultCapture,
    validate_output_schema,
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict"],
}


# -- schema validation ---------------------------------------------------------


@pytest.mark.parametrize(
    "schema",
    [None, {}, [], {"type": "array"}, {"type": "object"}, {"properties": {}}],
    ids=["none", "empty", "list", "array-rooted", "no-properties", "empty-props"],
)
def test_unsupported_schemas_fail_loud(schema) -> None:
    with pytest.raises(SchemaError):
        validate_output_schema(schema)


def test_object_root_is_defaulted_not_guessed() -> None:
    validated = validate_output_schema({"properties": {"x": {"type": "string"}}})
    assert validated["type"] == "object"


# -- capture tool --------------------------------------------------------------


def test_capture_tool_parameters_are_the_schema() -> None:
    capture = StructuredResultCapture(_SCHEMA)
    tool = capture.make_tool()
    assert tool.name == CAPTURE_TOOL_NAME
    assert tool.parameters["properties"]["verdict"]["enum"] == ["pass", "fail"]
    # Registry validation is what enforces conformance; the declared schema
    # must therefore reject a bad submission through the ordinary path.
    assert tool.validate_params({"verdict": "maybe"})
    assert tool.validate_params({"issues": []})  # missing required verdict
    assert tool.validate_params({"verdict": "pass"}) == []


def test_last_submission_wins_and_renders_canonically() -> None:
    capture = StructuredResultCapture(_SCHEMA)
    tool = capture.make_tool()
    asyncio.run(tool.execute(verdict="fail", issues=["a"]))
    asyncio.run(tool.execute(verdict="pass", issues=[]))
    assert capture.captured == {"verdict": "pass", "issues": []}
    assert capture.render() == '{"issues": [], "verdict": "pass"}'


def test_no_submission_renders_none() -> None:
    assert StructuredResultCapture(_SCHEMA).render() is None


# -- composed tool filters -----------------------------------------------------


def test_compose_tool_filters_chains_narrowing() -> None:
    allow_read = lambda names: tuple(n for n in names if "read" in n)  # noqa: E731
    drop_web = lambda names: tuple(n for n in names if n != "read_web")  # noqa: E731
    chained = _compose_tool_filters(allow_read, drop_web)
    assert chained(("read_file", "read_web", "bash")) == ("read_file",)


def test_compose_tool_filters_collapses_trivial_cases() -> None:
    assert _compose_tool_filters(None, None) is None
    only = lambda names: names  # noqa: E731
    assert _compose_tool_filters(None, only) is only


# -- spawn capability rules ----------------------------------------------------


def _control(tmp_path: Path) -> AgentControl:
    return AgentControl(str(tmp_path))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"persona": "reviewer"},
        {"tools": ["read_file"]},
        {"output_schema": _SCHEMA},
    ],
    ids=["persona", "tools", "output-schema"],
)
def test_external_backends_reject_composition(tmp_path: Path, kwargs) -> None:
    with pytest.raises(AgentLimitError, match="does not support"):
        _control(tmp_path).spawn("t", name="x", backend="codex", **kwargs)


def test_empty_tool_allowlist_fails_loud(tmp_path: Path) -> None:
    with pytest.raises(AgentLimitError, match="at least one tool"):
        _control(tmp_path).spawn("t", name="x", tools=["  ", ""])


def test_malformed_schema_fails_at_spawn(tmp_path: Path) -> None:
    with pytest.raises(AgentLimitError, match="object-rooted"):
        _control(tmp_path).spawn("t", name="x", output_schema={"type": "array"})


def test_spawn_records_composition_on_the_subagent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = _control(tmp_path)

    async def fake_run(sub, workspace):  # noqa: ANN001 - test double
        return "done"

    monkeypatch.setattr(control, "_run_subagent", fake_run)

    async def scenario() -> None:
        agent_id = control.spawn(
            "review the diff",
            name="rev",
            isolate=False,
            persona="You are a security reviewer.",
            tools=["read_file", "grep"],
            output_schema=_SCHEMA,
        )
        sub = control.get(agent_id)
        assert sub is not None
        assert sub.persona == "You are a security reviewer."
        assert sub.tool_names == ("read_file", "grep")
        assert sub.output_schema is not None
        await control.cancel_running()

    asyncio.run(scenario())


# -- end-to-end through a scripted child session -------------------------------


def test_structured_delegation_returns_the_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The native child pipeline: capture tool registered, prompt extended,
    result is the submitted JSON — via a scripted build_agent_session."""

    captured_kwargs: dict = {}

    class _ScriptedSession:
        def __init__(self, extra_tools):
            self._extra_tools = extra_tools

        def load_history(self, _messages):
            raise AssertionError("no seed history expected")

        async def run_stream(self, _op):
            # The "model" performs one conforming submission, then finishes.
            for tool in self._extra_tools:
                await tool.execute(verdict="pass", issues=["none found"])

            class _Msg:
                type = "task_complete"
                final_text = "prose that must NOT be the result"

            class _Event:
                msg = _Msg()

            yield _Event()

        async def aclose(self):
            pass

    def scripted_build(**kwargs):
        captured_kwargs.update(kwargs)
        return _ScriptedSession(kwargs.get("extra_tools", ())), "m", object()

    monkeypatch.setattr("core.agent_setup.build_agent_session", scripted_build)

    async def scenario() -> str:
        control = _control(tmp_path)
        agent_id = control.spawn(
            "verify the module",
            name="verify",
            isolate=False,
            persona="You are a verifier.",
            tools=["read_file"],
            output_schema=_SCHEMA,
        )
        sub = control.get(agent_id)
        assert sub is not None
        await sub.settled.wait()
        result = sub.result
        await control.cancel_running()
        return result

    result = asyncio.run(scenario())
    assert result == '{"issues": ["none found"], "verdict": "pass"}'

    # Composition reached the session builder.
    prompt = captured_kwargs["system_prompt"]
    assert "## Persona" in prompt and "You are a verifier." in prompt
    assert CAPTURE_TOOL_NAME in prompt
    names = captured_kwargs["tool_filter"](("read_file", "bash", CAPTURE_TOOL_NAME))
    assert names == ("read_file", CAPTURE_TOOL_NAME)


def test_structured_delegation_without_submission_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _SilentSession:
        def load_history(self, _messages):
            pass

        async def run_stream(self, _op):
            class _Msg:
                type = "task_complete"
                final_text = "I think it looks fine"

            class _Event:
                msg = _Msg()

            yield _Event()

        async def aclose(self):
            pass

    monkeypatch.setattr(
        "core.agent_setup.build_agent_session",
        lambda **kwargs: (_SilentSession(), "m", object()),
    )

    async def scenario() -> tuple[str, str]:
        control = _control(tmp_path)
        agent_id = control.spawn(
            "verify", name="v", isolate=False, output_schema=_SCHEMA
        )
        sub = control.get(agent_id)
        assert sub is not None and sub.handle is not None
        await sub.handle
        return sub.status, sub.result

    status, result = asyncio.run(scenario())
    assert status == "failed"
    assert "without submitting a structured result" in result
    assert "I think it looks fine" in result


def test_spawn_tool_rejects_unknown_tool_names(tmp_path: Path) -> None:
    """Verified live: an allowlist naming a nonexistent tool silently strips
    the child of capabilities and it invents answers. The spawn tool now
    validates names against the parent's own vocabulary."""
    from core.harness.tools.spawn_agent import SpawnAgentTool

    tool = SpawnAgentTool(_control(tmp_path), known_tools=("read", "grep", "bash"))
    out = asyncio.run(tool.execute(name="x", task="t", tools=["read_file", "grep"]))
    assert "Error: unknown tool name(s) read_file" in out
    assert "Available: bash, grep, read" in out
