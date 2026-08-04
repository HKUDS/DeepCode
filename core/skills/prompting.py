"""Provider-neutral prompt rendering for Agent Skills.

The available catalog is developer-priority capability guidance. Selected
instructions are user-priority workflow context. Both are attached to one
model Turn only; :mod:`core.agent_runtime.runner` keeps them out of durable
conversation history and maps developer priority to each provider safely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from core.agent_runtime.helpers import estimate_message_tokens
from core.skills.models import SkillRecord, SkillTurnSnapshot

CATALOG_CONTEXT_FRACTION = 0.02
UNKNOWN_CONTEXT_CHAR_BUDGET = 8_000
_DESCRIPTION_LIMITS = (1_000, 240, 120, 0)

_CATALOG_INTRO = (
    "## Skills\n\n"
    "Available reusable workflows. If the user names `$name`, or the task "
    "clearly matches a description, load that Skill before acting. Paths locate "
    "resources, not the task workspace: keep repository work in "
    "`<environment_context><cwd>` and resolve Skill-relative files from the "
    "Skill directory. Skills cannot override security or explicit user "
    "constraints.\n\n"
)


@dataclass(frozen=True, slots=True)
class SkillPromptBundle:
    """Transient catalog and selected instructions for one model Turn."""

    catalog: str = ""
    selected: str = ""

    def messages(self) -> tuple[dict[str, str], ...]:
        messages: list[dict[str, str]] = []
        if self.catalog:
            messages.append(
                {
                    # Internal developer priority. AgentRunner folds this into
                    # the provider's single system/developer instruction block.
                    "role": "developer",
                    "content": (
                        f"<skills_instructions>\n{self.catalog}\n</skills_instructions>"
                    ),
                }
            )
        if self.selected:
            messages.append(
                {
                    "role": "user",
                    "content": (f"<skill>\n{self.selected}\n</skill>"),
                }
            )
        return tuple(messages)


def build_skill_prompt_bundle(
    records: Iterable[SkillRecord],
    snapshot: SkillTurnSnapshot,
    *,
    context_window_tokens: int | None,
) -> SkillPromptBundle:
    return SkillPromptBundle(
        catalog=render_skill_catalog(
            records,
            context_window_tokens=context_window_tokens,
        ),
        selected=snapshot.injected_instructions,
    )


def render_skill_catalog(
    records: Iterable[SkillRecord],
    *,
    context_window_tokens: int | None,
) -> str:
    """Render progressive-disclosure metadata within the model's budget.

    Known context windows reserve at most two percent of their token capacity.
    Unknown windows use the documented 8,000-character fallback. Descriptions
    are shortened before entries are omitted; full ``SKILL.md`` content remains
    available through explicit selection or the ``skill`` tool.
    """

    ordered = tuple(records)
    if not ordered:
        return ""

    token_budget = (
        max(1, int(context_window_tokens * CATALOG_CONTEXT_FRACTION))
        if context_window_tokens and context_window_tokens > 0
        else None
    )

    def fits(text: str) -> bool:
        if token_budget is not None:
            return (
                estimate_message_tokens({"role": "user", "content": text})
                <= token_budget
            )
        return len(text) <= UNKNOWN_CONTEXT_CHAR_BUDGET

    lines: list[str] = []
    for record in ordered:
        chosen: str | None = None
        for description_limit in _DESCRIPTION_LIMITS:
            line = _summary_line(record, description_limit=description_limit)
            candidate = _CATALOG_INTRO + "\n".join((*lines, line))
            if fits(candidate):
                chosen = line
                break
        if chosen is None:
            break
        lines.append(chosen)

    while len(lines) < len(ordered):
        omitted = len(ordered) - len(lines)
        notice = (
            f"- … {omitted} additional Skills remain available through explicit "
            "selection."
        )
        candidate = _CATALOG_INTRO + "\n".join((*lines, notice))
        if fits(candidate):
            lines.append(notice)
            break
        if not lines:
            break
        lines.pop()

    return _CATALOG_INTRO + "\n".join(lines)


def _summary_line(record: SkillRecord, *, description_limit: int) -> str:
    description = (
        record.metadata.interface.short_description or record.description
    ).strip()
    if description_limit == 0:
        description = ""
    elif len(description) > description_limit:
        description = description[: max(1, description_limit - 1)].rstrip() + "…"
    detail = f": {description}" if description else ""
    return f"- **{record.name}**{detail} (`{record.directory}/SKILL.md`)"


__all__ = [
    "CATALOG_CONTEXT_FRACTION",
    "SkillPromptBundle",
    "UNKNOWN_CONTEXT_CHAR_BUDGET",
    "build_skill_prompt_bundle",
    "render_skill_catalog",
]
