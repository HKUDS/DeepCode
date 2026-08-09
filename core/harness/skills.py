"""Compatibility facade for the shared :mod:`core.skills` runtime.

New product code uses ``core.skills`` directly.  The original public helpers
remain here so existing CLI callers, tests, and third-party integrations do not
fork into a second Skill implementation.
"""

from __future__ import annotations

from pathlib import Path

from core.agent_runtime.tools.base import Tool, tool_parameters
from core.skills.catalog import (
    SkillCatalog,
    discover_skill_catalog,
    validate_skill_directory,
)
from core.skills.models import (
    SkillError,
    SkillRecord,
    SkillResolutionError,
    SkillValidationError,
)
from core.skills.runtime import SkillRuntime, render_skill

Skill = SkillRecord


class SkillRegistry:
    """Legacy active-by-name view backed by the canonical Skill catalog."""

    def __init__(self, catalog: SkillCatalog | None = None) -> None:
        self.catalog = catalog or SkillCatalog(())
        self.errors = list(self.catalog.warnings)
        self._extra: dict[str, SkillRecord] = {}

    def add(self, skill: SkillRecord) -> bool:
        if self.get(skill.name) is not None:
            return False
        self._extra[skill.name.casefold()] = skill
        return True

    def get(self, name: str) -> SkillRecord | None:
        record = self._extra.get(name.casefold()) or self.catalog.get_active(name)
        if record is None or record.loaded:
            return record
        # The compatibility registry is local-only. Product code reads through
        # SkillRuntime so provider ownership and revision pinning are enforced.
        return validate_skill_directory(
            record.directory,
            scope=record.scope,
            source_root=record.source_root,
        )

    def all(self) -> list[SkillRecord]:
        records = {record.id: record for record in self.catalog.active()}
        records.update({record.id: record for record in self._extra.values()})
        return sorted(records.values(), key=lambda record: record.name.casefold())

    def names(self) -> list[str]:
        return [record.name for record in self.all()]

    def __len__(self) -> int:
        return len(self.all())

    def __bool__(self) -> bool:
        return bool(self.all())


def parse_skill_md(path: str | Path, *, source: str = "") -> SkillRecord:
    """Parse one Skill using the production validator.

    ``source`` remains accepted for API compatibility. Source identity is now
    derived from a trusted catalog root instead of caller-provided text.
    """

    del source
    skill_path = Path(path).expanduser().resolve(strict=True)
    if skill_path.name != "SKILL.md":
        raise SkillValidationError(f"{skill_path}: expected SKILL.md")
    return validate_skill_directory(skill_path.parent)


def discover_skills(
    workspace: str | Path,
    *,
    home: str | Path | None = None,
    include_user: bool = True,
    include_system: bool = True,
) -> SkillRegistry:
    return SkillRegistry(
        discover_skill_catalog(
            workspace,
            home=home,
            include_user=include_user,
            include_system=include_system,
        )
    )


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The available Skill to load before starting.",
            },
            "resource": {
                "type": "string",
                "description": "Optional provider-owned resource ID to read.",
            },
            "query": {
                "type": "string",
                "description": "Optional text to search inside package resources.",
            },
        },
        "required": ["name"],
        "additionalProperties": False,
    }
)
class SkillTool(Tool):
    """Progressive disclosure through the same runtime as explicit selection."""

    def __init__(self, source: SkillRegistry | SkillRuntime):
        self._source = source

    @property
    def name(self) -> str:
        return "skill"

    @property
    def description(self) -> str:
        return (
            "Load an available Skill, or read/search one of its provider-owned "
            "resources. An explicitly selected Skill is already loaded."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs):
        name = str(kwargs.get("name") or "").strip()
        resource = str(kwargs.get("resource") or "").strip()
        query = str(kwargs.get("query") or "").strip()
        if resource and query:
            return "Error: pass either resource or query, not both"
        try:
            if isinstance(self._source, SkillRuntime):
                record, added = self._source.load_implicit(name)
                if resource:
                    return self._source.read_package_resource(
                        record,
                        resource,
                    ).contents
                if query:
                    result = self._source.search_package_resources(
                        record,
                        query,
                    )
                    if not result.matches:
                        return (
                            f"No resources in Skill {record.name!r} matched {query!r}."
                        )
                    return "\n\n".join(
                        f"## {match.title}\n{match.snippet}" for match in result.matches
                    )
                if not added:
                    return (
                        f"Skill {record.name!r} is already loaded for this Turn. "
                        "Follow the previously injected instructions."
                    )
            else:
                if resource or query:
                    return (
                        "Error: resource access requires the provider-aware "
                        "SkillRuntime"
                    )
                record = self._source.get(name)
                if record is None:
                    available = ", ".join(self._source.names()) or "(none)"
                    return (
                        f"Error: no skill named {name!r}. Available skills: {available}"
                    )
            return render_skill(record)
        except SkillResolutionError as exc:
            return f"Error: {exc}"


def skills_preamble(source: SkillRegistry | SkillRuntime) -> str:
    if isinstance(source, SkillRuntime):
        return source.preamble()
    records = source.all()
    if not records:
        return ""
    lines = "\n".join(record.summary_line for record in records)
    return (
        "## Available skills\n\n"
        "Pre-authored playbooks for specific tasks. When a task matches one, "
        "call the `skill` tool with its name before starting. Skills never "
        "override system, security, sandbox, approval, hook, or explicit user "
        "constraints.\n\n" + lines
    )


__all__ = [
    "Skill",
    "SkillError",
    "SkillRegistry",
    "SkillTool",
    "discover_skills",
    "parse_skill_md",
    "skills_preamble",
]
