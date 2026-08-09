from __future__ import annotations

from pathlib import Path

import pytest

from core.harness.skills import SkillTool
from core.skills.capabilities import SkillCapabilityResolver, SkillCapabilityStatus
from core.skills.catalog import LocalSkillProvider
from core.skills.models import (
    SkillInvocationKind,
    SkillResolutionError,
    SkillSelection,
)
from core.skills.provider import SkillListQuery, SkillReadRequest
from core.skills.runtime import SkillRuntime


def _write_skill(
    root: Path,
    name: str,
    *,
    body: str,
    allowed_tools: str = "",
    metadata: str | None = None,
) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    allowed = f"allowed-tools: {allowed_tools}\n" if allowed_tools else ""
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} workflow\n{allowed}---\n{body}\n",
        encoding="utf-8",
    )
    if metadata is not None:
        agents = directory / "agents"
        agents.mkdir()
        (agents / "openai.yaml").write_text(metadata, encoding="utf-8")
    return directory


def test_catalog_is_metadata_only_and_main_content_is_read_lazily(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_skill(
        workspace / ".agents" / "skills",
        "review",
        body="PINNED-INSTRUCTIONS",
    )
    provider = LocalSkillProvider(
        workspace,
        include_user=False,
        include_system=False,
    )
    entry = provider.list(SkillListQuery()).get_active("review")

    assert entry is not None
    assert entry.instructions is None
    result = provider.read(SkillReadRequest.from_reference(entry.provider_reference))
    assert result.contents == "PINNED-INSTRUCTIONS"
    assert result.package_revision == entry.revision


@pytest.mark.asyncio
async def test_skill_tool_reads_and_searches_bounded_package_resources(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    directory = _write_skill(
        workspace / ".agents" / "skills",
        "research",
        body="Use the bundled protocol.",
    )
    (directory / "references").mkdir()
    (directory / "references" / "protocol.md").write_text(
        "The evidence protocol requires independent verification.",
        encoding="utf-8",
    )
    runtime = SkillRuntime(workspace, include_user=False, include_system=False)
    tool = SkillTool(runtime)

    assert "independent verification" in await tool.execute(
        name="research",
        resource="references/protocol.md",
    )
    search = await tool.execute(name="research", query="evidence protocol")
    assert "references/protocol.md" in search
    assert "evidence protocol" in search
    escaped = await tool.execute(name="research", resource="../secret.txt")
    assert escaped.startswith("Error:")


def test_turn_expands_skill_dependencies_before_selected_skill(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = workspace / ".agents" / "skills"
    _write_skill(root, "foundation", body="FOUNDATION")
    _write_skill(
        root,
        "review",
        body="REVIEW",
        allowed_tools="read",
        metadata=(
            "dependencies:\n"
            "  tools:\n"
            "    - type: tool\n"
            "      value: read\n"
            "  skills:\n"
            "    - foundation\n"
        ),
    )
    runtime = SkillRuntime(workspace, include_user=False, include_system=False)
    review = runtime.catalog().get_active("review")
    assert review is not None

    context, token = runtime.begin_turn(
        "Review",
        (SkillSelection(review.id),),
        available_tools=("read", "write", "skill"),
    )
    try:
        assert tuple(record.name for record in context.snapshot.records) == (
            "foundation",
            "review",
        )
        assert tuple(item.kind for item in context.snapshot.invocations) == (
            SkillInvocationKind.DEPENDENCY,
            SkillInvocationKind.EXPLICIT,
        )
        assert runtime.allowed_tool_names() == frozenset(("read", "skill"))
        assert "FOUNDATION" in context.snapshot.injected_instructions
        assert "REVIEW" in context.snapshot.injected_instructions
    finally:
        runtime.end_turn(token)


def test_missing_tool_dependency_and_allowed_tools_conflict_fail_closed(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = workspace / ".agents" / "skills"
    metadata = "dependencies:\n  tools:\n    - type: tool\n      value: read\n"
    _write_skill(root, "missing", body="MISSING", metadata=metadata)
    _write_skill(
        root,
        "conflict",
        body="CONFLICT",
        allowed_tools="grep",
        metadata=metadata,
    )
    runtime = SkillRuntime(workspace, include_user=False, include_system=False)
    missing = runtime.catalog().get_active("missing")
    conflict = runtime.catalog().get_active("conflict")
    assert missing is not None and conflict is not None

    with pytest.raises(SkillResolutionError, match="tool capability is unavailable"):
        runtime.begin_turn(
            "run",
            (SkillSelection(missing.id),),
            available_tools=("grep", "skill"),
        )
    with pytest.raises(SkillResolutionError, match="excluded by allowed-tools"):
        runtime.begin_turn(
            "run",
            (SkillSelection(conflict.id),),
            available_tools=("read", "grep", "skill"),
        )


def test_missing_and_cyclic_skill_dependencies_fail_deterministically(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = workspace / ".agents" / "skills"
    _write_skill(
        root,
        "missing",
        body="MISSING",
        metadata="dependencies:\n  skills: [not-installed]\n",
    )
    _write_skill(
        root,
        "alpha",
        body="ALPHA",
        metadata="dependencies:\n  skills: [bravo]\n",
    )
    _write_skill(
        root,
        "bravo",
        body="BRAVO",
        metadata="dependencies:\n  skills: [alpha]\n",
    )
    runtime = SkillRuntime(workspace, include_user=False, include_system=False)
    missing = runtime.catalog().get_active("missing")
    alpha = runtime.catalog().get_active("alpha")
    assert missing is not None and alpha is not None

    with pytest.raises(SkillResolutionError, match="not-installed"):
        runtime.begin_turn("run", (SkillSelection(missing.id),))
    with pytest.raises(
        SkillResolutionError,
        match=r"Skill dependency cycle: alpha -> bravo -> alpha",
    ):
        runtime.begin_turn("run", (SkillSelection(alpha.id),))


def test_mcp_dependency_matches_registered_server_tools(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_skill(
        workspace / ".agents" / "skills",
        "browser-research",
        body="BROWSE",
        allowed_tools="mcp_browser_open",
        metadata=("dependencies:\n  tools:\n    - type: mcp\n      value: browser\n"),
    )
    runtime = SkillRuntime(workspace, include_user=False, include_system=False)
    record = runtime.catalog().get_active("browser-research")
    assert record is not None

    context, token = runtime.begin_turn(
        "browse",
        (SkillSelection(record.id),),
        available_tools=("mcp_browser_open", "skill"),
    )
    try:
        assert context.snapshot.records[0].name == "browser-research"
    finally:
        runtime.end_turn(token)


def test_codex_compatible_cli_dependency_checks_the_executable_path(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_skill(
        workspace / ".agents" / "skills",
        "github",
        body="USE GH",
        metadata=("dependencies:\n  tools:\n    - type: cli\n      value: gh\n"),
    )
    catalog = SkillRuntime(
        workspace,
        include_user=False,
        include_system=False,
    ).catalog()
    record = catalog.get_active("github")
    assert record is not None

    unavailable = SkillCapabilityResolver(
        catalog,
        command_lookup=lambda _name: None,
    ).report(record)
    ready = SkillCapabilityResolver(
        catalog,
        command_lookup=lambda name: "/usr/bin/gh" if name == "gh" else None,
    ).report(record)

    assert unavailable.status is SkillCapabilityStatus.UNAVAILABLE
    assert ready.status is SkillCapabilityStatus.READY
