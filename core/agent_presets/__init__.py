"""Agent presets — named model-facing compositions for a Session.

The skeleton follows dsh's agent-presets package; the file dialect does not.
A preset bundles exactly what the dsh blueprint allows a preset to own — a
persona, a tool allowlist, whether the session may spawn sub-agents — and
nothing it must not: the model route, approval policy, and sandbox remain
independent session-level knobs (``/permissions``, ``--model``), unchanged.

Instead of dsh's private composition YAML, a preset is one **agent file** in
the cross-product dialect used by ``.claude/agents`` and the wider
``.agents`` ecosystem: markdown with YAML frontmatter, body = persona. A
definition written for another harness loads here unchanged — the same
zero-copy stance already proven for ``.agents/skills``. Foreign tool names
(``Read``, ``WebFetch``) are normalized mechanically (CamelCase → snake);
unknown frontmatter keys are tolerated, never fatal.

Rules inherited from dsh:

- **Identity is the file stem.** Frontmatter cannot claim another id or a
  trust level, so a user-authored preset cannot impersonate a shipped one.
- **Broken presets stay on the roster** with their ``broken`` reason instead
  of silently disappearing; an unknown id is the *caller's* error and the
  raised exception carries the available roster.
- **Nearest trust root wins a duplicate id** (project > user > system).
- A preset is resolved once, at Session creation, and its resolved values
  are snapshotted into canonical Session metadata — editing the file later
  never changes what an existing Session means.

A preset's tool list only ever narrows the session's registry (the
AgentRunSpec ``tool_filter`` contract). Names that do not exist in a given
session simply narrow to nothing extra; an explicitly empty list is a
legitimate chat-only composition, so no registry vocabulary is baked in
here — the composition is the preset author's visible, explicit choice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from core.config import deepcode_home

TRUST_SYSTEM = "system"
TRUST_USER = "user"
TRUST_PROJECT = "project"

PROMPT_MODE_APPEND = "append"
PROMPT_MODE_REPLACE = "replace"
_PROMPT_MODES = (PROMPT_MODE_APPEND, PROMPT_MODE_REPLACE)

_FRONTMATTER_RE = re.compile(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.DOTALL)
_PRESET_ID_RE = re.compile(r"^(?!.*--)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

_BUILTIN_DIR = Path(__file__).resolve().parent / "builtin"
# Metadata key on the canonical Session holding the resolved snapshot.
METADATA_KEY = "agent_preset"


class AgentPresetError(ValueError):
    """Base error for preset resolution problems."""


class UnknownPresetError(AgentPresetError):
    """The id names no preset. The caller's error; carries the roster."""

    def __init__(self, preset_id: str, available: tuple[str, ...]) -> None:
        roster = ", ".join(available) if available else "none"
        super().__init__(f"unknown agent preset: {preset_id!r} (available: {roster})")
        self.preset_id = preset_id
        self.available = available


class BrokenPresetError(AgentPresetError):
    """The id names a preset whose file cannot compose a session."""

    def __init__(self, preset_id: str, reason: str) -> None:
        super().__init__(f"agent preset {preset_id!r} is broken: {reason}")
        self.preset_id = preset_id
        self.reason = reason


@dataclass(frozen=True, slots=True)
class PresetRoot:
    path: Path
    trust: str


@dataclass(frozen=True, slots=True)
class AgentPreset:
    """One discovered preset — possibly broken, never hidden."""

    id: str
    trust: str
    path: str
    display_name: str = ""
    description: str = ""
    prompt: str = ""
    prompt_mode: str = PROMPT_MODE_APPEND
    tools: tuple[str, ...] | None = None
    allow_spawn: bool | None = None
    suggested_model: str | None = None
    order: int | None = None
    broken: str | None = None

    def snapshot(self) -> AgentPresetSnapshot:
        if self.broken is not None:
            raise BrokenPresetError(self.id, self.broken)
        return AgentPresetSnapshot(
            id=self.id,
            prompt=self.prompt,
            prompt_mode=self.prompt_mode,
            tools=self.tools,
            allow_spawn=self.allow_spawn,
        )


