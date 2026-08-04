"""Per-Turn Skill resolution and progressive-disclosure runtime."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from core.skills.catalog import SkillCatalog, SkillCatalogProvider
from core.skills.config import SkillPolicyStore
from core.skills.models import (
    SkillInvocation,
    SkillInvocationKind,
    SkillRecord,
    SkillResolutionError,
    SkillSelection,
    SkillTurnSnapshot,
)
from core.skills.prompting import (
    SkillPromptBundle,
    build_skill_prompt_bundle,
    render_skill_catalog,
)

MAX_SKILLS_PER_TURN = 8
MAX_INJECTED_SKILL_CHARS = 48_000


@dataclass(slots=True)
class SkillTurnContext:
    catalog: SkillCatalog
    snapshot: SkillTurnSnapshot
    loaded: dict[str, SkillInvocation] = field(default_factory=dict)
    allowed_tools: set[str] = field(default_factory=set)
    has_tool_restriction: bool = False
    on_invocation: Callable[[SkillInvocation], None] | None = None
    on_failure: Callable[[str, str | None], None] | None = None

    def register(
        self,
        record: SkillRecord,
        kind: SkillInvocationKind,
    ) -> tuple[SkillInvocation, bool]:
        existing = self.loaded.get(record.id)
        if existing is not None:
            return existing, False
        invocation = SkillInvocation(
            skill_id=record.id,
            name=record.name,
            revision=record.revision,
            source=record.source,
            kind=kind,
        )
        self.loaded[record.id] = invocation
        if record.allowed_tools:
            self.has_tool_restriction = True
            self.allowed_tools.update(tool.casefold() for tool in record.allowed_tools)
        if self.on_invocation is not None:
            self.on_invocation(invocation)
        return invocation, True


class SkillRuntime:
    """One shared Skill backend used by AgentSession and every frontend."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        home: str | Path | None = None,
        working_directory: str | Path | None = None,
        include_user: bool = True,
        include_system: bool = True,
        policy: SkillPolicyStore | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve(strict=False)
        self.policy = policy or SkillPolicyStore(self.workspace)
        self.catalog_provider = SkillCatalogProvider(
            self.workspace,
            home=home,
            working_directory=working_directory,
            include_user=include_user,
            include_system=include_system,
            disabled_loader=self.policy.effective_disabled,
        )
        self._turn: ContextVar[SkillTurnContext | None] = ContextVar(
            f"deepcode_skill_turn_{id(self)}",
            default=None,
        )

    def catalog(self, *, force: bool = False) -> SkillCatalog:
        return self.catalog_provider.catalog(force=force)

    def begin_turn(
        self,
        text: str,
        selections: tuple[SkillSelection, ...] = (),
        *,
        on_invocation: Callable[[SkillInvocation], None] | None = None,
    ) -> tuple[SkillTurnContext, Token]:
        catalog = self.catalog()
        ordered: list[tuple[SkillRecord, SkillInvocationKind]] = []
        seen: set[str] = set()
        for selection in selections:
            record = catalog.select_id(selection.skill_id)
            if selection.name and selection.name.casefold() != record.name.casefold():
                raise SkillResolutionError(
                    f"Stale Skill selection: expected {selection.name}, "
                    f"resolved {record.name}"
                )
            if record.id not in seen:
                ordered.append((record, SkillInvocationKind.EXPLICIT))
                seen.add(record.id)
        for record in catalog.text_mentions(text):
            if record.id not in seen:
                ordered.append((record, SkillInvocationKind.TEXT_MENTION))
                seen.add(record.id)
        if len(ordered) > MAX_SKILLS_PER_TURN:
            raise SkillResolutionError(
                f"A Turn may select at most {MAX_SKILLS_PER_TURN} Skills"
            )
        instruction_chars = sum(len(record.instructions) for record, _kind in ordered)
        if instruction_chars > MAX_INJECTED_SKILL_CHARS:
            raise SkillResolutionError(
                "Selected Skill instructions exceed the per-Turn budget "
                f"({instruction_chars} > {MAX_INJECTED_SKILL_CHARS} characters)"
            )

        invocations = tuple(
            SkillInvocation(
                skill_id=record.id,
                name=record.name,
                revision=record.revision,
                source=record.source,
                kind=kind,
            )
            for record, kind in ordered
        )
        declared_tools = {
            tool.casefold()
            for record, _kind in ordered
            for tool in record.allowed_tools
        }
        snapshot = SkillTurnSnapshot(
            records=tuple(record for record, _kind in ordered),
            invocations=invocations,
            catalog_revision=catalog.revision,
            allowed_tools=frozenset(declared_tools) if declared_tools else None,
        )
        context = SkillTurnContext(
            catalog=catalog,
            snapshot=snapshot,
            on_invocation=on_invocation,
        )
        for record, kind in ordered:
            context.register(record, kind)
        return context, self._turn.set(context)

    def end_turn(self, token: Token) -> None:
        self._turn.reset(token)

    def current_context(self) -> SkillTurnContext | None:
        return self._turn.get()

    def load_implicit(self, name: str) -> tuple[SkillRecord, bool]:
        context = self.current_context()
        if context is None:
            # Compatibility for direct Tool execution outside AgentSession.
            record = self.catalog().get_implicit(name)
            if record is None:
                raise SkillResolutionError(f"Skill is not available: {name}")
            return record, True
        record = context.catalog.get_implicit(name)
        if record is None:
            explicit_only = context.catalog.get_active(name)
            message = (
                f"Skill requires explicit selection: {name}"
                if explicit_only is not None
                and not explicit_only.metadata.allow_implicit_invocation
                else f"Skill is not available: {name}"
            )
            if context.on_failure is not None:
                context.on_failure(message, explicit_only.id if explicit_only else None)
            raise SkillResolutionError(message)
        if record.id in context.loaded:
            return record, False
        if len(context.loaded) >= MAX_SKILLS_PER_TURN:
            message = f"A Turn may load at most {MAX_SKILLS_PER_TURN} Skills"
            if context.on_failure is not None:
                context.on_failure(message, record.id)
            raise SkillResolutionError(message)
        instruction_chars = sum(
            len(candidate.instructions)
            for skill_id in context.loaded
            if (candidate := context.catalog.get(skill_id)) is not None
        ) + len(record.instructions)
        if instruction_chars > MAX_INJECTED_SKILL_CHARS:
            message = (
                "Loaded Skill instructions exceed the per-Turn budget "
                f"({instruction_chars} > {MAX_INJECTED_SKILL_CHARS} characters)"
            )
            if context.on_failure is not None:
                context.on_failure(message, record.id)
            raise SkillResolutionError(message)
        _invocation, added = context.register(record, SkillInvocationKind.IMPLICIT)
        return record, added

    def allowed_tool_names(self) -> frozenset[str] | None:
        context = self.current_context()
        if context is None or not context.has_tool_restriction:
            return None
        # The progressive-disclosure tool must remain available so a model can
        # compose another Skill. It cannot grant any external permission.
        return frozenset((*context.allowed_tools, "skill"))

    def visible_tool_names(
        self,
        registered_names: tuple[str, ...],
    ) -> frozenset[str] | None:
        """Return a dynamic per-Turn tool filter.

        An empty catalog hides the internally registered progressive-disclosure
        tool so a workspace with no Skills retains the original CLI tool
        surface. The tool becomes visible on the next Turn after a Skill is
        added, without rebuilding the Session.
        """

        context = self.current_context()
        allowed = self.allowed_tool_names()
        if allowed is not None:
            return frozenset(
                name for name in registered_names if name.casefold() in allowed
            )
        if context is None or context.catalog.implicit():
            return None
        return frozenset(name for name in registered_names if name != "skill")

    def preamble(
        self,
        catalog: SkillCatalog | None = None,
        *,
        context_window_tokens: int | None = None,
    ) -> str:
        active = (catalog or self.catalog()).implicit()
        return render_skill_catalog(
            active,
            context_window_tokens=context_window_tokens,
        )

    def prompt_bundle(
        self,
        context: SkillTurnContext,
        *,
        context_window_tokens: int | None = None,
    ) -> SkillPromptBundle:
        return build_skill_prompt_bundle(
            context.catalog.implicit(),
            context.snapshot,
            context_window_tokens=context_window_tokens,
        )

    def invalidate(self) -> None:
        self.catalog_provider.invalidate()


def render_skill(record: SkillRecord) -> str:
    header = f"# Skill: {record.name}\n{record.description}\n"
    if record.allowed_tools:
        header += (
            "\nThis Skill declares a restricted tool set: "
            + ", ".join(record.allowed_tools)
            + "\n"
        )
    header += (
        f"\nSkill resources are in `{record.directory}`. This is not the task "
        "workspace: keep repository operations in `<environment_context><cwd>`. "
        "Resolve Skill-relative files against this directory and access bundled "
        "resources only through the normal permission and sandbox policy.\n"
    )
    return f"{header}\n{record.instructions}"


__all__ = [
    "MAX_INJECTED_SKILL_CHARS",
    "MAX_SKILLS_PER_TURN",
    "SkillRuntime",
    "SkillTurnContext",
    "render_skill",
]
