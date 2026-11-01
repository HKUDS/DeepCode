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
    extra_args: tuple[str, ...] = (),
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

    loop_cli.main(
        ["make calc.add correct", "-w", str(workspace), "--trust", *extra_args]
    )

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
        "Blocked by the completion verifier after 3 rejected completion claims "
        "with no new changes in between."
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


# -- reviewer -----------------------------------------------------------------
# One scripted model plays both parts: the reviewer's session starts inside the
# agent's update_goal call, so its steps come right after that claim.

FIXED = "def add(a, b):\n    return a + b\n"


def _verdict(verdict: str, summary: str, *missing: str) -> tuple[str, dict]:
    requirement = {
        "requirement": "calc.add adds",
        "finding": "proved" if verdict == "pass" else "contradicted",
        "evidence": "read calc.py",
    }
    return (
        "submit_result",
        {
            "verdict": verdict,
            "summary": summary,
            "requirements": [requirement],
            "missing": list(missing),
        },
    )


def _reviewer_requests(provider: _ScriptedProvider) -> list[dict[str, Any]]:
    return [
        request
        for request in provider.requests
        if "independent Goal reviewer" in request["messages"][0]["content"]
    ]


def test_reviewer_rejects_then_passes_the_fixed_work(monkeypatch, tmp_path) -> None:
    goal, _, provider = _run(
        monkeypatch,
        tmp_path,
        [
            ("read", {"file_path": "calc.py"}),
            COMPLETE,
            # reviewer, first claim
            ("read", {"file_path": "calc.py"}),
            _verdict("fail", "add subtracts.", "calc.add returns a - b"),
            "Reviewed.",
            # agent
            ("write", {"file_path": "calc.py", "content": FIXED}),
            COMPLETE,
            # reviewer, second claim
            ("read", {"file_path": "calc.py"}),
            _verdict("pass", "add adds."),
            "Reviewed.",
        ],
        verifier="reviewer",
    )

    first, second = provider.goal_results()
    assert first["updated"] is False
    assert (
        "did not accept the claim: add subtracts." in first["verdict"]["justification"]
    )
    assert "- calc.add returns a - b" in first["verdict"]["justification"]
    assert second["updated"] is True
    assert (
        "passed the claim: add adds. (1 requirement(s) checked.)"
        in (second["verdict"]["justification"])
    )
    assert goal.status.value == "complete"


def test_reviewer_sees_the_claim_and_only_read_only_tools(
    monkeypatch, tmp_path
) -> None:
    goal, _, provider = _run(
        monkeypatch,
        tmp_path,
        [
            ("read", {"file_path": "calc.py"}),
            COMPLETE,
            _verdict("pass", "fine."),
            "Reviewed.",
        ],
        verifier="reviewer",
    )

    first = _reviewer_requests(provider)[0]
    task = next(
        m["content"]
        for m in first["messages"]
        if m["role"] == "user" and "Review this completion claim" in str(m["content"])
    )
    assert "make calc.add correct" in task
    assert "calc.add works." in task  # the agent's reason
    assert "read: calc.py" in task  # its evidence
    tools = {tool["function"]["name"] for tool in first["tools"]}
    assert "submit_result" in tools
    assert tools <= {"read", "grep", "glob", "bash", "submit_result"}
    assert goal.status.value == "complete"


def test_impossible_blocks_the_goal_at_once(monkeypatch, tmp_path) -> None:
    goal, outcome, provider = _run(
        monkeypatch,
        tmp_path,
        [
            ("read", {"file_path": "calc.py"}),
            COMPLETE,
            _verdict("impossible", "Needs a paid API key.", "API key"),
            "Reviewed.",
        ],
        verifier="reviewer",
    )

    (result,) = provider.goal_results()
    assert result["autoBlocked"] is True
    assert result["rejections"] == 1
    assert goal.status.value == "blocked"
    assert outcome.reason.startswith(
        "An independent review found the Goal cannot be met here: Needs a paid API key."
    )


def test_a_reviewer_without_a_verdict_blocks_the_goal_unverified(
    monkeypatch, tmp_path
) -> None:
    from core.harness.agents import goal_reviewer

    monkeypatch.setattr(goal_reviewer, "REVIEW_RETRY_DELAYS_SECONDS", (0.0, 0.0))
    goal, outcome, provider = _run(
        monkeypatch,
        tmp_path,
        [
            ("read", {"file_path": "calc.py"}),
            COMPLETE,
            # The reviewer forgets to submit, three times.
            "Looks fine to me.",
            "Still fine.",
            "Fine.",
            "Stopping here.",
        ],
        verifier="reviewer",
    )

    (result,) = provider.goal_results()
    assert result["unverified"] is True
    assert "gave no verdict (tried 3 times)" in result["verdict"]["justification"]
    assert "rejections" not in result  # not counted against the agent
    assert len(_reviewer_requests(provider)) == 3
    assert goal.status.value == "blocked"
    assert outcome.reason.startswith("The completion claim could not be verified")


