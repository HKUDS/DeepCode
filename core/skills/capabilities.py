"""Resolve Skill-to-Skill dependencies against the current Agent capabilities."""

from __future__ import annotations

import shlex
import shutil
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from core.skills.catalog import SkillCatalog
from core.skills.models import (
    SkillAuthority,
    SkillInvocationKind,
    SkillPackageId,
    SkillProviderKind,
    SkillRecord,
    SkillResolutionError,
    SkillToolDependency,
)


class SkillCapabilityStatus(StrEnum):
    READY = "ready"
    UNAVAILABLE = "unavailable"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class SkillCapabilityIssue:
    kind: str
    dependency: str
    message: str


@dataclass(frozen=True, slots=True)
class SkillCapabilityReport:
    status: SkillCapabilityStatus
    issues: tuple[SkillCapabilityIssue, ...] = ()

    @property
    def ready(self) -> bool:
        return self.status is SkillCapabilityStatus.READY


class SkillCapabilityResolver:
    """Validate capabilities and expand a deterministic dependency graph."""

    def __init__(
        self,
        catalog: SkillCatalog,
        *,
        available_tools: Iterable[str] | None = None,
        available_mcp_servers: (
            Mapping[str, Iterable[str]] | Iterable[str] | None
        ) = None,
        command_lookup: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.catalog = catalog
        self.available_tools = (
            frozenset(name.casefold() for name in available_tools)
            if available_tools is not None
            else None
        )
        self.available_mcp_servers = _normalize_mcp_capabilities(available_mcp_servers)
        self.command_lookup = command_lookup

    def report(self, record: SkillRecord) -> SkillCapabilityReport:
        issues: list[SkillCapabilityIssue] = []
        for dependency in record.metadata.dependencies.tools:
            issue = self._tool_issue(record, dependency)
            if issue is not None:
                issues.append(issue)
        for name in record.metadata.dependencies.skills:
            dependency = self.catalog.get_active(name)
            if dependency is None:
                issues.append(
                    SkillCapabilityIssue(
                        kind="skill",
                        dependency=name,
                        message=f"required Skill is unavailable: {name}",
                    )
                )
        return SkillCapabilityReport(
            status=(
                SkillCapabilityStatus.READY
                if not issues
                else (
                    SkillCapabilityStatus.BLOCKED
                    if any(issue.kind in {"policy", "unsupported"} for issue in issues)
                    else SkillCapabilityStatus.UNAVAILABLE
                )
            ),
            issues=tuple(issues),
        )

    def expand(
        self,
        roots: Iterable[tuple[SkillRecord, SkillInvocationKind]],
    ) -> tuple[tuple[SkillRecord, SkillInvocationKind], ...]:
        """Return dependencies before dependants, preserving root order."""

        roots = tuple(roots)
        root_kinds = {
            (record.authority, record.package_id): kind for record, kind in roots
        }
        ordered: list[tuple[SkillRecord, SkillInvocationKind]] = []
        complete: set[tuple[SkillAuthority, SkillPackageId]] = set()
        visiting: list[SkillRecord] = []
        visiting_ids: set[tuple[SkillAuthority, SkillPackageId]] = set()

        def visit(record: SkillRecord, kind: SkillInvocationKind) -> None:
            identity = (record.authority, record.package_id)
            if identity in complete:
                return
            if identity in visiting_ids:
                start = next(
                    index
                    for index, candidate in enumerate(visiting)
                    if (candidate.authority, candidate.package_id) == identity
                )
                cycle = " -> ".join(
                    candidate.name for candidate in (*visiting[start:], record)
                )
                raise SkillResolutionError(f"Skill dependency cycle: {cycle}")

            report = self.report(record)
            if not report.ready:
                details = "; ".join(issue.message for issue in report.issues)
                raise SkillResolutionError(
                    f"Skill {record.name!r} has unavailable dependencies: {details}"
                )
            visiting.append(record)
            visiting_ids.add(identity)
            for name in record.metadata.dependencies.skills:
                dependency = self.catalog.get_active(name)
                if dependency is None:  # Covered by report; keeps type narrowing clear.
                    raise SkillResolutionError(f"required Skill is unavailable: {name}")
                visit(
                    dependency,
                    root_kinds.get(
                        (dependency.authority, dependency.package_id),
                        SkillInvocationKind.DEPENDENCY,
                    ),
                )
            visiting.pop()
            visiting_ids.remove(identity)
            complete.add(identity)
            ordered.append((record, kind))

        for record, kind in roots:
            visit(record, kind)
        return tuple(ordered)

    def _tool_issue(
        self,
        record: SkillRecord,
        dependency: SkillToolDependency,
    ) -> SkillCapabilityIssue | None:
        dependency_type = dependency.type.casefold()
        value = dependency.value.casefold()
        allowed = frozenset(name.casefold() for name in record.allowed_tools)
        candidates: frozenset[str]
        if dependency_type == "tool":
            candidates = frozenset((value,))
            available = self.available_tools is None or value in self.available_tools
        elif dependency_type == "mcp":
            capability_keys = [value]
            if record.authority.kind is SkillProviderKind.CUSTOM:
                capability_keys.insert(
                    0,
                    f"{record.authority.provider_id.casefold()}:{value}",
                )
            if self.available_mcp_servers is not None:
                matched = tuple(
                    self.available_mcp_servers[key]
                    for key in capability_keys
                    if key in self.available_mcp_servers
                )
                candidates = frozenset(
                    tool for tool_names in matched for tool in tool_names
                )
                available = bool(matched)
            else:
                legacy_prefix = f"mcp_{value}_"
                canonical_prefix = f"mcp__{value}__"
                candidates = frozenset(
                    name
                    for name in (self.available_tools or allowed)
                    if name.startswith((legacy_prefix, canonical_prefix))
                )
                available = self.available_tools is None or bool(candidates)
        elif dependency_type in {"cli", "command"}:
            command = dependency.command or dependency.value
            try:
                executable = shlex.split(command)[0]
            except (ValueError, IndexError):
                executable = ""
            candidates = frozenset()
            available = bool(executable and self.command_lookup(executable))
        else:
            return SkillCapabilityIssue(
                kind="unsupported",
                dependency=f"{dependency.type}:{dependency.value}",
                message=(
                    "unsupported Skill dependency type: "
                    f"{dependency.type}:{dependency.value}"
                ),
            )

        if not available:
            return SkillCapabilityIssue(
                kind=dependency_type,
                dependency=dependency.value,
                message=(
                    f"required {dependency_type} capability is unavailable: "
                    f"{dependency.value}"
                ),
            )

        if allowed and dependency_type in {"tool", "mcp"}:
            usable = candidates & allowed
            if not usable:
                return SkillCapabilityIssue(
                    kind="policy",
                    dependency=dependency.value,
                    message=(
                        f"required {dependency_type} capability is excluded by "
                        f"allowed-tools: {dependency.value}"
                    ),
                )
        return None


def _normalize_mcp_capabilities(
    value: Mapping[str, Iterable[str]] | Iterable[str] | None,
) -> dict[str, frozenset[str]] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return {
            name.casefold(): frozenset(tool.casefold() for tool in tools)
            for name, tools in value.items()
        }
    return {name.casefold(): frozenset() for name in value}


__all__ = [
    "SkillCapabilityIssue",
    "SkillCapabilityReport",
    "SkillCapabilityResolver",
    "SkillCapabilityStatus",
]
