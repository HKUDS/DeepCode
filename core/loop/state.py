"""LoopState — the durable, file-based state of an autonomous loop (P3).

Ralph's "file-based state": a loop's entire progress lives as JSON on disk
(``<workspace>/.deepcode/loop/state.json``), so a loop survives a crash or a
process restart and can be inspected with ``cat``/``jq``. Nothing about a
loop's decision-making lives only in memory.

Pure data + load/save. No agent, no subprocess — the engine
(:mod:`core.loop.task`) drives it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

_STATE_SUBPATH = ".deepcode/loop/state.json"

# Loop lifecycle. running → one of the terminal states.
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"  # tests green
STATUS_EXHAUSTED = "exhausted"  # hit the round budget
STATUS_STALLED = "stalled"  # no progress across rounds
STATUS_ERROR = "error"  # an unexpected failure
TERMINAL = {STATUS_SUCCEEDED, STATUS_EXHAUSTED, STATUS_STALLED, STATUS_ERROR}


@dataclass
class RoundRecord:
    """One round of the loop: what the agent did and what the tests said."""

    index: int
    agent_stop_reason: str = ""
    tests_passed: bool | None = None  # None = tests not run / no command
    test_summary: str = ""
    test_signature: str = ""  # a stable fingerprint of the failure, for stall detection
    snapshot_id: str = ""
    handoff: str = ""  # compact note carried to the next round


@dataclass
class LoopState:
    """The full, persistable state of a loop run."""

    goal: str
    workspace: str
    test_command: str = ""
    max_rounds: int = 8
    status: str = STATUS_RUNNING
    stop_reason: str = ""
    rounds: list[RoundRecord] = field(default_factory=list)

    # -- derived ---------------------------------------------------------------

    @property
    def round_count(self) -> int:
        return len(self.rounds)

    @property
    def last_round(self) -> RoundRecord | None:
        return self.rounds[-1] if self.rounds else None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL

    # -- mutation --------------------------------------------------------------

    def add_round(self, record: RoundRecord) -> None:
        self.rounds.append(record)

    def finish(self, status: str, reason: str) -> None:
        self.status = status
        self.stop_reason = reason

    # -- persistence -----------------------------------------------------------

    @staticmethod
    def path_for(workspace: str | Path) -> Path:
        return Path(workspace) / _STATE_SUBPATH

    def save(self) -> None:
        path = self.path_for(self.workspace)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, workspace: str | Path) -> "LoopState | None":
        path = cls.path_for(workspace)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        rounds = [RoundRecord(**r) for r in data.pop("rounds", [])]
        return cls(**data, rounds=rounds)

    def to_dict(self) -> dict:
        return asdict(self)