def test_the_rule_check_rejects_before_any_review(monkeypatch, tmp_path) -> None:
    goal, _, provider = _run(
        monkeypatch,
        tmp_path,
        [("bash", {"command": CHECK}), COMPLETE, "Stopping here."],  # red check
        verifier="reviewer",
    )

    (result,) = provider.goal_results()
    assert "latest check failed" in result["verdict"]["justification"]
    assert _reviewer_requests(provider) == []
    assert goal.status.value != "complete"


def test_the_reviewer_runs_no_hooks(monkeypatch, tmp_path) -> None:
    # A hook the agent writes mid-Turn would run outside the sandbox.
    marker = f"\"{sys.executable}\" -c \"open('HOOK_RAN', 'w').write('x')\""
    hooks = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "", "hooks": [{"type": "command", "command": marker}]}
            ]
        }
    }
    _run(
        monkeypatch,
        tmp_path,
        [
            (
                "write",
                {"file_path": ".deepcode/hooks.json", "content": json.dumps(hooks)},
            ),
            ("read", {"file_path": "calc.py"}),
            COMPLETE,
            ("glob", {"pattern": "*.py"}),
            _verdict("pass", "fine."),
            "Reviewed.",
        ],
        verifier="reviewer",
    )

    assert not (tmp_path / "proj" / "HOOK_RAN").exists()


class _MeteredProvider(_ScriptedProvider):
    """Every response reports 1000 tokens."""

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        response = await super().chat_with_retry(**kwargs)
        response.usage = {
            "prompt_tokens": 900,
            "completion_tokens": 100,
            "total_tokens": 1000,
        }
        return response


def test_the_reviews_tokens_count_toward_the_goal(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sys.modules[__name__], "_ScriptedProvider", _MeteredProvider)
    goal, _, provider = _run(
        monkeypatch,
        tmp_path,
        [
            ("read", {"file_path": "calc.py"}),
            COMPLETE,
            ("read", {"file_path": "calc.py"}),
            _verdict("pass", "fine."),
            "Reviewed.",
        ],
        verifier="reviewer",
    )

    reviewer_calls = len(_reviewer_requests(provider))
    assert reviewer_calls == 3
    ledger = [
        json.loads(line)
        for line in next((tmp_path / "sessions").glob("*/goal.jsonl"))
        .read_text()
        .splitlines()
    ]
    charged = [entry for entry in ledger if entry.get("reason") == "completion review"]
    assert len(charged) == 1
    assert "turnId" not in charged[0] or charged[0]["turnId"] is None
    assert goal.tokens_used >= reviewer_calls * 1000
    assert goal.status.value == "complete"


def test_a_file_change_between_rejections_restarts_the_count(
    monkeypatch, tmp_path
) -> None:
    bad = ("read", {"file_path": "missing.py"})  # a failed last step
    goal, _, provider = _run(
        monkeypatch,
        tmp_path,
        [
            bad,
            COMPLETE,
            ("write", {"file_path": "notes.txt", "content": "progress"}),
            bad,
            COMPLETE,  # rejected, but files changed since: count restarts
            bad,
            COMPLETE,
            bad,
            COMPLETE,  # third in a row without a change: blocked
        ],
    )

    results = provider.goal_results()
    assert [r["rejections"] for r in results] == [1, 1, 2, 3]
    assert results[-1]["autoBlocked"] is True
    assert goal.status.value == "blocked"


def test_a_turn_that_keeps_learning_nothing_blocks_the_goal(
    monkeypatch, tmp_path
) -> None:
    """The 2026-11 runaway: two commands alternated for 741 steps in one Turn."""
    from core.agent_runtime.evidence_ledger import STALE_RESULT_STOP

    loop = [("bash", {"command": CHECK}), ("read", {"file_path": "calc.py"})] * 40
    goal, outcome, provider = _run(monkeypatch, tmp_path, loop)

    assert provider.calls == STALE_RESULT_STOP + 2
    assert goal.status.value == "blocked"
    assert "nothing new" in outcome.reason


class _TotallessProvider(_ScriptedProvider):
    """Reports prompt and completion tokens but a total of 0."""

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        response = await super().chat_with_retry(**kwargs)
        response.usage = {
            "prompt_tokens": 900,
            "completion_tokens": 100,
            "total_tokens": 0,
        }
        return response


@pytest.mark.parametrize("metered", [_MeteredProvider, _TotallessProvider])
def test_a_token_budget_stops_the_goal_inside_one_turn(
    monkeypatch, tmp_path, metered
) -> None:
    monkeypatch.setattr(sys.modules[__name__], "_ScriptedProvider", metered)
    # Every call is new, so only the budget can stop this Turn.
    steps = [("bash", {"command": f"echo {n}"}) for n in range(20)]
    goal, _, provider = _run(
        monkeypatch, tmp_path, steps, extra_args=("--token-budget", "3500")
    )

    # 1000 tokens a call: 4000 >= 3500 after the fourth.
    assert provider.calls == 4
    assert goal.status.value == "budget_limited"
    assert goal.tokens_used == 4000
