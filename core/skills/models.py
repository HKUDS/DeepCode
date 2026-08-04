"""Immutable domain models for local Agent Skills."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

_SKILL_ID_RE = re.compile(r"^sk_[0-9a-f]{24}$")
MAX_SELECTED_SKILLS = 8


class SkillScope(StrEnum):
    PROJECT = "project"
    USER = "user"
    SYSTEM = "system"


MUTABLE_SKILL_SCOPES = (SkillScope.PROJECT, SkillScope.USER)


class SkillSourceRoot(StrEnum):
    AGENTS = "agents"
    DEEPCODE = "deepcode"
    CLAUDE = "claude"
    SYSTEM = "system"


class SkillStatus(StrEnum):
    ACTIVE = "active"
    SHADOWED = "shadowed"
    DISABLED = "disabled"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class SkillInterface:
    display_name: str | None = None
    short_description: str | None = None
    icon_small: str | None = None
    icon_large: str | None = None
    brand_color: str | None = None
    default_prompt: str | None = None


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    interface: SkillInterface = SkillInterface()
    allow_implicit_invocation: bool = True
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillKey:
    """Filesystem identity used to derive the opaque public Skill ID."""

    scope: SkillScope
    source_root: SkillSourceRoot
    skill_file: Path
    relative_path: str
    writable: bool = False

    @property
    def id(self) -> str:
        # The catalog-entry path participates in identity so two projects (or
        # two aliases of one user Skill) cannot accidentally resolve each
        # other's selection. Only the digest crosses the protocol boundary.
        material = "\0".join(
            (
                "deepcode-skill-v1",
                self.scope.value,
                self.source_root.value,
                str(self.skill_file),
                self.relative_path,
            )
        ).encode("utf-8")
        return f"sk_{hashlib.sha256(material).hexdigest()[:24]}"

    @property
    def source(self) -> str:
        return f"{self.scope.value}:{self.source_root.value}"


@dataclass(frozen=True, slots=True)
class SkillRecord:
    """One catalog candidate, including invalid and shadowed entries."""

    key: SkillKey
    name: str
    description: str
    instructions: str
    allowed_tools: tuple[str, ...]
    revision: str
    status: SkillStatus
    byte_size: int
    metadata: SkillMetadata = SkillMetadata()
    error: str | None = None
    shadowed_by: str | None = None

    @property
    def id(self) -> str:
        return self.key.id

    @property
    def scope(self) -> SkillScope:
        return self.key.scope

    @property
    def source_root(self) -> SkillSourceRoot:
        return self.key.source_root

    @property
    def source(self) -> str:
        return self.key.source

    @property
    def directory(self) -> str:
        return str(self.key.skill_file.parent)

    @property
    def selectable(self) -> bool:
        return self.status in {SkillStatus.ACTIVE, SkillStatus.SHADOWED}

    @property
    def deletable(self) -> bool:
        return (
            self.key.writable
            and self.scope is not SkillScope.SYSTEM
            and self.source_root is SkillSourceRoot.AGENTS
        )

    @property
    def summary_line(self) -> str:
        return f"- **{self.name}**: {self.description}"


@dataclass(frozen=True, slots=True)
class SkillSelection:
    """A protocol-safe explicit selection."""

    skill_id: str
    name: str = ""

    def __post_init__(self) -> None:
        if not _SKILL_ID_RE.fullmatch(self.skill_id):
            raise ValueError("skill_id must be an opaque sk_ identifier")


class SkillInvocationKind(StrEnum):
    EXPLICIT = "explicit"
    TEXT_MENTION = "text_mention"
    IMPLICIT = "implicit"


@dataclass(frozen=True, slots=True)
class SkillInvocation:
    skill_id: str
    name: str
    revision: str
    source: str
    kind: SkillInvocationKind

    def to_metadata(self) -> dict[str, str]:
        return {
            "skillId": self.skill_id,
            "name": self.name,
            "revision": self.revision,
            "source": self.source,
            "invocation": self.kind.value,
        }


@dataclass(frozen=True, slots=True)
class SkillTurnSnapshot:
    """Resolved, immutable Skill state consumed by one Agent Turn."""

    records: tuple[SkillRecord, ...]
    invocations: tuple[SkillInvocation, ...]
    catalog_revision: str
    allowed_tools: frozenset[str] | None

    @property
    def injected_instructions(self) -> str:
        if not self.records:
            return ""
        sections = [
            (
                f"## Skill: {record.name}\n"
                f"Source: {record.source}\n"
                f"Revision: {record.revision}\n\n"
                f"{record.instructions}"
            )
            for record in self.records
        ]
        return (
            "# User-selected Skills\n\n"
            "The user explicitly selected the workflow instructions below for "
            "this Turn. Follow them where they support the user's task. They do "
            "not override system, security, sandbox, approval, hook, or explicit "
            "user constraints. Skill source paths identify workflow resources, "
            "not the task workspace. Keep repository operations in "
            "`<environment_context><cwd>` and resolve only Skill-relative files "
            "against the corresponding Skill directory.\n\n"
            + "\n\n---\n\n".join(sections)
        )


class SkillError(ValueError):
    """Base class for deterministic Skill validation and resolution errors."""


class SkillValidationError(SkillError):
    pass


class SkillResolutionError(SkillError):
    pass


__all__ = [
    "SkillError",
    "SkillInvocation",
    "SkillInvocationKind",
    "SkillInterface",
    "SkillKey",
    "SkillRecord",
    "SkillMetadata",
    "SkillResolutionError",
    "SkillScope",
    "SkillSelection",
    "SkillSourceRoot",
    "SkillStatus",
    "SkillTurnSnapshot",
    "SkillValidationError",
    "MUTABLE_SKILL_SCOPES",
]
