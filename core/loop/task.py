"""LoopTask — the autonomous, test-driven Ralph loop (P3 centrepiece).

Given a goal and a test command, run rounds until the tests are green (or a
circuit breaker fires). Each round:

1. **reset + handoff** — build a *fresh* :class:`AgentSession` (so context
   never grows without bound) seeded with the goal, an accumulated handoff
   note (what's been tried and why it failed — the failure ratchet), and the
   latest real test output;
2. run the agent turn (it edits files on disk, which persist across rounds);
3. **checkpoint** the workspace with a shadow-git snapshot;
4. **test backpressure** — actually run the tests and read the exit code;
5. record the round to durable :class:`LoopState` and let the declarative
   :func:`decide` policy call continue / stop.

Everything load-bearing is assembled from P0–P2: the round is a normal
``AgentSession`` (P1/P2 kernel + tools + memory), the checkpoint is the P2.d
``Snapshotter``, the truth is a real test run. The round runner and test
runner are injected, so the whole orchestration is unit-testable offline with
a scripted "agent".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from core.agent_setup import build_agent_session
from core.events import UserInput
from core.harness.snapshot import Snapshotter
from core.loop.backpressure import TestResult, run_tests
from core.loop.policy import decide
from core.loop.state import STATUS_ERROR, STATUS_EXHAUSTED, LoopState, RoundRecord

# (workspace, prompt) -> (stop_reason, final_text)
RoundRunner = Callable[[str, str], Awaitable[tuple[str, str]]]
# (workspace, test_command) -> TestResult
TestRunner = Callable[[str, str], TestResult]
# a progress hook: (state, latest_round, test_result) -> None
EventHook = Callable[[LoopState, RoundRecord, TestResult], None]

_HANDOFF_MAX = 2400


@dataclass
class LoopResult:
    state: LoopState

    @property
    def succeeded(self) -> bool:
        from core.loop.state import STATUS_SUCCEEDED

        return self.state.status == STATUS_SUCCEEDED


def _make_default_round_runner(model: str | None, max_iterations: int) -> RoundRunner:
    """A round runner that runs one turn of a fresh AgentSession per round."""

    async def _run(workspace: str, prompt: str) -> tuple[str, str]:
        session, _model, _engine = build_agent_session(
            workspace=workspace, model=model, max_iterations=max_iterations
        )
        final_text = ""
        stop_reason = "completed"
        async for event in session.run_stream(UserInput(text=prompt)):
            if event.msg.type == "task_complete":
                final_text = event.msg.final_text or ""
                stop_reason = event.msg.stop_reason
        return stop_reason, final_text

    return _run


class LoopTask:
    """Run a goal to completion across autonomous, test-checked rounds."""

    def __init__(
        self,
        *,
        goal: str,
        workspace: str,
        test_command: str = "",
        model: str | None = None,
        max_rounds: int = 8,
        max_iterations: int = 40,
        round_runner: RoundRunner | None = None,
        test_runner: TestRunner = run_tests,
        on_event: EventHook | None = None,
    ) -> None:
        self.goal = goal
        self.workspace = workspace
        self.test_command = test_command
        self.max_rounds = max(1, max_rounds)
        self._round_runner = round_runner or _make_default_round_runner(
            model, max_iterations
        )
        self._test_runner = test_runner
        self._on_event = on_event

    async def run(self) -> LoopResult:
        state = LoopState(
            goal=self.goal,
            workspace=self.workspace,
            test_command=self.test_command,
            max_rounds=self.max_rounds,
        )
        state.save()
        snapshotter = (
            Snapshotter(self.workspace) if Snapshotter.git_available() else None
        )
        handoff = ""
        last_test: TestResult | None = None

        for index in range(self.max_rounds):
            repeat = _repeated_failures(state, last_test)
            prompt = self._round_prompt(index, handoff, last_test, repeat)
            try:
                stop_reason, final_text = await self._round_runner(
                    self.workspace, prompt
                )
            except Exception as exc:  # noqa: BLE001 - a bad round must not crash the loop
                state.finish(STATUS_ERROR, f"round {index} raised: {exc}")
                state.save()
                return LoopResult(state)

            snapshot_id = ""
            if snapshotter is not None:
                try:
                    snapshot_id = snapshotter.snapshot(f"loop round {index}").id
                except Exception:  # noqa: BLE001 - checkpointing is best-effort
                    snapshot_id = ""

            test = self._test_runner(self.workspace, self.test_command)
            record = RoundRecord(
                index=index,
                agent_stop_reason=stop_reason,
                tests_passed=test.passed if test.ran else None,
                test_summary=test.summary,
                test_signature=test.signature,
                snapshot_id=snapshot_id,
                handoff=_summarize_round(index, final_text, test),
            )
            state.add_round(record)
            state.save()
            if self._on_event is not None:
                self._on_event(state, record, test)

            decision = decide(state)
            if decision.stop:
                state.finish(decision.status, decision.reason)
                state.save()
                return LoopResult(state)

            handoff = _accumulate_handoff(handoff, record)
            last_test = test

        # Defensive: the policy caps at max_rounds, so this is only reached if
        # the loop body never set a terminal status.
        if not state.is_terminal:
            state.finish(STATUS_EXHAUSTED, "loop ended without passing tests")
            state.save()
        return LoopResult(state)

    # -- prompt construction ---------------------------------------------------

    def _round_prompt(
        self,
        index: int,
        handoff: str,
        last_test: TestResult | None,
        repeat: int,
    ) -> str:
        parts = [f"Goal: {self.goal}"]
        if self.test_command:
            parts.append(
                f"The project is verified by running: `{self.test_command}`. "
                "Your work is only done when that command exits successfully."
            )
        if index == 0:
            parts.append(
                "Implement what the goal asks. Create the files and the tests, "
                "then run the test command yourself to confirm it passes."
            )
        else:
            if handoff:
                parts.append("Progress so far (previous rounds):\n" + handoff)
            if last_test is not None and last_test.ran and not last_test.passed:
                parts.append(
                    "The tests are currently FAILING. Here is the latest output:\n"
                    "```\n" + last_test.output_tail + "\n```\n"
                    "Diagnose the failure and fix the code so the tests pass. "
                    "Change the source, not the tests (unless the tests are "
                    "clearly wrong)."
                )
            # Failure ratchet: the same failure survived your last change(s).
            # Escalate so the agent abandons the approach instead of nudging it.
            if repeat >= 1:
                parts.append(
                    f"IMPORTANT: this exact failure has now persisted through "
                    f"{repeat + 1} attempts — your recent changes did NOT affect "
                    "it. Stop refining the current approach. Re-read the failing "
                    "test to understand what it truly requires, and take a "
                    "different implementation strategy."
                )
        return "\n\n".join(parts)


def _repeated_failures(state: LoopState, last_test: TestResult | None) -> int:
    """How many *earlier* consecutive rounds share the pending failure signature.

    0 means the last failure is new; N>0 means the same failure survived N
    prior rounds — the signal the failure ratchet escalates on.
    """
    if last_test is None or not last_test.signature:
        return 0
    count = 0
    # rounds[-1] is the round that produced last_test; count identical ones before it.
    for record in reversed(state.rounds[:-1]):
        if record.test_signature == last_test.signature:
            count += 1
        else:
            break
    return count


def _summarize_round(index: int, final_text: str, test: TestResult) -> str:
    reply = (final_text or "").strip().splitlines()
    head = reply[0][:160] if reply else "(no summary)"
    verdict = (
        "tests passed"
        if test.ran and test.passed
        else ("tests failed" if test.ran else "tests not run")
    )
    return f"round {index}: {head} — {verdict}"


def _accumulate_handoff(existing: str, record: RoundRecord) -> str:
    combined = (
        (existing + "\n" + record.handoff).strip() if existing else record.handoff
    )
    # Keep the handoff bounded — carry the most recent rounds (the tail).
    if len(combined) > _HANDOFF_MAX:
        combined = "…\n" + combined[-_HANDOFF_MAX:]
    return combined
