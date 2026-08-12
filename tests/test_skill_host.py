from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from core.skills.catalog import SkillCatalog, discover_skill_catalog
from core.skills.host import SkillCatalogHost, SkillWorkspaceRegistry
from core.skills.monitor import SkillCatalogMonitor
from core.skills.models import (
    SkillAuthority,
    SkillPackageId,
    SkillProviderKind,
    SkillReference,
)
from core.skills.provider import (
    SkillListQuery,
    SkillProviderSource,
    SkillReadRequest,
    SkillReadResult,
    SkillSearchRequest,
    SkillSearchResult,
)


def _write_skill(root: Path, name: str, body: str = "Follow the workflow.") -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A reusable workflow\n---\n{body}\n",
        encoding="utf-8",
    )
    return directory


def test_registry_reuses_host_but_turn_runtimes_remain_isolated(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_skill(workspace / ".agents" / "skills", "review")
    registry = SkillWorkspaceRegistry()

    try:
        first_host = registry.get(workspace)
        second_host = registry.get(workspace / ".")
        first_runtime = registry.new_runtime(workspace)
        second_runtime = registry.new_runtime(workspace)

        assert first_host is second_host
        assert first_runtime is not second_runtime
        assert first_runtime.skill_providers is second_runtime.skill_providers

        _context, token = first_runtime.begin_turn("$review")
        try:
            assert first_runtime.current_context() is not None
            assert second_runtime.current_context() is None
        finally:
            first_runtime.end_turn(token)
    finally:
        registry.close()


def test_monitor_invalidates_shared_catalog_without_mutating_active_turn(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill_directory = _write_skill(
        workspace / ".agents" / "skills",
        "review",
        "Use the first workflow.",
    )
    host = SkillCatalogHost(
        workspace,
        include_user=False,
        include_system=False,
    )
    runtime = host.new_runtime()
    context, turn_token = runtime.begin_turn("$review")
    changes: list[Path] = []
    monitor = SkillCatalogMonitor(changes.append, interval_seconds=60)
    monitor.register(host)
    try:
        (skill_directory / "SKILL.md").write_text(
            "---\nname: review\ndescription: A reusable workflow\n---\n"
            "Use the second workflow.\n",
            encoding="utf-8",
        )

        assert monitor.poll_once() == (workspace,)
        assert changes == [workspace]
        assert context.snapshot.records[0].require_instructions() == (
            "Use the first workflow."
        )

        next_context, next_token = runtime.begin_turn("$review")
        try:
            assert next_context.snapshot.records[0].require_instructions() == (
                "Use the second workflow."
            )
        finally:
            runtime.end_turn(next_token)
    finally:
        monitor.close()
        runtime.end_turn(turn_token)


def test_registry_publishes_one_workspace_change(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill_directory = _write_skill(workspace / ".agents" / "skills", "review")
    monitors: list[SkillCatalogMonitor] = []

    def monitor_factory(callback):
        monitor = SkillCatalogMonitor(callback, interval_seconds=60)
        monitors.append(monitor)
        return monitor

    registry = SkillWorkspaceRegistry(monitor_factory=monitor_factory)
    changes: list[Path] = []
    subscription = registry.subscribe(changes.append)
    try:
        registry.get(workspace)
        (skill_directory / "SKILL.md").write_text(
            "---\nname: review\ndescription: Updated workflow\n---\nRun it.\n",
            encoding="utf-8",
        )

        assert monitors[0].poll_once() == (workspace,)
        assert changes == [workspace]
    finally:
        registry.unsubscribe(subscription)
        registry.close()


class _ContributedProvider:
    def __init__(
        self, source_workspace: Path, provider_id: str = "plugin:test"
    ) -> None:
        base = discover_skill_catalog(
            source_workspace,
            include_user=False,
            include_system=False,
        ).get_active("plugin-review")
        assert base is not None
        self.authority = SkillAuthority(SkillProviderKind.CUSTOM, provider_id)
        self.record = replace(
            base,
            reference=SkillReference(
                self.authority,
                SkillPackageId("plugin-review"),
                base.main_resource,
            ),
            origin=None,
        )

    def list(self, query: SkillListQuery) -> SkillCatalog:
        del query
        return SkillCatalog((self.record,))

    def read(self, request: SkillReadRequest) -> SkillReadResult:
        assert request.reference == self.record.provider_reference
        return SkillReadResult(
            reference=request.reference,
            contents="PLUGIN-CONTRIBUTED-INSTRUCTIONS",
            package_revision=self.record.revision,
            resource_revision=self.record.revision,
        )

    def search(self, request: SkillSearchRequest) -> SkillSearchResult:
        del request
        return SkillSearchResult()


def test_contributed_source_is_visible_to_existing_runtimes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source_workspace = tmp_path / "plugin-source"
    workspace.mkdir()
    source_workspace.mkdir()
    _write_skill(workspace / ".agents" / "skills", "local-review")
    _write_skill(source_workspace / ".deepcode" / "skills", "plugin-review")
    host = SkillCatalogHost(
        workspace,
        include_user=False,
        include_system=False,
    )
    runtime = host.new_runtime()
    provider = _ContributedProvider(source_workspace)
    source = SkillProviderSource.for_authority(
        provider.authority,
        provider,
        label="test-plugin",
    )

    host.set_contributed_sources((source,))
    contributed = runtime.catalog(force=True).get_package(
        provider.authority,
        SkillPackageId("plugin-review"),
    )
    assert contributed is not None
    assert runtime.read(contributed.id).require_instructions() == (
        "PLUGIN-CONTRIBUTED-INSTRUCTIONS"
    )

    host.set_contributed_sources(())
    assert (
        runtime.catalog(force=True).get_package(
            provider.authority,
            SkillPackageId("plugin-review"),
        )
        is None
    )


def test_registry_contribution_seam_publishes_once(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source_workspace = tmp_path / "plugin-source"
    workspace.mkdir()
    source_workspace.mkdir()
    _write_skill(source_workspace / ".deepcode" / "skills", "plugin-review")
    monitors: list[SkillCatalogMonitor] = []

    def host_factory(path: Path) -> SkillCatalogHost:
        return SkillCatalogHost(
            path,
            include_user=False,
            include_system=False,
        )

    def monitor_factory(callback):
        monitor = SkillCatalogMonitor(callback, interval_seconds=60)
        monitors.append(monitor)
        return monitor

    registry = SkillWorkspaceRegistry(
        host_factory,
        monitor_factory=monitor_factory,
    )
    runtime = registry.new_runtime(workspace)
    provider = _ContributedProvider(source_workspace)
    source = SkillProviderSource.for_authority(provider.authority, provider)
    changes: list[Path] = []
    subscription = registry.subscribe(changes.append)
    try:
        registry.set_contributed_sources(workspace, (source,))

        assert changes == [workspace]
        assert monitors[0].poll_once() == ()
        assert (
            runtime.catalog().get_package(
                provider.authority,
                SkillPackageId("plugin-review"),
            )
            is not None
        )
    finally:
        registry.unsubscribe(subscription)
        registry.close()


def test_registry_keeps_contributors_independent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    first_source = tmp_path / "first-source"
    second_source = tmp_path / "second-source"
    for path in (workspace, first_source, second_source):
        path.mkdir()
    _write_skill(first_source / ".deepcode" / "skills", "plugin-review")
    _write_skill(second_source / ".deepcode" / "skills", "plugin-review")
    first = _ContributedProvider(first_source, "first")
    second = _ContributedProvider(second_source, "second")
    registry = SkillWorkspaceRegistry(
        lambda path: SkillCatalogHost(
            path,
            include_user=False,
            include_system=False,
        )
    )
    runtime = registry.new_runtime(workspace)
    try:
        registry.register_contributor(
            "first-owner",
            lambda _workspace: (
                SkillProviderSource.for_authority(first.authority, first),
            ),
        )
        registry.register_contributor(
            "second-owner",
            lambda _workspace: (
                SkillProviderSource.for_authority(second.authority, second),
            ),
        )
        catalog = runtime.catalog(force=True)
        assert catalog.get_package(first.authority, first.record.package_id) is not None
        assert (
            catalog.get_package(second.authority, second.record.package_id) is not None
        )

        assert registry.unregister_contributor("first-owner") is True
        catalog = runtime.catalog(force=True)
        assert catalog.get_package(first.authority, first.record.package_id) is None
        assert (
            catalog.get_package(second.authority, second.record.package_id) is not None
        )
        assert registry.unregister_contributor("missing-owner") is False
    finally:
        registry.close()
