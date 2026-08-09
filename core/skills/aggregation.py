"""Deterministic composition of source-owned Skill catalogs."""

from __future__ import annotations

from dataclasses import replace

from core.skills.catalog import MAX_CATALOG_ENTRIES, SkillCatalog
from core.skills.models import SkillRecord, SkillStatus


def merge_skill_catalogs(
    catalogs: tuple[SkillCatalog, ...],
    *,
    warnings: tuple[str, ...] = (),
) -> SkillCatalog:
    """Merge Provider catalogs while preserving source and record order.

    Providers resolve their own internal precedence. This function applies the
    same active/shadowed rule only across provider boundaries. Invalid,
    disabled, and already-shadowed entries retain their provider-owned status.
    """

    records: list[SkillRecord] = []
    winner_by_name: dict[str, SkillRecord] = {}
    truncated = False
    for catalog in catalogs:
        for record in catalog.records:
            if len(records) >= MAX_CATALOG_ENTRIES:
                truncated = True
                break
            if record.status is SkillStatus.ACTIVE:
                folded = record.name.casefold()
                winner = winner_by_name.get(folded)
                if winner is None:
                    winner_by_name[folded] = record
                else:
                    record = replace(
                        record,
                        status=SkillStatus.SHADOWED,
                        shadowed_by=winner.id,
                    )
            records.append(record)
        if truncated:
            break

    catalog_warnings = tuple(
        warning for catalog in catalogs for warning in catalog.provider_warnings
    )
    if truncated:
        catalog_warnings = (
            *catalog_warnings,
            f"Merged Skill catalog is limited to {MAX_CATALOG_ENTRIES} entries",
        )
    return SkillCatalog(tuple(records), warnings=(*warnings, *catalog_warnings))


__all__ = ["merge_skill_catalogs"]