@dataclass(frozen=True, slots=True)
class AgentPresetSnapshot:
    """The resolved by-value composition a Session persists and runs with."""

    id: str
    prompt: str = ""
    prompt_mode: str = PROMPT_MODE_APPEND
    tools: tuple[str, ...] | None = None
    allow_spawn: bool | None = None

    def to_metadata(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "promptMode": self.prompt_mode,
            "tools": list(self.tools) if self.tools is not None else None,
            "allowSpawn": self.allow_spawn,
        }

    @classmethod
    def from_metadata(cls, value: Any) -> AgentPresetSnapshot | None:
        """Decode a stored snapshot; anything unreadable means "no preset".

        The canonical Session is hand-editable JSONL, so decoding is
        tolerant: a malformed snapshot degrades to the default composition
        instead of making the Session unopenable.
        """
        if not isinstance(value, dict) or not isinstance(value.get("id"), str):
            return None
        raw_tools = value.get("tools")
        tools: tuple[str, ...] | None = None
        if isinstance(raw_tools, list):
            tools = tuple(str(name) for name in raw_tools)
        mode = value.get("promptMode")
        allow_spawn = value.get("allowSpawn")
        return cls(
            id=value["id"],
            prompt=str(value.get("prompt") or ""),
            prompt_mode=(mode if mode in _PROMPT_MODES else PROMPT_MODE_APPEND),
            tools=tools,
            allow_spawn=allow_spawn if isinstance(allow_spawn, bool) else None,
        )

    def fingerprint(self) -> tuple[Any, ...]:
        """Hashable identity for runtime-key comparison."""
        return (self.id, self.prompt, self.prompt_mode, self.tools, self.allow_spawn)

    def compose_system_prompt(self, base: str) -> str:
        if not self.prompt:
            return base
        if self.prompt_mode == PROMPT_MODE_REPLACE:
            return self.prompt
        # Same section shape the sub-agent composer uses (control.py).
        return f"{base}\n\n## Persona\n{self.prompt}"

    def tool_filter(self) -> Any | None:
        """AgentRunSpec-contract narrowing filter, or None when unrestricted."""
        if self.tools is None:
            return None
        allowed = frozenset(self.tools)

        def preset_filter(names: tuple[str, ...]) -> tuple[str, ...]:
            return tuple(name for name in names if name in allowed)

        return preset_filter


def normalize_tool_name(name: str) -> str:
    """Fold foreign agent-file dialect tool names onto this registry's shape.

    Purely mechanical — ``Read`` → ``read``, ``WebFetch`` → ``web_fetch`` —
    so no cross-product alias table has to be maintained. A name with no
    local counterpart simply never matches, which only narrows further.
    MCP names (``mcp__server__tool``) are verbatim registry keys and pass
    through untouched — folding would corrupt a camelCase remote tool name.
    """
    name = name.strip()
    if "__" in name:
        return name
    return _CAMEL_BOUNDARY_RE.sub("_", name).lower()


def discover_preset_roots(workspace: str | Path | None = None) -> list[PresetRoot]:
    """Trust-ranked roots, highest precedence first (project > user > system)."""
    roots: list[PresetRoot] = []
    if workspace is not None:
        base = Path(workspace)
        roots.append(PresetRoot(base / ".agents" / "presets", TRUST_PROJECT))
        # Zero-copy interop: definitions written for Claude Code load as-is.
        roots.append(PresetRoot(base / ".claude" / "agents", TRUST_PROJECT))
    roots.append(PresetRoot(deepcode_home() / "agent-presets", TRUST_USER))
    home = Path.home()
    roots.append(PresetRoot(home / ".agents" / "presets", TRUST_USER))
    roots.append(PresetRoot(home / ".claude" / "agents", TRUST_USER))
    roots.append(PresetRoot(_BUILTIN_DIR, TRUST_SYSTEM))
    return roots


def list_agent_presets(workspace: str | Path | None = None) -> list[AgentPreset]:
    """Every discovered preset, nearest-root-wins on duplicate ids.

    Broken files are included with their reason (the dsh roster rule) so a
    deployment problem is visible instead of a preset silently vanishing.
    """
    presets: dict[str, AgentPreset] = {}
    for root in discover_preset_roots(workspace):
        try:
            entries = sorted(root.path.glob("*.md"))
        except OSError:
            continue
        for path in entries:
            if not path.is_file():
                continue
            preset = _parse_preset_file(path, trust=root.trust)
            presets.setdefault(preset.id, preset)
    ordered = sorted(
        presets.values(),
        key=lambda p: (p.order if p.order is not None else 1_000_000, p.id),
    )
    return ordered


