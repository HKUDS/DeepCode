"""Result rows + aggregate report for a harness run."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class ResultRow:
    """The outcome of one instance."""

    instance_id: str
    resolved: bool
    fail_to_pass_ok: bool
    pass_to_pass_ok: bool
    patch_generated: bool
    detail: str = ""


@dataclass
class Report:
    """Aggregate of all instance results — the baseline number lives here."""

    model: str
    mode: str
    rows: list[ResultRow] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def resolved(self) -> int:
        return sum(1 for r in self.rows if r.resolved)

    @property
    def resolved_rate(self) -> float:
        return (self.resolved / self.total) if self.total else 0.0

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "mode": self.mode,
            "total": self.total,
            "resolved": self.resolved,
            "resolved_rate": round(self.resolved_rate, 4),
            "results": [asdict(r) for r in self.rows],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def summary_line(self) -> str:
        pct = self.resolved_rate * 100
        return (
            f"SWE-bench [{self.mode}] {self.model}: "
            f"{self.resolved}/{self.total} resolved ({pct:.1f}%)"
        )
