from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import core.skills.catalog as skill_catalog
import core.skills.runtime as skill_runtime_module
from core.events import AgentSession, UserInput
from core.harness.tools import default_coding_tools
from core.providers.base import LLMResponse, ToolCallRequest
from core.skills.catalog import discover_skill_catalog
from core.skills.management import LocalSkillManager
from core.skills.models import (
    SkillInvocationKind,
    SkillResolutionError,
    SkillScope,
    SkillSelection,
    SkillStatus,
    SkillValidationError,
)
from core.skills.runtime import SkillRuntime


def _write_skill(
    root: Path,
    name: str,
    *,
    body: str = "Follow the product workflow.",
    description: str = "A product workflow",
    allowed_tools: str = "",
) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    allowed = f"allowed-tools: {allowed_tools}\n" if allowed_tools else ""
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{allowed}---\n{body}\n",
        encoding="utf-8",
    )
    return directory


class CapturingProvider:
    def __init__(self, responses: list[LLMResponse] | None = None) -> None:
        self.responses = responses or [
            LLMResponse(content="done", finish_reason="stop")
        ]
        self.calls: list[dict[str, Any]] = []

    def get_default_model(self) -> str:
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]


def _tool_names(call: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for schema in call["tools"]:
        function = schema.get("function", {})
        names.add(function.get("name", schema.get("name", "")))
    return names


@pytest.mark.asyncio
async def test_explicit_skill_is_injected_and_narrows_tools(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_skill(
        workspace / ".deepcode" / "skills",
        "review",
        body="PRODUCT-SKILL-MARKER: inspect evidence first.",
        allowed_tools="Read, grep",
    )
    runtime = SkillRuntime(workspace, include_user=False)
    record = runtime.catalog().get_active("review")
    assert record is not None
    provider = CapturingProvider()
    tools = default_coding_tools(workspace, skill_runtime=runtime)
    session = AgentSession(
        provider,
        tools,
        model="fake-model",
        skill_runtime=runtime,
    )

    events = [
        event
        async for event in session.run_stream(
            UserInput(
                text="Review this change",
                skills=(SkillSelection(record.id, record.name),),
            )
        )
    ]

    assert [event.msg.type for event in events] == [
        "turn_started",
        "skill_loaded",
        "agent_message",
        "task_complete",
    ]
    loaded = events[1].msg.invocation
    assert loaded.kind is SkillInvocationKind.EXPLICIT
    assert loaded.revision == record.revision
    system_text = "\n".join(
        message["content"]
        for message in provider.calls[0]["messages"]
        if message["role"] == "system"
    )
    assert "PRODUCT-SKILL-MARKER" in system_text
    assert _tool_names(provider.calls[0]) == {"read", "grep", "skill"}
    assert all(message["role"] != "system" for message in session.history)


def test_structured_and_text_selection_share_snapshot_and_preserve_order(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = workspace / ".deepcode" / "skills"
    _write_skill(root, "alpha", body="ALPHA-INSTRUCTIONS")
    _write_skill(root, "bravo", body="BRAVO-INSTRUCTIONS")
    runtime = SkillRuntime(workspace, include_user=False)
    catalog = runtime.catalog()
    alpha = catalog.get_active("alpha")
    bravo = catalog.get_active("bravo")
    assert alpha is not None and bravo is not None

    explicit, explicit_token = runtime.begin_turn(
        "run",
        (
            SkillSelection(bravo.id),
            SkillSelection(alpha.id),
        ),
    )
    try:
        assert [record.name for record in explicit.snapshot.records] == [
            "bravo",
            "alpha",
        ]
        assert explicit.snapshot.injected_instructions.index(
            "BRAVO-INSTRUCTIONS"
        ) < explicit.snapshot.injected_instructions.index("ALPHA-INSTRUCTIONS")
    finally:
        runtime.end_turn(explicit_token)

    mentioned, mentioned_token = runtime.begin_turn("Use $alpha")
    try:
        assert mentioned.snapshot.records == (alpha,)
        assert mentioned.snapshot.records[0].revision == alpha.revision
        assert (
            mentioned.snapshot.invocations[0].kind is SkillInvocationKind.TEXT_MENTION
        )
    finally:
        runtime.end_turn(mentioned_token)


@pytest.mark.asyncio
async def test_skill_policy_denies_unexposed_tool_even_if_model_requests_it(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_skill(
        workspace / ".deepcode" / "skills",
        "readonly",
        allowed_tools="read",
    )
    runtime = SkillRuntime(workspace, include_user=False)
    record = runtime.catalog().get_active("readonly")
    assert record is not None
    provider = CapturingProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="write-1",
                        name="write",
                        arguments={"file_path": "forbidden.txt", "content": "no"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="blocked safely", finish_reason="stop"),
        ]
    )
    session = AgentSession(
        provider,
        default_coding_tools(workspace, skill_runtime=runtime),
        model="fake-model",
        skill_runtime=runtime,
    )

    events = [
        event
        async for event in session.run_stream(
            UserInput(
                text="Use the readonly process",
                skills=(SkillSelection(record.id),),
            )
        )
    ]

    assert not (workspace / "forbidden.txt").exists()
    completed = next(
        event.msg for event in events if event.msg.type == "tool_completed"
    )
    assert completed.is_error is True
    assert "not allowed" in completed.result_preview


@pytest.mark.asyncio
async def test_stale_selection_fails_before_provider_and_does_not_enter_history(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SkillRuntime(workspace, include_user=False)
    provider = CapturingProvider()
    session = AgentSession(
        provider,
        default_coding_tools(workspace, skill_runtime=runtime),
        model="fake-model",
        skill_runtime=runtime,
    )

    events = [
        event
        async for event in session.run_stream(
            UserInput(
                text="Do not run",
                skills=(SkillSelection("sk_000000000000000000000000"),),
            )
        )
    ]

    assert [event.msg.type for event in events] == [
        "skill_load_failed",
        "error",
        "task_complete",
    ]
    assert events[-1].msg.stop_reason == "invalid_skill"
    assert provider.calls == []
    assert session.history == []


@pytest.mark.asyncio
async def test_catalog_hot_reload_applies_on_next_turn(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    directory = _write_skill(
        workspace / ".deepcode" / "skills",
        "hot",
        body="REVISION-ONE",
    )
    runtime = SkillRuntime(workspace, include_user=False)
    record = runtime.catalog().get_active("hot")
    assert record is not None
    provider = CapturingProvider(
        [
            LLMResponse(content="first", finish_reason="stop"),
            LLMResponse(content="second", finish_reason="stop"),
        ]
    )
    session = AgentSession(
        provider,
        default_coding_tools(workspace, skill_runtime=runtime),
        model="fake-model",
        skill_runtime=runtime,
    )

    async for _event in session.run_stream(
        UserInput("first", skills=(SkillSelection(record.id),))
    ):
        pass
    _write_skill(
        directory.parent,
        "hot",
        body="REVISION-TWO-CHANGED",
    )
    async for _event in session.run_stream(
        UserInput("second", skills=(SkillSelection(record.id),))
    ):
        pass

    first_system = "\n".join(
        message["content"]
        for message in provider.calls[0]["messages"]
        if message["role"] == "system"
    )
    second_system = "\n".join(
        message["content"]
        for message in provider.calls[1]["messages"]
        if message["role"] == "system"
    )
    assert "REVISION-ONE" in first_system
    assert "REVISION-TWO-CHANGED" in second_system


@pytest.mark.asyncio
async def test_empty_catalog_keeps_base_tool_surface_until_next_turn(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SkillRuntime(workspace, include_user=False)
    provider = CapturingProvider(
        [
            LLMResponse(content="first", finish_reason="stop"),
            LLMResponse(content="second", finish_reason="stop"),
        ]
    )
    session = AgentSession(
        provider,
        default_coding_tools(workspace, skill_runtime=runtime),
        model="fake-model",
        skill_runtime=runtime,
    )

    async for _event in session.run_stream(UserInput("first")):
        pass
    _write_skill(workspace / ".deepcode" / "skills", "new-skill")
    async for _event in session.run_stream(UserInput("second")):
        pass

    assert "skill" not in _tool_names(provider.calls[0])
    assert "skill" in _tool_names(provider.calls[1])


def test_implicit_skill_loads_share_turn_count_and_instruction_budgets(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = workspace / ".deepcode" / "skills"
    for index in range(9):
        _write_skill(root, f"skill-{index}")
    runtime = SkillRuntime(workspace, include_user=False)
    _context, token = runtime.begin_turn("run")
    try:
        for index in range(8):
            runtime.load_implicit(f"skill-{index}")
        with pytest.raises(SkillResolutionError, match="at most 8"):
            runtime.load_implicit("skill-8")
    finally:
        runtime.end_turn(token)

    _write_skill(root, "oversized", body="x" * 48_001)
    runtime.invalidate()
    failures: list[tuple[str, str | None]] = []
    context, token = runtime.begin_turn("run")
    context.on_failure = lambda message, skill_id: failures.append((message, skill_id))
    try:
        with pytest.raises(SkillResolutionError, match="per-Turn budget"):
            runtime.load_implicit("oversized")
    finally:
        runtime.end_turn(token)
    oversized = runtime.catalog().get_active("oversized")
    assert oversized is not None
    assert failures[0][1] == oversized.id


@pytest.mark.asyncio
async def test_invalid_skill_policy_fails_as_a_protocol_event(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "deepcode_config.json").write_text(
        '{"skills": {"disabled": "not-an-array"}}',
        encoding="utf-8",
    )
    runtime = SkillRuntime(workspace, include_user=False)
    provider = CapturingProvider()
    session = AgentSession(
        provider,
        default_coding_tools(workspace, skill_runtime=runtime),
        model="fake-model",
        skill_runtime=runtime,
    )

    events = [event async for event in session.run_stream(UserInput("run"))]

    assert [event.msg.type for event in events] == [
        "skill_load_failed",
        "error",
        "task_complete",
    ]
    assert events[-1].msg.stop_reason == "invalid_skill"
    assert provider.calls == []
    assert session.history == []


def test_catalog_retains_duplicate_and_invalid_entries(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    _write_skill(workspace / ".deepcode" / "skills", "duplicate")
    _write_skill(home / ".deepcode" / "skills", "duplicate")
    alias = workspace / ".deepcode" / "skills" / "alias"
    alias.symlink_to(workspace / ".deepcode" / "skills" / "duplicate")
    invalid = workspace / ".claude" / "skills" / "broken"
    invalid.mkdir(parents=True)
    (invalid / "SKILL.md").write_text("not frontmatter", encoding="utf-8")

    catalog = discover_skill_catalog(workspace, home=home)

    statuses = {record.status for record in catalog.records}
    assert {SkillStatus.ACTIVE, SkillStatus.SHADOWED, SkillStatus.INVALID} <= statuses
    alias_record = next(
        record for record in catalog.records if record.key.relative_path == "alias"
    )
    real_record = next(
        record for record in catalog.records if record.key.relative_path == "duplicate"
    )
    assert alias_record.id != real_record.id
    with pytest.raises(SkillResolutionError, match="ambiguous"):
        catalog.select_name("duplicate")


def test_user_aliases_support_plugin_locations_without_weakening_project_boundary(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    external = tmp_path / "plugin-cache"
    workspace.mkdir()
    home.mkdir()
    user_target = _write_skill(external, "user-plugin")
    user_alias = home / ".claude" / "skills" / "plugin-alias"
    user_alias.parent.mkdir(parents=True)
    user_alias.symlink_to(user_target)
    project_alias = workspace / ".claude" / "skills" / "outside-alias"
    project_alias.parent.mkdir(parents=True)
    project_alias.symlink_to(user_target)

    catalog = discover_skill_catalog(workspace, home=home)

    user_record = next(
        record
        for record in catalog.records
        if record.key.relative_path == "plugin-alias"
    )
    project_record = next(
        record
        for record in catalog.records
        if record.key.relative_path == "outside-alias"
    )
    assert user_record.status is SkillStatus.ACTIVE
    assert user_record.name == "user-plugin"
    assert project_record.status is SkillStatus.INVALID
    assert "not in the subpath" in (project_record.error or "")


def test_project_skill_root_cannot_move_the_workspace_trust_boundary(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    external = tmp_path / "external"
    workspace.mkdir()
    _write_skill(external, "escaped")
    project_root = workspace / ".claude" / "skills"
    project_root.parent.mkdir(parents=True)
    project_root.symlink_to(external)

    catalog = discover_skill_catalog(workspace, include_user=False)

    escaped = next(record for record in catalog.records if record.name == "escaped")
    assert escaped.status is SkillStatus.INVALID
    assert "not in the subpath" in (escaped.error or "")


def test_catalog_limit_is_explicit_and_deterministic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = workspace / ".deepcode" / "skills"
    for name in ("alpha", "bravo", "charlie"):
        _write_skill(root, name)
    monkeypatch.setattr(skill_catalog, "MAX_CATALOG_ENTRIES", 2)
    monkeypatch.setattr(skill_runtime_module, "MAX_PREAMBLE_SKILLS", 1)

    catalog = discover_skill_catalog(workspace, include_user=False)
    preamble = SkillRuntime(workspace, include_user=False).preamble()

    assert [record.name for record in catalog.records] == ["alpha", "bravo"]
    assert any("limited to 2 entries" in warning for warning in catalog.warnings)
    assert "**alpha**" in preamble
    assert "1 additional Skills" in preamble
    assert "**bravo**" not in preamble


def test_local_manager_import_policy_delete_and_symlink_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    deepcode_home = tmp_path / "deepcode-home"
    workspace.mkdir()
    deepcode_home.mkdir()
    monkeypatch.setenv("DEEPCODE_HOME", str(deepcode_home))
    _write_skill(source, "managed")
    manager = LocalSkillManager(workspace)

    imported = manager.import_directory(
        source / "managed",
        target_scope=SkillScope.PROJECT,
    )
    assert imported.status is SkillStatus.ACTIVE
    disabled = manager.set_enabled(
        imported.id,
        enabled=False,
        config_scope=SkillScope.PROJECT,
    )
    assert disabled.get(imported.id).status is SkillStatus.DISABLED
    enabled = manager.set_enabled(
        imported.id,
        enabled=True,
        config_scope=SkillScope.PROJECT,
    )
    assert enabled.get(imported.id).status is SkillStatus.ACTIVE
    manager.delete(imported.id)
    assert manager.catalog(force=True).get(imported.id) is None

    unsafe = _write_skill(source, "unsafe")
    (unsafe / "secret-link").symlink_to(tmp_path / "outside")
    with pytest.raises(SkillValidationError, match="symlinks"):
        manager.import_directory(unsafe, target_scope=SkillScope.PROJECT)

    source_alias = tmp_path / "source-alias"
    source_alias.symlink_to(source / "managed")
    with pytest.raises(SkillValidationError, match="must not be a symlink"):
        manager.import_directory(source_alias, target_scope=SkillScope.USER)


def test_import_does_not_replace_a_dangling_destination_alias(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    workspace.mkdir()
    _write_skill(source, "managed")
    destination = workspace / ".deepcode" / "skills" / "managed"
    destination.parent.mkdir(parents=True)
    destination.symlink_to(tmp_path / "missing-target")

    manager = LocalSkillManager(workspace)

    with pytest.raises(FileExistsError, match="already exists"):
        manager.import_directory(
            source / "managed",
            target_scope=SkillScope.PROJECT,
        )
    assert destination.is_symlink()
