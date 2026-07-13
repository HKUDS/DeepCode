"""Agent skills — Claude Code-compatible SKILL.md capabilities (P5).

A *skill* is a folder with a ``SKILL.md``: YAML frontmatter (``name``,
``description``, optional ``allowed-tools``) plus a markdown body of
instructions — a pre-authored playbook for a recurring task. The agent sees
each discovered skill's name + description in its system prompt (cheap), and
loads the full instructions on demand through the ``skill`` tool. This is Claude
Code's *progressive disclosure* model, so a workspace can carry many skills
without bloating every prompt.

**Ecosystem interop** (DEEPCODE_V2_MASTER_PLAN.md P5): skills are discovered from
BOTH DeepCode's own ``.deepcode/skills/`` and Claude Code's ``.claude/skills/``,
at project and user level — so a skill authored for either agent runs here
unchanged, driven by DeepCode's own model and tools. This only *reads files*;
it never invokes Claude Code and does not depend on Anthropic.

Assembled once in :func:`core.agent_setup.build_agent_session`, exactly like
:mod:`core.harness.memory`, so every frontend (TUI, exec, loop, team) gets the
same skills. Pure mechanism: filesystem discovery + text; *which* skill to
invoke is the model's decision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from core.agent_runtime.tools.base import Tool, tool_parameters

_SKILL_FILE = "SKILL.md"
# Search roots in precedence order (first skill of a given name wins): a
# project skill overrides a same-named user skill; within a level DeepCode's own
# dir is consulted before Claude Code's. Reading .claude/ is the interop point.
_PROJECT_ROOTS = (".deepcode/skills", ".claude/skills")
_USER_ROOTS = (".deepcode/skills", ".claude/skills")  # resolved under $HOME
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_MAX_BODY_CHARS = 16000  # cap the body the tool returns; keep context bounded


class SkillError(ValueError):
    """A SKILL.md is malformed (missing frontmatter or required fields)."""


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    instructions: str
    allowed_tools: tuple[str, ...] = ()
    directory: str = ""  # the skill folder, for reading bundled resources
    source: str = ""  # provenance (which root it came from)

    @property
    def summary_line(self) -> str:
        return f"- **{self.name}**: {self.description}"


def _coerce_tools(value: Any) -> tuple[str, ...]:
    """Accept ``allowed-tools`` as a list or a comma-separated string."""
    if not value:
        return ()
    if isinstance(value, str):
        return tuple(t.strip() for t in value.split(",") if t.strip())
    if isinstance(value, (list, tuple)):
        return tuple(str(t).strip() for t in value if str(t).strip())
    return ()


def parse_skill_md(path: str | Path, *, source: str = "") -> Skill:
    """Parse one ``SKILL.md`` into a :class:`Skill`. Raises :class:`SkillError`
    on a missing frontmatter block or a missing name/description."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise SkillError(f"cannot read {p}: {exc}") from exc

    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise SkillError(f"{p}: SKILL.md must open with a YAML frontmatter block")

    import yaml

    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise SkillError(f"{p}: invalid YAML frontmatter: {exc}") from exc
    if not isinstance(meta, dict):
        raise SkillError(f"{p}: frontmatter must be a mapping")

    # `name` falls back to the skill folder name (Claude Code convention).
    name = str(meta.get("name") or p.parent.name).strip()
    description = str(meta.get("description") or "").strip()
    if not name:
        raise SkillError(f"{p}: skill needs a name")
    if not description:
        raise SkillError(f"{p}: skill {name!r} needs a description")

    body = text[match.end() :].strip()
    allowed = _coerce_tools(meta.get("allowed-tools", meta.get("allowed_tools")))
    return Skill(
        name=name,
        description=description,
        instructions=body,
        allowed_tools=allowed,
        directory=str(p.parent),
        source=source,
    )


