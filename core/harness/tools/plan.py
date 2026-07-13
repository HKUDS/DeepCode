"""update_plan — the agent's self-maintained TODO / checklist (C1).

A general agent should decide *how* to work, not follow a code-prescribed
pipeline. This tool lets the model lay out and track its own steps for a
multi-step task: it supplies the full list of steps, each ``pending`` /
``in_progress`` / ``completed``, and calls the tool again to advance progress.
At most one step may be ``in_progress`` at a time.

Pure session state: it records the plan and echoes a rendered checklist back to
the model (and the UI, via the tool card). It never touches the workspace or the
security posture, so it is side-effect-free and auto-allowed.
"""

from __future__ import annotations

from typing import Any

from core.agent_runtime.tools.base import Tool, tool_parameters

_STATUSES = ("pending", "in_progress", "completed")
_GLYPH = {"pending": "☐", "in_progress": "▶", "completed": "☑"}


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "explanation": {
                "type": "string",
                "description": "Optional one-line note about this plan update.",
            },
            "plan": {
                "type": "array",
                "description": "The full list of steps (replaces the previous plan).",
                "items": {
                    "type": "object",
                    "properties": {
                        "step": {"type": "string", "description": "Task step text."},
                        "status": {
                            "type": "string",
                            "enum": list(_STATUSES),
                            "description": "Step status.",
                        },
                    },
                    "required": ["step", "status"],
                },
            },
        },
        "required": ["plan"],
    }
)
class UpdatePlanTool(Tool):
    """The agent's own TODO plan. At most one step may be in_progress."""

    def __init__(self) -> None:
        self._plan: list[dict[str, str]] = []

    @property
    def name(self) -> str:
        return "update_plan"

    @property
    def description(self) -> str:
        return (
            "Maintain a short TODO plan for a multi-step task. Provide the full "
            "list of steps (each with status pending | in_progress | completed) "
            "and an optional one-line explanation; call again to update "
            "progress. At most one step may be in_progress at a time. Use it to "
            "plan and track your own work on non-trivial tasks."
        )

    @property
    def read_only(self) -> bool:
        # No workspace / security side effects — benign, must not be gated.
        return True

    @property
    def plan(self) -> list[dict[str, str]]:
        """The current plan (for a UI to render)."""
        return list(self._plan)

    async def execute(self, **kwargs: Any) -> Any:
        raw = kwargs.get("plan")
        if not isinstance(raw, list) or not raw:
            return "Error: 'plan' must be a non-empty list of {step, status} items."

        steps: list[dict[str, str]] = []
        in_progress = 0
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                return f"Error: plan[{i}] must be an object with 'step' and 'status'."
            step = str(item.get("step", "")).strip()
            status = str(item.get("status", "")).strip()
            if not step:
                return f"Error: plan[{i}].step is required."
            if status not in _STATUSES:
                return (
                    f"Error: plan[{i}].status must be one of "
                    f"{', '.join(_STATUSES)} (got {status!r})."
                )
            if status == "in_progress":
                in_progress += 1
            steps.append({"step": step, "status": status})
        if in_progress > 1:
            return "Error: at most one step may be in_progress at a time."

        self._plan = steps
        return self._render(str(kwargs.get("explanation") or "").strip())

    def _render(self, explanation: str) -> str:
        done = sum(1 for s in self._plan if s["status"] == "completed")
        head = f"Plan updated ({done}/{len(self._plan)} done)"
        if explanation:
            head += f" — {explanation}"
        body = "\n".join(f"{_GLYPH[s['status']]} {s['step']}" for s in self._plan)
        return f"{head}\n{body}"
