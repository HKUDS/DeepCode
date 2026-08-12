"""Agent Plugins Specification 1.0.0 manifest and component adapter."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core.plugins.domain import (
    PluginComponent,
    PluginComponentKind,
    PluginComponentStatus,
    PluginDiagnostic,
    PluginDiagnosticSeverity,
    PluginMetadata,
    PluginResourceReference,
    PluginValidationError,
)
from core.plugins.mcp import load_agent_plugin_mcp

AGENT_PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
PLUGIN_MANIFEST_RESOURCE = PluginResourceReference("plugin.json")
SKILLS_RESOURCE = PluginResourceReference("skills")
MCP_RESOURCE = PluginResourceReference("mcp.json")

_PLUGIN_NAME_RE = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
_MANIFEST_FIELDS = frozenset(
    {
        "$schema",
        "name",
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
        "extensions",
    }
)


class AgentPluginsV1Adapter:
    """Translate the one supported portable format into core domain models."""

    schema = AGENT_PLUGIN_SCHEMA

    def parse_metadata(
        self,
        raw: dict[str, Any],
    ) -> tuple[PluginMetadata, tuple[PluginDiagnostic, ...]]:
        schema = _required_string(raw, "$schema")
        if schema != self.schema:
            raise PluginValidationError(f"Unsupported Agent Plugins schema: {schema}")

        name = _required_string(raw, "name")
        if len(name) > 64 or not _PLUGIN_NAME_RE.fullmatch(name):
            raise PluginValidationError(
                "Agent Plugin name must be 1-64 lowercase letters, numbers, dots, "
                "or hyphens; start and end with an alphanumeric character; and "
                "contain neither '..' nor '--'"
            )

        version = _optional_string(raw, "version")
        description = _optional_string(raw, "description") or ""
        homepage = _optional_string(raw, "homepage")
        repository = _optional_string(raw, "repository")
        license_name = _optional_string(raw, "license")
        author_name = _author_name(raw)
        keywords = _keywords(raw)

        diagnostics = [
            PluginDiagnostic(
                code="agent_plugins.unknown_manifest_field",
                severity=PluginDiagnosticSeverity.WARNING,
                message=f"Ignored unknown Agent Plugins manifest field: {key}",
                resource=PLUGIN_MANIFEST_RESOURCE.value,
            )
            for key in sorted(raw.keys() - _MANIFEST_FIELDS)
        ]
        extensions = raw.get("extensions")
        extension_namespaces: tuple[str, ...] = ()
        if "extensions" in raw and not isinstance(extensions, dict):
            diagnostics.append(
                PluginDiagnostic(
                    code="agent_plugins.non_object_extensions",
                    severity=PluginDiagnosticSeverity.WARNING,
                    message="Ignored Agent Plugins extensions because it is not an object",
                    resource=PLUGIN_MANIFEST_RESOURCE.value,
                )
            )
        elif isinstance(extensions, dict):
            # Unimplemented namespaces are intentionally opaque. The Agent
            # Plugins spec forbids clients from validating their contents.
            extension_namespaces = tuple(sorted(str(key) for key in extensions))

        return (
            PluginMetadata(
                name=name,
                version=version,
                description=description,
                author_name=author_name,
                homepage=homepage,
                repository=repository,
                license=license_name,
                keywords=keywords,
                extension_namespaces=extension_namespaces,
            ),
            tuple(diagnostics),
        )

    def discover_components(self, root: Path) -> tuple[PluginComponent, ...]:
        components: list[PluginComponent] = []
        skills = _fixed_component(root, SKILLS_RESOURCE, expect_directory=True)
        if skills is not None:
            status, diagnostic, path = skills
            components.append(
                PluginComponent(
                    kind=PluginComponentKind.SKILLS,
                    status=status,
                    resource=(SKILLS_RESOURCE if path is not None else None),
                    item_count=(
                        _skill_candidate_count(path, root)
                        if path is not None and status is PluginComponentStatus.READY
                        else None
                    ),
                    diagnostics=((diagnostic,) if diagnostic is not None else ()),
                )
            )

        mcp = _fixed_component(root, MCP_RESOURCE, expect_directory=False)
        if mcp is not None:
            status, diagnostic, path = mcp
            if status is PluginComponentStatus.READY:
                parsed = load_agent_plugin_mcp(root, plugin_schema=self.schema)
                if parsed.fatal:
                    status = PluginComponentStatus.INVALID
                components.append(
                    PluginComponent(
                        kind=PluginComponentKind.MCP,
                        status=status,
                        resource=(MCP_RESOURCE if path is not None else None),
                        item_count=(len(parsed.servers) if not parsed.fatal else None),
                        revision=parsed.revision,
                        diagnostics=parsed.diagnostics,
                    )
                )
            else:
                components.append(
                    PluginComponent(
                        kind=PluginComponentKind.MCP,
                        status=status,
                        resource=(MCP_RESOURCE if path is not None else None),
                        diagnostics=((diagnostic,) if diagnostic is not None else ()),
                    )
                )
        return tuple(components)


def validate_plugin_name(value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) > 64
        or not _PLUGIN_NAME_RE.fullmatch(value)
    ):
        raise PluginValidationError("Invalid Agent Plugin name")


def _fixed_component(
    root: Path,
    resource: PluginResourceReference,
    *,
    expect_directory: bool,
) -> (
    tuple[
        PluginComponentStatus,
        PluginDiagnostic | None,
        Path | None,
    ]
    | None
):
    unresolved = root / resource.value
    if not unresolved.exists() and not unresolved.is_symlink():
        return None
    kind = "directory" if expect_directory else "regular file"
    try:
        resolved = unresolved.resolve(strict=True)
        resolved.relative_to(root)
        valid_kind = resolved.is_dir() if expect_directory else resolved.is_file()
        if not valid_kind:
            raise ValueError(f"not a {kind}")
    except (OSError, ValueError) as exc:
        component = (
            PluginComponentKind.SKILLS if expect_directory else PluginComponentKind.MCP
        )
        return (
            PluginComponentStatus.INVALID,
            PluginDiagnostic(
                code="agent_plugins.invalid_fixed_component",
                severity=PluginDiagnosticSeverity.ERROR,
                message=f"Invalid Agent Plugins {resource.value} component: {exc}",
                component=component,
                resource=resource.value,
            ),
            None,
        )
    return PluginComponentStatus.READY, None, unresolved


def _skill_candidate_count(skills: Path, root: Path) -> int:
    try:
        children = sorted(skills.iterdir(), key=lambda item: item.name.casefold())
    except OSError:
        return 0
    count = 0
    for child in children:
        skill_file = child / "SKILL.md"
        try:
            if not child.is_dir():
                continue
            resolved = skill_file.resolve(strict=True)
            resolved.relative_to(root)
            if resolved.is_file():
                count += 1
        except (OSError, ValueError):
            continue
    return count


def _required_string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise PluginValidationError(f"Plugin {key} must be a non-empty string")
    return value


def _optional_string(raw: dict[str, Any], key: str) -> str | None:
    if key not in raw:
        return None
    value = raw[key]
    if not isinstance(value, str):
        raise PluginValidationError(f"Agent Plugin {key} must be a string")
    return value


def _author_name(raw: dict[str, Any]) -> str | None:
    if "author" not in raw:
        return None
    author = raw["author"]
    if not isinstance(author, dict):
        raise PluginValidationError("Agent Plugin author must be an object")
    unknown = set(author) - {"name", "email", "url"}
    if unknown:
        raise PluginValidationError(
            f"Unknown Agent Plugin author field: {min(unknown)}"
        )
    for key, value in author.items():
        if not isinstance(value, str):
            raise PluginValidationError(f"Agent Plugin author {key} must be a string")
    return author.get("name")


def _keywords(raw: dict[str, Any]) -> tuple[str, ...]:
    if "keywords" not in raw:
        return ()
    value = raw["keywords"]
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PluginValidationError("Agent Plugin keywords must be a string array")
    return tuple(value)


__all__ = [
    "AGENT_PLUGIN_SCHEMA",
    "MCP_RESOURCE",
    "PLUGIN_MANIFEST_RESOURCE",
    "SKILLS_RESOURCE",
    "AgentPluginsV1Adapter",
    "validate_plugin_name",
]
