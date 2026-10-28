"""The ``rule`` completion verifier through the real Goal path.

loop CLI -> service -> AgentSession -> update_goal -> GoalExtension, with a
scripted model that answers instantly: the Turn record must still include
every step the agent reported before the claim (the settle step), or these
verdicts would depend on timing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.usefixtures("shared_cli_service")

from cli import loop_cli
from core import agent_setup
from core.providers.base import LLMResponse, ToolCallRequest


class _ScriptedProvider:
    """Replays model steps; a step is text, one tool call, or a list of them."""

    def __init__(self, steps: list[Any]) -> None:
        self.steps = list(steps)
        self.calls = 0
        self.requests: list[dict[str, Any]] = []

    def get_default_model(self) -> str:
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        self.requests.append(kwargs)
        self.calls += 1
        step = self.steps.pop(0) if self.steps else "Done."
        if isinstance(step, str):
            return LLMResponse(content=step, finish_reason="stop")
        calls = step if isinstance(step, list) else [step]
        return LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(id=f"call-{self.calls}-{n}", name=name, arguments=args)
                for n, (name, args) in enumerate(calls)
            ],
            finish_reason="tool_calls",
        )

    def goal_results(self) -> list[dict[str, Any]]:
        """Every update_goal result the model saw, in order."""

        names: dict[str, str] = {}
        results = []
        for message in self.requests[-1]["messages"]:
            for call in message.get("tool_calls") or ():
                names[call["id"]] = call["function"]["name"]
            if (
                message.get("role") == "tool"
                and names.get(message.get("tool_call_id")) == "update_goal"
            ):
                results.append(json.loads(message["content"]))
        return results


class _Profile:
    model = "fake-model"


COMPLETE = ("update_goal", {"status": "complete", "reason": "calc.add works."})
# -B: the fix keeps calc.py's size, so a cached .pyc from the red run written
# in the same second would otherwise be reused.
CHECK = f'"{sys.executable}" -B -c "from calc import add; assert add(2, 3) == 5"'


def _run(
    monkeypatch,
    tmp_path: Path,
    steps: list[Any],
    *,
    verifier: str = "rule",
) -> tuple[Any, Any, _ScriptedProvider]:
    from core.sessions import SessionStore, ThreadGoalStore

    workspace = tmp_path / "proj"
    workspace.mkdir()
    (workspace / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    home = tmp_path / "home"
    home.mkdir()
    (home / "deepcode_config.json").write_text(
        json.dumps(
            {
                "goal": {"completionVerifier": verifier},
                # The scripted model runs commands unattended.
                "security": {"accessPreset": "full_access", "sandbox": False},
            }
        )
    )
    provider = _ScriptedProvider(steps)
    monkeypatch.setattr(
        agent_setup,
        "get_workflow_provider",
        lambda **_kwargs: (provider, _Profile()),
    )
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    monkeypatch.setenv("DEEPCODE_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setattr("core.sessions.store._DEFAULT_STORE", None)

    loop_cli.main(["make calc.add correct", "-w", str(workspace), "--trust"])

    sessions = SessionStore(tmp_path / "sessions")
    session_id = sessions.list_sessions()[0].session_id
    record = ThreadGoalStore(sessions).read_record(session_id)
    return record.goal, record.outcome, provider


def test_red_fix_green_completes(monkeypatch, tmp_path) -> None:
    fixed = "def add(a, b):\n    return a + b\n"
    goal, _, provider = _run(
        monkeypatch,
        tmp_path,
        [
            ("bash", {"command": CHECK}),  # red
            ("write", {"file_path": "calc.py", "content": fixed}),
            ("bash", {"command": CHECK}),  # green
            COMPLETE,
        ],
    )

    assert goal.status.value == "complete"
    assert [result.get("updated") for result in provider.goal_results()] == [True]


def test_early_failed_read_then_work_completes(monkeypatch, tmp_path) -> None:
    goal, _, provider = _run(
        monkeypatch,
        tmp_path,
        [
            ("read", {"file_path": "calculator.py"}),  # mistyped: fails
            ("read", {"file_path": "calc.py"}),
            COMPLETE,
        ],
    )

    assert goal.status.value == "complete"
    assert len(provider.goal_results()) == 1


def test_get_goal_alone_is_rejected_then_blocked_by_the_verifier(
    monkeypatch, tmp_path
) -> None:
    goal, outcome, provider = _run(
        monkeypatch,
        tmp_path,
        [("get_goal", {}), COMPLETE, COMPLETE, COMPLETE],
    )

    results = provider.goal_results()
    assert [result.get("rejections") for result in results] == [1, 2, 3]
    assert "no finished work" in results[0]["verdict"]["justification"]
    assert results[-1]["autoBlocked"] is True
    assert goal.status.value == "blocked"
    assert outcome.reason.startswith(
        "Blocked by the completion verifier after 3 rejected completion claims."
    )


def test_failure_after_twelve_steps_is_caught_and_resolved_by_later_work(
    monkeypatch, tmp_path
) -> None:
    steps: list[Any] = [("read", {"file_path": "calc.py"}) for _ in range(12)]
    steps += [
        ("read", {"file_path": "missing_final_check.py"}),
        COMPLETE,
        ("read", {"file_path": "calc.py"}),
        COMPLETE,
    ]
    goal, _, provider = _run(monkeypatch, tmp_path, steps)

    first, second = provider.goal_results()
    assert first["updated"] is False
    assert (
        "latest step failed: read `missing_final_check.py`"
        in first["verdict"]["justification"]
    )
    assert second["updated"] is True
    assert goal.status.value == "complete"


def test_a_claim_beside_its_own_check_points_at_that_check(
    monkeypatch, tmp_path
) -> None:
    fixed = "def add(a, b):\n    return a + b\n"
    goal, _, provider = _run(
        monkeypatch,
        tmp_path,
        [
            ("bash", {"command": CHECK}),  # red
            ("write", {"file_path": "calc.py", "content": fixed}),
            [("bash", {"command": CHECK}), COMPLETE],  # green, same step
            COMPLETE,
        ],
    )

    first, second = provider.goal_results()
    assert first["updated"] is False
    assert "same step as this claim" in first["verdict"]["justification"]
    assert second["updated"] is True
    assert goal.status.value == "complete"


def test_off_records_the_claim_without_verifying(monkeypatch, tmp_path) -> None:
    goal, _, provider = _run(
        monkeypatch,
        tmp_path,
        [("get_goal", {}), COMPLETE],
        verifier="off",
    )

    assert goal.status.value == "complete"
    assert "verdict" not in provider.goal_results()[0]
