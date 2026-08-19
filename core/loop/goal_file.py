"""P0-1: GOAL.yaml-style model-writable goal file (PenguinHarness lesson).

PenguinHarness' goal mode (``goal-file.ts``) gives the *model* a writable
control channel: the system writes GOAL.yaml once (``objective`` + ``status``),
the model may only edit ``status`` (``complete`` / ``blocked``) with shell
tools, and the loop reads it after every round to decide whether to continue.
The objective's canonical value lives in the loop's memory and is re-stated
each round, so a tampered file changes nothing.

DeepCode's ``core.loop.state.LoopState`` is a *system-internal* file (the
model never sees or writes it). This module adds the complementary
model-visible control file used by goal-mode loops:

* **Model-writable mailbox.** The model sets ``status``; the loop treats it
  as the authoritative stop signal.
* **Fault-tolerant reads.** A parse failure, missing file, or out-of-protocol
  status all normalize to ``blocked`` — a broken control channel stops the
  loop instead of spinning forever (mirrors PenguinHarness' tolerance).
* **No YAML dependency.** PenguinHarness parses SKILL.md frontmatter without
  a YAML library; we parse the two-field control file the same way (a real
  dependency-free subset), falling back to JSON when present.
* **Status ownership.** System-side endings (budget_limited / aborted) are
  reported on the event stream, never written here — the file always keeps
  the model's own last write, which is the resume point of an interrupted
  goal.

Design rule (mirrors ``core.harness``): pure mechanism — read/write the file,
no agent, no subprocess.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# Goal statuses. ``active`` (initial), ``complete``/``blocked`` (model-write),
# ``budget_limited`` (system-side outcome, never written to disk).
GOAL_ACTIVE = "active"
GOAL_COMPLETE = "complete"
GOAL_BLOCKED = "blocked"
GOAL_BUDGET_LIMITED = "budget_limited"

_VALID_MODEL_STATUSES = {GOAL_ACTIVE, GOAL_COMPLETE, GOAL_BLOCKED}


@dataclass
class GoalFile:
    """In-memory view of the model-visible goal control file."""

    objective: str
    status: str = GOAL_ACTIVE


# ---------------------------------------------------------------------------
# Minimal YAML-subset serialization (no dependency, mirrors PenguinHarness'
# dependency-free frontmatter parser).
# ---------------------------------------------------------------------------
# Accepted forms:
#   objective: <value>
#   status: <value>
# Values are scalars; YAML single/double quotes are stripped; a leading
# `# ` comment line is ignored. Anything else that doesn't fit normalizes to
# `blocked` on read.


def serialize_goal_file(goal: GoalFile) -> str:
    """Serialize to the dependency-free YAML subset (stable field order)."""
    return f"objective: {goal.objective}\nstatus: {goal.status}\n"


def _parse_scalar_line(line: str) -> tuple[str, str] | None:
    idx = line.find(":")
    if idx <= 0:
        return None
    key = line[:idx].strip()
    value = line[idx + 1 :].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1].strip()
    if not key or not value:
        return None
    return key, value


def _parse_goal_text(text: str) -> GoalFile | None:
    """Parse the control-file text into a GoalFile (or None on failure)."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parsed = _parse_scalar_line(line)
        if parsed is None:
            return None  # a non-field line → invalid control file
        key, value = parsed
        fields[key] = value
    objective = fields.get("objective")
    if not objective:
        return None
    status = fields.get("status", GOAL_ACTIVE)
    return GoalFile(objective=objective, status=status)


def _parse_goal_json(text: str) -> GoalFile | None:
    """Parse a JSON form of the control file (tolerant fallback)."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    objective = data.get("objective")
    if not isinstance(objective, str) or not objective:
        return None
    status = data.get("status", GOAL_ACTIVE)
    if not isinstance(status, str):
        status = GOAL_ACTIVE
    return GoalFile(objective=objective, status=status)


def parse_goal_file(text: str) -> GoalFile | None:
    """Parse the control file, YAML-subset first, JSON fallback. None on
    failure (caller normalizes to blocked)."""
    return _parse_goal_text(text) or _parse_goal_json(text)


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------


def goal_file_path(workspace: str | Path) -> Path:
    """Where the goal control file lives for a workspace."""
    return Path(workspace) / ".deepcode" / "loop" / "GOAL.yaml"


def write_goal_file(workspace: str | Path, goal: GoalFile) -> Path:
    """Write the goal control file (called once, at goal creation)."""
    path = goal_file_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_goal_file(goal), encoding="utf-8")
    return path


def read_goal_status(workspace: str | Path) -> str:
    """Read the model's status from the control file, normalized.

    Everything unreadable, unparsable, or out-of-protocol collapses to
    ``blocked`` — a broken control channel stops the loop rather than looping
    forever (mirrors PenguinHarness' ``readGoalStatus``).
    """
    path = goal_file_path(workspace)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return GOAL_BLOCKED
    goal = parse_goal_file(text)
    if goal is None:
        return GOAL_BLOCKED
    return goal.status if goal.status in _VALID_MODEL_STATUSES else GOAL_BLOCKED


def read_goal_file(workspace: str | Path) -> GoalFile | None:
    """Read the full control file, tolerant of the model's edits.

    Returns None when unreadable/unparsable (caller treats as blocked); the
    status is normalized to a valid model status when possible.
    """
    path = goal_file_path(workspace)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    goal = parse_goal_file(text)
    if goal is None:
        return None
    if goal.status not in _VALID_MODEL_STATUSES:
        goal.status = GOAL_BLOCKED
    return goal


__all__ = [
    "GOAL_ACTIVE",
    "GOAL_BLOCKED",
    "GOAL_BUDGET_LIMITED",
    "GOAL_COMPLETE",
    "GoalFile",
    "goal_file_path",
    "parse_goal_file",
    "read_goal_file",
    "read_goal_status",
    "serialize_goal_file",
    "write_goal_file",
]