class SkillRegistry:
    """The skills available to a session, keyed by name (first-added wins)."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self.errors: list[str] = []  # malformed skills, surfaced not swallowed

    def add(self, skill: Skill) -> bool:
        """Register ``skill`` unless a higher-precedence one already holds the
        name. Returns whether it was added."""
        if skill.name in self._skills:
            return False
        self._skills[skill.name] = skill
        return True

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def all(self) -> list[Skill]:
        return sorted(self._skills.values(), key=lambda s: s.name)

    def names(self) -> list[str]:
        return sorted(self._skills)

    def __len__(self) -> int:
        return len(self._skills)

    def __bool__(self) -> bool:
        return bool(self._skills)


def _iter_skill_files(root: Path) -> Iterator[Path]:
    """Yield each ``<root>/<name>/SKILL.md`` that exists, sorted by folder."""
    if not root.is_dir():
        return
    for child in sorted(root.iterdir()):
        if child.is_dir():
            skill_file = child / _SKILL_FILE
            if skill_file.is_file():
                yield skill_file


def discover_skills(
    workspace: str | Path,
    *,
    home: str | Path | None = None,
    include_user: bool = True,
) -> SkillRegistry:
    """Discover skills for ``workspace`` from the DeepCode and Claude Code dirs,
    project level first then (unless ``include_user`` is False) user level.

    ``home`` overrides ``$HOME`` (tests). ``include_user=False`` keeps discovery
    workspace-hermetic — used by :func:`default_coding_tools` so a standalone
    build never depends on ambient ``~/.claude/skills``; the full project+user
    set is discovered once in :func:`core.agent_setup.build_agent_session`.
    """
    registry = SkillRegistry()
    ws = Path(workspace)
    roots: list[tuple[Path, str]] = [
        (ws / rel, f"project:{rel}") for rel in _PROJECT_ROOTS
    ]
    if include_user:
        home_dir = Path(home) if home is not None else Path.home()
        roots += [(home_dir / rel, f"user:{rel}") for rel in _USER_ROOTS]
    for root, source in roots:
        for skill_file in _iter_skill_files(root):
            try:
                registry.add(parse_skill_md(skill_file, source=source))
            except SkillError as exc:
                registry.errors.append(str(exc))
    return registry


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The skill to load (one of the available skills "
                "listed in the system prompt).",
            }
        },
        "required": ["name"],
    }
)
class SkillTool(Tool):
    """Load a skill's full instructions on demand (progressive disclosure)."""

    def __init__(self, registry: SkillRegistry):
        self._registry = registry

    @property
    def name(self) -> str:
        return "skill"

    @property
    def description(self) -> str:
        return (
            "Load the full instructions for a named skill before doing that "
            "kind of task. Skills are pre-authored playbooks; the available "
            "ones are listed in the system prompt. Returns the skill's steps "
            "for you to follow with your normal tools."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> Any:
        name = str(kwargs.get("name") or "").strip()
        skill = self._registry.get(name)
        if skill is None:
            available = ", ".join(self._registry.names()) or "(none)"
            return f"Error: no skill named {name!r}. Available skills: {available}"

        body = skill.instructions[:_MAX_BODY_CHARS]
        if len(skill.instructions) > _MAX_BODY_CHARS:
            body += "\n…[truncated]"
        header = f"# Skill: {skill.name}\n{skill.description}\n"
        if skill.allowed_tools:
            header += f"\nIntended tools: {', '.join(skill.allowed_tools)}\n"
        if skill.directory:
            header += (
                f"\nSkill files are in `{skill.directory}` — read any bundled "
                "resources with the read tool.\n"
            )
        return f"{header}\n{body}"


def skills_preamble(registry: SkillRegistry) -> str:
    """The available-skills addendum for the system prompt — names and
    descriptions only (progressive disclosure; the tool loads the rest)."""
    if not registry:
        return ""
    lines = "\n".join(s.summary_line for s in registry.all())
    return (
        "## Available skills\n\n"
        "Pre-authored playbooks for specific tasks. When a task matches one, "
        "call the `skill` tool with its name to load the full instructions "
        "BEFORE starting, then follow them.\n\n" + lines
    )