def resolve_agent_preset(
    preset_id: str,
    workspace: str | Path | None = None,
) -> AgentPresetSnapshot:
    """Resolve one id to its by-value snapshot.

    Unknown id → :class:`UnknownPresetError` (a bad request, with the
    roster); broken file → :class:`BrokenPresetError` (a deployment
    problem). The distinction is dsh's and worth keeping: the first is the
    caller's to fix, the second the preset author's.
    """
    roster = list_agent_presets(workspace)
    for preset in roster:
        if preset.id == preset_id:
            return preset.snapshot()
    raise UnknownPresetError(
        preset_id,
        tuple(p.id for p in roster if p.broken is None),
    )


def _parse_preset_file(path: Path, *, trust: str) -> AgentPreset:
    preset_id = path.stem
    if not _PRESET_ID_RE.fullmatch(preset_id) or len(preset_id) > 64:
        return AgentPreset(
            id=preset_id,
            trust=trust,
            path=str(path),
            broken="file name must be a kebab-case preset id",
        )
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return AgentPreset(id=preset_id, trust=trust, path=str(path), broken=str(exc))

    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return AgentPreset(
            id=preset_id,
            trust=trust,
            path=str(path),
            broken="missing YAML frontmatter block",
        )
    try:
        data = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        return AgentPreset(
            id=preset_id,
            trust=trust,
            path=str(path),
            broken=f"invalid YAML frontmatter: {exc}",
        )
    if not isinstance(data, dict):
        return AgentPreset(
            id=preset_id,
            trust=trust,
            path=str(path),
            broken="frontmatter must be a mapping",
        )

    tools, tools_error = _parse_tools(data.get("tools"))
    if tools_error is not None:
        return AgentPreset(
            id=preset_id, trust=trust, path=str(path), broken=tools_error
        )
    mode = str(data.get("prompt-mode") or data.get("promptMode") or "").strip()
    if mode and mode not in _PROMPT_MODES:
        return AgentPreset(
            id=preset_id,
            trust=trust,
            path=str(path),
            broken=f"prompt-mode must be one of {_PROMPT_MODES}, got {mode!r}",
        )
    allow_spawn = data.get("allow-spawn", data.get("allowSpawn"))
    if allow_spawn is not None and not isinstance(allow_spawn, bool):
        return AgentPreset(
            id=preset_id,
            trust=trust,
            path=str(path),
            broken="allow-spawn must be a boolean",
        )

    order = data.get("order")
    model = data.get("model")
    return AgentPreset(
        id=preset_id,
        trust=trust,
        path=str(path),
        display_name=str(data.get("name") or preset_id),
        description=str(data.get("description") or ""),
        prompt=text[match.end() :].strip(),
        prompt_mode=mode or PROMPT_MODE_APPEND,
        tools=tools,
        allow_spawn=allow_spawn,
        suggested_model=str(model) if isinstance(model, str) and model else None,
        order=order if isinstance(order, int) else None,
    )


def _parse_tools(value: Any) -> tuple[tuple[str, ...] | None, str | None]:
    """``tools:`` field → normalized allowlist. Absent or ``*`` = unrestricted."""
    if value is None:
        return None, None
    if isinstance(value, str):
        if value.strip() == "*":
            return None, None
        names = [part for part in re.split(r"[,\s]+", value) if part]
    elif isinstance(value, list):
        if any(not isinstance(item, str) for item in value):
            return None, "tools list entries must be strings"
        names = [item for item in value if item.strip()]
    else:
        return None, "tools must be a string or a list of strings"
    normalized: list[str] = []
    for name in names:
        folded = normalize_tool_name(name)
        if folded and folded not in normalized:
            normalized.append(folded)
    return tuple(normalized), None


__all__ = [
    "METADATA_KEY",
    "AgentPreset",
    "AgentPresetError",
    "AgentPresetSnapshot",
    "BrokenPresetError",
    "PresetRoot",
    "UnknownPresetError",
    "discover_preset_roots",
    "list_agent_presets",
    "normalize_tool_name",
    "resolve_agent_preset",
]
