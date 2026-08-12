"""Filesystem Skill Provider for one resolved Agent Plugin installation."""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

from core.plugins.domain import (
    PluginComponentKind,
    PluginComponentStatus,
    ResolvedPlugin,
)
from core.skills.catalog import (
    MAX_CATALOG_ENTRIES,
    SKILL_FILENAME,
    SkillCatalog,
    SkillValidationProfile,
    read_skill_candidate,
)
from core.skills.metadata import OPENAI_METADATA_PATH
from core.skills.models import (
    SKILL_MAIN_RESOURCE,
    SkillAuthority,
    SkillOrigin,
    SkillOriginKind,
    SkillPackageId,
    SkillProviderKind,
    SkillRecord,
    SkillReference,
    SkillResolutionError,
    SkillScope,
    SkillSourceRoot,
    SkillStatus,
)
from core.skills.provider import (
    SkillListQuery,
    SkillReadRequest,
    SkillReadResult,
    SkillSearchMatch,
    SkillSearchRequest,
    SkillSearchResult,
)
from core.skills.resources import read_local_resource, search_local_resources


class PluginSkillProvider:
    """Expose direct child Skills from one Agent Plugins 1.0 package."""

    def __init__(
        self,
        plugin: ResolvedPlugin,
        *,
        installation_id: str,
        workspace: Path,
    ) -> None:
        self.plugin = plugin
        self.installation_id = installation_id
        self.workspace = workspace.resolve(strict=True)
        self.authority = SkillAuthority(SkillProviderKind.CUSTOM, installation_id)
        self._lock = threading.RLock()
        self._signature: tuple[Any, ...] | None = None
        self._catalog: SkillCatalog | None = None

    def list(self, query: SkillListQuery) -> SkillCatalog:
        signature = self._filesystem_signature()
        with self._lock:
            if (
                not query.force
                and self._catalog is not None
                and signature == self._signature
            ):
                return self._catalog
            catalog = self._discover()
            self._signature = signature
            self._catalog = catalog
            return catalog

    def read(self, request: SkillReadRequest) -> SkillReadResult:
        self._validate_authority(request.authority)
        record = self.list(SkillListQuery()).get_package(
            request.authority,
            request.package,
        )
        if record is None:
            raise SkillResolutionError(
                f"Plugin Skill not found: {request.package.value}"
            )
        contents, resource_revision = read_local_resource(
            record,
            request.resource,
            workspace=self.workspace,
            trust_boundary=self.plugin.root,
        )
        package_revision = record.revision
        if request.resource == record.main_resource:
            loaded = self._decorate(
                self._read_candidate(Path(record.directory)),
                request.package,
            )
            if loaded.status is SkillStatus.INVALID:
                raise SkillResolutionError(
                    loaded.error or "Plugin Skill became invalid during read"
                )
            if loaded.id != record.id:
                raise SkillResolutionError("Plugin Skill identity changed during read")
            contents = loaded.require_instructions()
            package_revision = loaded.revision
        return SkillReadResult(
            reference=request.reference,
            contents=contents,
            package_revision=package_revision,
            resource_revision=resource_revision,
        )

    def search(self, request: SkillSearchRequest) -> SkillSearchResult:
        self._validate_authority(request.authority)
        query = request.query.strip().casefold()
        if not query:
            return SkillSearchResult()
        catalog = self.list(SkillListQuery())
        if request.package is not None:
            record = catalog.get_package(request.authority, request.package)
            if record is None:
                raise SkillResolutionError(
                    f"Plugin Skill not found: {request.package.value}"
                )
            return SkillSearchResult(
                matches=search_local_resources(
                    record,
                    request.query,
                    workspace=self.workspace,
                    trust_boundary=self.plugin.root,
                    limit=request.limit,
                )
            )

        matches: list[SkillSearchMatch] = []
        for record in catalog.records:
            interface = record.metadata.interface
            searchable = "\n".join(
                value
                for value in (
                    record.name,
                    record.description,
                    interface.display_name,
                    interface.short_description,
                    self.plugin.name,
                )
                if value
            ).casefold()
            if query in searchable:
                matches.append(
                    SkillSearchMatch(
                        reference=record.provider_reference,
                        title=record.name,
                        snippet=record.description,
                    )
                )
                if len(matches) >= request.limit:
                    break
        return SkillSearchResult(matches=tuple(matches))

    def invalidate(self) -> None:
        with self._lock:
            self._signature = None

    def skill_change_token(self) -> object:
        return self._filesystem_signature()

    def _discover(self) -> SkillCatalog:
        records: list[SkillRecord] = []
        warnings: list[str] = []
        for directory in self._skill_directories():
            if len(records) >= MAX_CATALOG_ENTRIES:
                warnings.append(
                    f"Plugin {self.plugin.name} Skill catalog is limited to "
                    f"{MAX_CATALOG_ENTRIES} entries"
                )
                break
            package = SkillPackageId(directory.relative_to(self.plugin.root).as_posix())
            record = self._decorate(self._read_candidate(directory), package)
            if record.status is SkillStatus.INVALID:
                warnings.append(
                    f"Plugin {self.plugin.name} skipped invalid Skill "
                    f"{directory.name!r}: {record.error or 'validation failed'}"
                )
                continue
            records.append(
                replace(record, status=SkillStatus.ACTIVE).without_instructions()
            )
        return SkillCatalog(tuple(records), warnings=tuple(warnings))

    def _skill_directories(self) -> tuple[Path, ...]:
        component = self.plugin.package.component(PluginComponentKind.SKILLS)
        if (
            component is None
            or component.status is not PluginComponentStatus.READY
            or component.resource is None
        ):
            return ()
        root = self.plugin.root / component.resource.value
        try:
            children = sorted(root.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            return ()
        directories: list[Path] = []
        for child in children:
            try:
                if not child.is_dir():
                    continue
                skill_file = (child / SKILL_FILENAME).resolve(strict=True)
                skill_file.relative_to(self.plugin.root)
                if skill_file.is_file():
                    directories.append(child)
            except (OSError, ValueError):
                continue
        return tuple(directories)

    def _read_candidate(self, directory: Path) -> SkillRecord:
        return read_skill_candidate(
            root=directory.parent,
            directory=directory,
            scope=SkillScope.USER,
            source_root=SkillSourceRoot.AGENTS,
            trust_boundary=self.plugin.root,
            writable=False,
            validation_profile=SkillValidationProfile.AGENT_SKILLS_V1,
        )

    def _decorate(
        self,
        record: SkillRecord,
        package: SkillPackageId,
    ) -> SkillRecord:
        return replace(
            record,
            reference=SkillReference(
                authority=self.authority,
                package=package,
                resource=SKILL_MAIN_RESOURCE,
            ),
            origin=SkillOrigin(
                kind=SkillOriginKind.PROVIDER,
                label=self.plugin.name,
                location=f"plugin/{self.installation_id}/{package.value}",
            ),
        )

    def _validate_authority(self, authority: SkillAuthority) -> None:
        if authority != self.authority:
            raise SkillResolutionError(
                f"Plugin {self.plugin.name} does not own authority "
                f"{authority.kind.value}:{authority.provider_id}"
            )

    def _filesystem_signature(self) -> tuple[Any, ...]:
        values: list[Any] = [self.plugin.package.manifest_revision]
        component = self.plugin.package.component(PluginComponentKind.SKILLS)
        if component is not None and component.resource is not None:
            values.append(_stat_signature(self.plugin.root / component.resource.value))
        for directory in self._skill_directories()[:MAX_CATALOG_ENTRIES]:
            values.append(_stat_signature(directory / SKILL_FILENAME))
            values.append(_stat_signature(directory / OPENAI_METADATA_PATH))
        return tuple(values)


def _stat_signature(path: Path) -> tuple[str, int | None, int | None]:
    try:
        stat = path.stat()
        return (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return (str(path), None, None)


__all__ = ["PluginSkillProvider"]
