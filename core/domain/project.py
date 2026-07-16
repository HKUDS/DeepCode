"""Project aggregate root and trust boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from core.domain.common import (
    JsonObject,
    new_id,
    require_aware,
    require_non_empty,
    require_prefixed_id,
    utc_now,
    validate_json_object,
)


class TrustState(StrEnum):
    UNTRUSTED = "untrusted"
    TRUSTED = "trusted"


@dataclass(frozen=True, slots=True)
class Project:
    canonical_path: str
    display_name: str
    trust_state: TrustState = TrustState.UNTRUSTED
    settings: JsonObject = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("proj"))
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    last_opened_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        require_prefixed_id(self.id, "proj")
        require_non_empty(self.canonical_path, "canonical_path")
        require_non_empty(self.display_name, "display_name")
        if not Path(self.canonical_path).is_absolute():
            raise ValueError("canonical_path must be absolute")
        for name in ("created_at", "updated_at", "last_opened_at"):
            require_aware(getattr(self, name), name)
        validate_json_object(self.settings, "settings")
