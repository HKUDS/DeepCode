"""A Goal's checklist: proposed by the agent, confirmed by the user, run by DeepCode.

Also the history of completion reviews the Goal keeps.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from core.agent_runtime.goal_checks import GoalCheckSpec, run_checks
from core.agent_runtime.goal_runtime import GoalRuntimeContext, GoalRuntimeRouter
from core.agent_runtime.goal_verifier import (
    CompletionGate,
    GoalCompletionRequest,
    GoalEvidenceItem,
    GoalVerdict,
    VerificationUnavailable,
    review_record,
)
from core.application.errors import ConflictError, InvalidArgumentError
from core.domain.thread_goal import GoalCheck, ThreadGoal
from core.domain.turn import Turn, TurnStatus
from core.domain.common import utc_now
from core.sessions import SessionStore
from core.sessions.thread_goal_store import (
    GOAL_LEDGER_FILENAME,
    GOAL_REVIEWS_FILENAME,
    ThreadGoalStore,
)

PY = sys.executable


def _spec(code: str, requirement: str = "it works") -> GoalCheckSpec:
    return GoalCheckSpec(requirement=requirement, command=(PY, "-c", code))


# --- running checks -------------------------------------------------------


@pytest.mark.asyncio
async def test_checks_run_without_a_shell_and_report_each_exit(tmp_path) -> None:
    runs = await run_checks(
        (
            _spec("print('ok')"),
            _spec("import sys; print('boom'); sys.exit(3)", "fails"),
            GoalCheckSpec(requirement="left to the reviewer", command=()),
            # A pipe is just an argument without a shell.
            GoalCheckSpec(requirement="pipe", command=(PY, "-c", "print(1)", "|", "x")),
        ),
        cwd=str(tmp_path),
    )
    assert [(run.requirement, run.exit_code) for run in runs] == [
        ("it works", 0),
        ("fails", 3),
        ("pipe", 0),
    ]
    assert runs[0].passed and runs[0].output.strip() == "ok"
    assert runs[1].output.strip() == "boom"
    assert runs[1].describe().endswith("exited 3")


@pytest.mark.asyncio
async def test_a_check_runs_in_the_workspace(tmp_path) -> None:
    (tmp_path / "marker.txt").write_text("here")
    (run,) = await run_checks(
        (_spec("print(open('marker.txt').read())"),), cwd=str(tmp_path)
    )
    assert run.passed and run.output.strip() == "here"


@pytest.mark.asyncio
async def test_a_slow_check_times_out_and_later_ones_still_run(tmp_path) -> None:
    runs = await run_checks(
        (_spec("import time; time.sleep(30)", "slow"), _spec("pass", "quick")),
        cwd=str(tmp_path),
        timeout_seconds=0.5,
    )
    assert runs[0].exit_code is None and "timed out" in runs[0].problem
    assert not runs[0].passed
    assert runs[1].passed


@pytest.mark.asyncio
async def test_checks_stop_starting_once_their_total_time_is_used(tmp_path) -> None:
    runs = await run_checks(
        (_spec("import time; time.sleep(30)", "slow"), _spec("pass", "next")),
        cwd=str(tmp_path),
        timeout_seconds=10,
        total_seconds=0.5,
    )
    assert "timed out" in runs[0].problem
    assert runs[1].exit_code is None and "was not run" in runs[1].problem


@pytest.mark.asyncio
async def test_a_missing_program_fails_the_check(tmp_path) -> None:
    (run,) = await run_checks(
        (GoalCheckSpec(requirement="x", command=("no-such-program-xyz",)),),
        cwd=str(tmp_path),
        sandbox_enabled=False,
    )
    assert run.exit_code is None and "could not start" in run.problem
    # Inside the sandbox the wrapper starts, and the command fails there.
    (sandboxed,) = await run_checks(
        (GoalCheckSpec(requirement="x", command=("no-such-program-xyz",)),),
        cwd=str(tmp_path),
    )
    assert not sandboxed.passed


@pytest.mark.asyncio
async def test_a_check_that_leaves_a_process_behind_still_reports_its_exit(
    tmp_path,
) -> None:
    marker = tmp_path / "left-behind"
    (run,) = await run_checks(
        (
            GoalCheckSpec(
                requirement="x",
                command=("sh", "-c", f"(sleep 5; touch {marker}) & echo hi; exit 0"),
            ),
        ),
        cwd=str(tmp_path),
        timeout_seconds=4,
    )
    assert run.exit_code == 0 and run.output.strip() == "hi"
    assert run.seconds < 4
    await asyncio.sleep(6)
    # What it left running was ended with it.
    assert not marker.exists()


@pytest.mark.asyncio
async def test_a_check_has_the_agents_sandbox(tmp_path) -> None:
    from core.harness.sandbox import fences_writes

    if not fences_writes():
        pytest.skip("no sandbox backend here")
    outside = Path.home() / ".deepcode-check-sandbox-probe"
    try:
        (run,) = await run_checks(
            (GoalCheckSpec("x", ("sh", "-c", f"echo x > {outside}")),),
            cwd=str(tmp_path),
            sandbox_enabled=True,
        )
        assert not run.passed and not outside.exists()
    finally:
        outside.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_only_the_end_of_long_output_is_kept(tmp_path) -> None:
    (run,) = await run_checks(
        (_spec("print('a' * 200000); print('THE END')"),), cwd=str(tmp_path)
    )
    assert run.passed
    assert len(run.output) <= 4_000 and run.output.rstrip().endswith("THE END")


# --- the completion gate --------------------------------------------------


def _request(*checks: GoalCheckSpec, evidence_status: str = "completed"):
    return GoalCompletionRequest(
        objective="Make add correct.",
        reason="Fixed and tested.",
        evidence=(
            GoalEvidenceItem(
                kind="command_execution", status=evidence_status, summary="pytest"
            ),
        ),
        checks=checks,
    )


@pytest.mark.asyncio
async def test_with_nothing_to_check_the_gate_checks_nothing(tmp_path) -> None:
    verdict = await CompletionGate(rule=False, workspace=str(tmp_path))(_request())
    assert verdict.confirmed and verdict.stage == "none"


@pytest.mark.asyncio
async def test_a_failed_check_turns_the_claim_back_with_its_output(tmp_path) -> None:
    gate = CompletionGate(rule=False, workspace=str(tmp_path))
    verdict = await gate(
        _request(
            _spec("pass", "builds"),
            _spec("print('3 failed'); raise SystemExit(1)", "tests pass"),
        )
    )
    assert not verdict.confirmed and verdict.stage == "checks"
    assert "1 did not pass" in verdict.justification
    assert "tests pass" in verdict.justification and "3 failed" in verdict.justification
    assert [run.exit_code for run in verdict.check_runs] == [0, 1]


@pytest.mark.asyncio
async def test_the_rule_check_runs_before_the_checks(tmp_path) -> None:
    ran = []

    async def run(checks):
        ran.append(checks)
        return ()

    gate = CompletionGate(rule=True, run=run)
    verdict = await gate(_request(_spec("pass"), evidence_status="failed"))
    assert not verdict.confirmed and verdict.stage == "rule"
    assert ran == []


@pytest.mark.asyncio
async def test_the_reviewer_hears_how_the_checks_went(tmp_path) -> None:
    seen = []

    async def review(request):
        seen.append(request)
        return GoalVerdict(confirmed=True, justification="ok", stage="reviewer")

    gate = CompletionGate(rule=True, review=review, workspace=str(tmp_path))
    verdict = await gate(_request(_spec("pass", "builds")))
    assert verdict.confirmed and verdict.stage == "reviewer"
    assert [run.requirement for run in seen[0].check_runs] == ["builds"]
    assert [run.requirement for run in verdict.check_runs] == ["builds"]


@pytest.mark.asyncio
async def test_a_review_that_cannot_run_keeps_the_check_runs(tmp_path) -> None:
    async def review(request):
        raise VerificationUnavailable("provider down", tokens=7)

    gate = CompletionGate(rule=False, review=review, workspace=str(tmp_path))
    with pytest.raises(VerificationUnavailable) as raised:
        await gate(_request(_spec("pass", "builds")))
    assert [run.requirement for run in raised.value.check_runs] == ["builds"]


def test_the_reviewer_is_given_the_confirmed_checklist() -> None:
    from core.agent_runtime.goal_checks import GoalCheckRun
    from core.harness.agents.goal_reviewer import review_task

    task = review_task(
        GoalCompletionRequest(
            objective="Fix the cart.",
            reason="Done.",
            evidence=(),
            checks=(
                GoalCheckSpec("Cart tests pass", ("pytest", "-q")),
                GoalCheckSpec("Zero stock removes the item", ()),
            ),
            check_runs=(GoalCheckRun("Cart tests pass", ("pytest", "-q"), 0, "", 1.0),),
        )
    )
    assert "The user confirmed this checklist" in task
    assert "- Zero stock removes the item" in task
    assert "(command: `pytest -q`)" in task
    assert "DeepCode itself ran" in task


# --- the router keeps a history ------------------------------------------


class _Handler:
    def __init__(self) -> None:
        self.reviews: list[dict] = []
        self.updates: list[str] = []

    def read_goal(self, context):
        return {"canUpdate": True}

    def update_goal(self, context, *, status, reason, expected_objective=None):
        self.updates.append(status)
        return {"goalId": context.goal_id, "status": status}

    def record_verification_tokens(self, context, tokens):
        pass

    def record_review(self, context, review):
        self.reviews.append(review)

    def goal_turn_count(self, context):
        return 3

    def user_declined_in_turn(self, context):
        return False

    def completion_request(self, context, *, reason):
        return _request()


class _Fixed:
    def __init__(self, outcome) -> None:
        self.outcome = outcome

    async def __call__(self, request):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _router(handler, verifier) -> GoalRuntimeRouter:
    router = GoalRuntimeRouter()
    router.configure(handler)
    router.configure_completion_verifier(verifier)
    router.activate(GoalRuntimeContext("t", "goal_1", "turn_1"))
    return router


@pytest.mark.asyncio
async def test_each_review_is_kept_and_an_unchecked_claim_is_not() -> None:
    handler = _Handler()
    rejected = GoalVerdict(confirmed=False, justification="no", stage="rule")
    await _router(handler, _Fixed(rejected)).request(status="complete", reason="x")
    passed = GoalVerdict(confirmed=True, justification="ok", stage="reviewer", tokens=5)
    await _router(handler, _Fixed(passed)).request(status="complete", reason="x")
    from core.harness.agents.goal_reviewer import ReviewFailed

    await _router(handler, _Fixed(ReviewFailed("down", tokens=3))).request(
        status="complete", reason="x"
    )
    unchecked = GoalVerdict(confirmed=True, justification="", stage="none")
    result = await _router(handler, _Fixed(unchecked)).request(
        status="complete", reason="x"
    )
    assert [(r["stage"], r["result"]) for r in handler.reviews] == [
        ("rule", "rejected"),
        ("reviewer", "passed"),
        ("reviewer", "unavailable"),
    ]
    assert handler.reviews[1]["tokens"] == 5
    assert handler.reviews[2]["justification"] == "down"
    assert "verdict" not in result


@pytest.mark.asyncio
async def test_a_history_that_cannot_be_written_never_hides_the_decision() -> None:
    class Broken(_Handler):
        def record_review(self, context, review):
            raise OSError("disk full")

    handler = Broken()
    passed = GoalVerdict(confirmed=True, justification="ok", stage="checks")
    result = await _router(handler, _Fixed(passed)).request(
        status="complete", reason="x"
    )
    assert result["verdict"]["confirmed"] is True
    assert handler.updates == ["complete"]


def test_a_review_record_keeps_only_the_end_of_long_output() -> None:
    from core.agent_runtime.goal_checks import GoalCheckRun

    verdict = GoalVerdict(
        confirmed=False,
        justification="x" * 5000,
        stage="checks",
        check_runs=(GoalCheckRun("r", ("c",), 1, "y" * 3000 + "END", 1.0),),
    )
    record = review_record(
        goal_id="goal_1", turn_id="turn_1", result="rejected", verdict=verdict
    )
    assert len(record["justification"]) <= 2000
    assert record["checks"][0]["output"].endswith("END")
    assert len(record["checks"][0]["output"]) <= 1500
    json.dumps(record)


# --- the Goal and its store ----------------------------------------------


class _Turns:
    def __init__(self) -> None:
        self.turns: dict[str, Turn] = {}

    def read(self, turn_id):
        from types import SimpleNamespace

        return SimpleNamespace(turn=self.turns[turn_id], items=())

    def list_for_goal(self, thread_id, goal_id):
        return ()

    def executing_for_thread(self, thread_id):
        return None

    def active_for_thread(self, thread_id):
        return None


def _goals(tmp_path):
    from core.application.goal_extension import GoalExtension

    sessions = SessionStore(tmp_path / "sessions", use_index=False)
    session = sessions.create_session(title="checks")
    store = ThreadGoalStore(sessions)
    notified: list = []
    goals = GoalExtension(
        store, _Turns(), update_sink=lambda thread_id, goal: notified.append(goal)
    )
    return goals, store, sessions, session.session_id, notified


def _running_turn(goals, thread_id, goal_id) -> GoalRuntimeContext:
    turn = Turn(
        thread_id=thread_id,
        ordinal=1,
        prompt="work",
        goal_id=goal_id,
        status=TurnStatus.RUNNING,
        started_at=utc_now(),
    )
    goals.turns.turns[turn.id] = turn
    return GoalRuntimeContext(thread_id, goal_id, turn.id)


def test_the_agent_proposes_and_the_user_confirms(tmp_path) -> None:
    goals, store, _sessions, thread_id, notified = _goals(tmp_path)
    goal = goals.create(thread_id, objective="Fix the cart", start=False)
    context = _running_turn(goals, thread_id, goal.id)
    proposal = (
        GoalCheck("Cart tests pass", ("python", "-m", "pytest", "-q")),
        GoalCheck("Zero stock removes the item"),
    )

    result = goals.propose_checks(context, proposal)
    assert result["proposed"] == 2
    current = goals.read(thread_id)
    assert current.proposed_checks == proposal and current.checks == ()
    assert "Your proposed checklist is waiting" in goals._continuation_prompt(current)

    confirmed = goals.confirm_checks(
        thread_id, expected_goal_id=goal.id, checks=current.proposed_checks
    )
    assert confirmed.checks == proposal and confirmed.proposed_checks == ()
    assert "`python -m pytest -q`" in goals._continuation_prompt(confirmed)
    assert goals.completion_request(context, reason="x").checks[0].command == (
        "python",
        "-m",
        "pytest",
        "-q",
    )
    # Read back from the ledger after a restart.
    assert (
        ThreadGoalStore(SessionStore(tmp_path / "sessions", use_index=False))
        .read(thread_id)
        .checks
        == proposal
    )
    assert notified[-1].checks == proposal

    removed = goals.confirm_checks(thread_id, expected_goal_id=goal.id, checks=())
    assert removed.checks == ()


def test_a_proposal_can_be_dismissed_and_never_replaces_confirmed_checks(
    tmp_path,
) -> None:
    goals, _store, _sessions, thread_id, _ = _goals(tmp_path)
    goal = goals.create(
        thread_id,
        objective="Fix the cart",
        checks=(GoalCheck("tests", ("pytest",)),),
        start=False,
    )
    context = _running_turn(goals, thread_id, goal.id)
    goals.propose_checks(context, (GoalCheck("something else", ("true",)),))
    current = goals.read(thread_id)
    assert current.checks == (GoalCheck("tests", ("pytest",)),)
    dismissed = goals.dismiss_proposed_checks(thread_id, expected_goal_id=goal.id)
    assert dismissed.proposed_checks == ()
    assert dismissed.checks == current.checks


def test_a_goal_without_checks_asks_for_a_proposal(tmp_path) -> None:
    goals, _store, _sessions, thread_id, _ = _goals(tmp_path)
    goal = goals.create(thread_id, objective="Fix the cart", start=False)
    assert "call propose_goal_checks" in goals._continuation_prompt(goal)


def test_invalid_checks_are_refused(tmp_path) -> None:
    goals, _store, _sessions, thread_id, _ = _goals(tmp_path)
    goal = goals.create(thread_id, objective="Fix the cart", start=False)
    with pytest.raises(ValueError):
        GoalCheck("", ("x",))
    with pytest.raises(ValueError):
        GoalCheck("r", ("",))
    with pytest.raises(InvalidArgumentError):
        goals.confirm_checks(
            thread_id,
            expected_goal_id=goal.id,
            checks=tuple(GoalCheck(f"r{n}", ("x",)) for n in range(13)),
        )
    with pytest.raises(ConflictError):
        goals.confirm_checks(thread_id, expected_goal_id="goal_other", checks=())


def test_a_finished_turn_cannot_propose(tmp_path) -> None:
    goals, _store, _sessions, thread_id, _ = _goals(tmp_path)
    goal = goals.create(thread_id, objective="Fix the cart", start=False)
    context = _running_turn(goals, thread_id, goal.id)
    goals.turns.turns[context.turn_id] = Turn(
        thread_id=thread_id,
        ordinal=1,
        prompt="work",
        goal_id=goal.id,
        status=TurnStatus.COMPLETED,
        started_at=utc_now(),
        completed_at=utc_now(),
    )
    with pytest.raises(ConflictError):
        goals.propose_checks(context, (GoalCheck("tests", ("pytest",)),))


def test_a_ledger_without_checks_reads_and_writes_as_before(tmp_path) -> None:
    goals, _store, sessions, thread_id, _ = _goals(tmp_path)
    goals.create(thread_id, objective="Fix the cart", start=False)
    ledger = sessions.root / thread_id / GOAL_LEDGER_FILENAME
    entry = json.loads(ledger.read_text().splitlines()[-1])
    assert "checks" not in entry["goal"] and "proposedChecks" not in entry["goal"]


def test_reviews_are_kept_per_goal_and_unreadable_lines_skipped(tmp_path) -> None:
    goals, store, sessions, thread_id, notified = _goals(tmp_path)
    goal = goals.create(thread_id, objective="Fix the cart", start=False)
    context = GoalRuntimeContext(thread_id, goal.id, "turn_1")
    for result in ("rejected", "passed"):
        goals.record_review(
            context,
            review_record(
                goal_id=goal.id,
                turn_id="turn_1",
                result=result,
                verdict=GoalVerdict(
                    confirmed=result == "passed", justification=result, stage="checks"
                ),
            ),
        )
    path = sessions.root / thread_id / GOAL_REVIEWS_FILENAME
    with path.open("a") as handle:
        handle.write("not json\n")
        handle.write(json.dumps({"schemaVersion": 1, "goalId": "goal_other"}) + "\n")
    reviews = goals.read_reviews(thread_id, goals.read(thread_id))
    assert [review["result"] for review in reviews] == ["rejected", "passed"]
    assert (
        goals.read_reviews(thread_id, goals.read(thread_id), limit=1)[0]["result"]
        == "passed"
    )
    assert goals.read_reviews(thread_id, None) == ()
    # Each review publishes the Goal again, so open views pick it up.
    assert notified[-1].id == goal.id
    # The Goal ledger itself is untouched by reviews.
    assert store.read(thread_id) == goals.read(thread_id)


# --- the app server ---------------------------------------------------------


def test_the_app_server_confirms_and_dismisses_checks(tmp_path) -> None:
    from app_server.connection import ConnectionState
    from app_server.dispatcher import Dispatcher, InvalidParams, Params
    from core.application import DeepCodeApplication

    from core.domain.project import TrustState

    workspace = tmp_path / "w"
    workspace.mkdir()
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_store=SessionStore(tmp_path / "sessions", use_index=False),
    )
    project = application.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
    thread_id = application.threads.start(project.id, title="api").id
    dispatcher = Dispatcher(application, ConnectionState(application.broker))
    created = dispatcher._thread_goal_set(
        Params(
            {
                "threadId": thread_id,
                "objective": "Fix the cart",
                "start": False,
                "checks": [{"requirement": "tests pass", "command": ["pytest", "-q"]}],
            }
        )
    )
    goal = created["goal"]
    assert goal["checks"] == [
        {"requirement": "tests pass", "command": ["pytest", "-q"]}
    ]
    assert created["reviews"] == []

    with pytest.raises(InvalidParams):
        dispatcher._thread_goal_set(
            Params(
                {
                    "threadId": thread_id,
                    "expectedGoalId": goal["id"],
                    "checks": [],
                }
            )
        )
    with pytest.raises(InvalidParams):
        dispatcher._thread_goal_checks_confirm(
            Params(
                {
                    "threadId": thread_id,
                    "expectedGoalId": goal["id"],
                    "checks": [{"requirement": "x", "command": "pytest -q"}],
                }
            )
        )
    confirmed = dispatcher._thread_goal_checks_confirm(
        Params(
            {
                "threadId": thread_id,
                "expectedGoalId": goal["id"],
                "checks": [{"requirement": "reviewed"}],
            }
        )
    )
    assert confirmed["goal"]["checks"] == [{"requirement": "reviewed", "command": []}]
    dismissed = dispatcher._thread_goal_checks_dismiss(
        Params({"threadId": thread_id, "expectedGoalId": goal["id"]})
    )
    assert dismissed["goal"]["proposedChecks"] == []
    got = dispatcher._thread_goal_get(Params({"threadId": thread_id}))
    assert (
        got["reviews"] == [] and got["goal"]["checks"][0]["requirement"] == "reviewed"
    )


def test_settings_switch_the_completion_verifier(tmp_path) -> None:
    from core.application.config_store import ConfigStore
    from core.application.settings_service import SettingsService

    service = SettingsService(store=ConfigStore(tmp_path / "deepcode_config.json"))
    assert service.read()["goal"] == {"completionVerifier": "off", "maxRejections": 3}
    updated = service.update({"goal": {"completionVerifier": "reviewer"}})
    assert updated["goal"]["completionVerifier"] == "reviewer"
    with pytest.raises(InvalidArgumentError):
        service.update({"goal": {"completionVerifier": "always"}})
    with pytest.raises(InvalidArgumentError):
        service.update({"goal": {"somethingElse": 1}})


# --- the command line -------------------------------------------------------


def test_check_commands_are_split_and_shell_syntax_runs_in_bash() -> None:
    from cli.goal_runner import _goal_checks, GoalRunOptions, parse_check_command

    assert parse_check_command('python -m pytest -q "tests/a b.py"') == (
        "python",
        "-m",
        "pytest",
        "-q",
        "tests/a b.py",
    )
    for text in ("pytest | tail", "make&&make test", "pytest tests/*.py"):
        assert parse_check_command(text) == ("bash", "-o", "pipefail", "-c", text)
    with pytest.raises(InvalidArgumentError):
        parse_check_command("  ")
    long = "pytest -k " + "a" * 600
    (check,) = _goal_checks(
        GoalRunOptions(objective="x", workspace=".", completion_evidence_command=long)
    )
    assert len(check.requirement) <= 500
    with pytest.raises(InvalidArgumentError):
        _goal_checks(
            GoalRunOptions(objective="x", workspace=".", check_commands=("x" * 3000,))
        )


@pytest.mark.asyncio
async def test_a_failing_stage_fails_a_piped_check(tmp_path) -> None:
    from cli.goal_runner import parse_check_command

    command = parse_check_command(f'"{PY}" -c "raise SystemExit(1)" | cat')
    (run,) = await run_checks((GoalCheckSpec("x", command),), cwd=str(tmp_path))
    assert run.exit_code == 1


def test_the_tui_lists_confirms_and_adds_checks(tmp_path) -> None:
    from types import SimpleNamespace

    from cli.tui.goal_controller import TuiGoalController

    goals, _store, _sessions, thread_id, _ = _goals(tmp_path)
    goal = goals.create(thread_id, objective="Fix the cart", start=False)
    context = _running_turn(goals, thread_id, goal.id)
    goals.propose_checks(context, (GoalCheck("tests pass", ("pytest", "-q")),))
    owner = SimpleNamespace(
        thread_client=SimpleNamespace(goals=goals, session_id=thread_id)
    )
    controller = TuiGoalController(owner)

    shown = asyncio.run(controller.execute("checks")).message
    assert "Proposed by the agent" in shown and "`pytest -q`" in shown
    # The agent replaces its proposal after the user looked: confirming shows
    # the new one first instead of approving it unseen.
    goals.propose_checks(context, (GoalCheck("tests pass", ("pytest", "-q", "-x")),))
    again = asyncio.run(controller.execute("checks confirm")).message
    assert "This is the current proposal" in again and "pytest -q -x" in again
    assert goals.read(thread_id).checks == ()
    goals.propose_checks(context, (GoalCheck("tests pass", ("pytest", "-q")),))
    asyncio.run(controller.execute("checks"))
    asyncio.run(controller.execute("checks confirm"))
    assert goals.read(thread_id).checks == (GoalCheck("tests pass", ("pytest", "-q")),)
    asyncio.run(controller.execute("checks add ruff check ."))
    assert goals.read(thread_id).checks[-1].command == ("ruff", "check", ".")
    asyncio.run(controller.execute("checks clear"))
    assert goals.read(thread_id).checks == ()
    # Other text after "checks" is an objective, as with every keyword.
    refused = asyncio.run(controller.execute("checks the parser for bugs")).message
    assert "already has a Goal" in refused
    assert isinstance(goals.read(thread_id), ThreadGoal)


# --- the time limit ---------------------------------------------------------


def test_a_goal_stops_once_its_time_limit_is_used(tmp_path) -> None:
    from core.domain.thread_goal import KEEP_TIME_BUDGET, ThreadGoalStatus

    goal = ThreadGoal(thread_id="t", objective="x", time_budget_seconds=60)
    assert goal.has_budget and not goal.budget_spent
    spent = goal.add_usage(tokens=10, elapsed_seconds=61)
    assert spent.status is ThreadGoalStatus.BUDGET_LIMITED
    with pytest.raises(ValueError, match="time limit"):
        spent.user_transition(ThreadGoalStatus.ACTIVE)
    kept = spent.edit(
        "x", token_budget=None, skill_ids=(), time_budget_seconds=KEEP_TIME_BUDGET
    )
    assert kept.time_budget_seconds == 60
    longer = spent.edit("x", token_budget=None, skill_ids=(), time_budget_seconds=600)
    assert (
        longer.user_transition(ThreadGoalStatus.ACTIVE).status
        is ThreadGoalStatus.ACTIVE
    )
    removed = spent.edit("x", token_budget=None, skill_ids=(), time_budget_seconds=None)
    assert removed.time_budget_seconds is None and not removed.has_budget
    with pytest.raises(ValueError):
        ThreadGoal(thread_id="t", objective="x", time_budget_seconds=0)


def test_the_time_limit_is_kept_unless_an_edit_names_one(tmp_path) -> None:
    from app_server.connection import ConnectionState
    from app_server.dispatcher import Dispatcher, Params
    from core.application import DeepCodeApplication
    from core.domain.project import TrustState

    workspace = tmp_path / "w"
    workspace.mkdir()
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_store=SessionStore(tmp_path / "sessions", use_index=False),
    )
    project = application.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
    thread_id = application.threads.start(project.id, title="api").id
    dispatcher = Dispatcher(application, ConnectionState(application.broker))
    goal = dispatcher._thread_goal_set(
        Params(
            {
                "threadId": thread_id,
                "objective": "Fix the cart",
                "start": False,
                "timeBudgetSeconds": 1800,
            }
        )
    )["goal"]
    assert goal["timeBudgetSeconds"] == 1800
    edited = dispatcher._thread_goal_set(
        Params(
            {
                "threadId": thread_id,
                "expectedGoalId": goal["id"],
                "objective": "Fix the cart fully",
                "start": False,
            }
        )
    )["goal"]
    assert edited["timeBudgetSeconds"] == 1800
    cleared = dispatcher._thread_goal_set(
        Params(
            {
                "threadId": thread_id,
                "expectedGoalId": goal["id"],
                "timeBudgetSeconds": None,
                "start": False,
            }
        )
    )["goal"]
    assert cleared["timeBudgetSeconds"] is None


def test_resuming_from_the_command_line_needs_a_longer_time_limit() -> None:
    from types import SimpleNamespace

    from cli.goal_runner import _apply_budget_override
    from core.domain.thread_goal import ThreadGoalStatus

    goal = ThreadGoal(thread_id="t", objective="x", time_budget_seconds=60).add_usage(
        tokens=0, elapsed_seconds=90
    )
    assert goal.status is ThreadGoalStatus.BUDGET_LIMITED
    edits = []
    goals = SimpleNamespace(edit=lambda *args, **kwargs: edits.append(kwargs) or goal)
    with pytest.raises(InvalidArgumentError, match="--time-budget"):
        _apply_budget_override(goals, goal=goal, token_budget=None)
    with pytest.raises(InvalidArgumentError, match="longer"):
        _apply_budget_override(
            goals, goal=goal, token_budget=None, time_budget_seconds=60
        )
    _apply_budget_override(goals, goal=goal, token_budget=None, time_budget_seconds=600)
    assert edits[-1]["time_budget_seconds"] == 600
    assert edits[-1]["token_budget"] is None


@pytest.mark.asyncio
async def test_a_claim_waits_for_running_sub_agents() -> None:
    handler = _Handler()
    passed = GoalVerdict(confirmed=True, justification="ok", stage="checks")
    router = _router(handler, _Fixed(passed))
    running = [2]
    router.configure_running_work(lambda: running[0])

    early = await router.request(status="complete", reason="x")
    assert early["updated"] is False and "2 sub-agent(s)" in early["note"]
    assert handler.updates == [] and handler.reviews == []
    # A blocked report is not held back.
    blocked = await router.request(status="blocked", reason="no key")
    assert blocked["status"] == "blocked"

    router = _router(handler, _Fixed(passed))
    router.configure_running_work(lambda: 0)
    done = await router.request(status="complete", reason="x")
    assert done["verdict"]["confirmed"] is True


def test_a_dismissed_proposal_is_remembered_and_a_paused_goal_takes_none(
    tmp_path,
) -> None:
    goals, _store, sessions, thread_id, _ = _goals(tmp_path)
    goal = goals.create(thread_id, objective="Fix the cart", start=False)
    context = _running_turn(goals, thread_id, goal.id)
    goals.propose_checks(context, (GoalCheck("tests", ("pytest",)),))
    dismissed = goals.dismiss_proposed_checks(thread_id, expected_goal_id=goal.id)
    assert dismissed.checks_dismissed
    prompt = goals._continuation_prompt(dismissed)
    assert "dismissed" in prompt and "call propose_goal_checks" not in prompt
    reread = ThreadGoalStore(SessionStore(tmp_path / "sessions", use_index=False))
    assert reread.read(thread_id).checks_dismissed
    confirmed = goals.confirm_checks(
        thread_id, expected_goal_id=goal.id, checks=(GoalCheck("t", ("pytest",)),)
    )
    assert not confirmed.checks_dismissed

    goals.pause(thread_id, expected_goal_id=goal.id)
    with pytest.raises(ConflictError, match="paused"):
        goals.propose_checks(context, (GoalCheck("other", ("true",)),))


def test_kept_reviews_match_the_protocol(tmp_path) -> None:
    goals, _store, sessions, thread_id, _ = _goals(tmp_path)
    goal = goals.create(thread_id, objective="Fix the cart", start=False)
    from core.agent_runtime.goal_checks import GoalCheckRun

    goals.record_review(
        GoalRuntimeContext(thread_id, goal.id, "turn_1"),
        review_record(
            goal_id=goal.id,
            turn_id="turn_1",
            result="rejected",
            verdict=GoalVerdict(
                confirmed=False,
                justification="no",
                stage="checks",
                check_runs=(GoalCheckRun("r", ("c",), 1, "out", 1.0),),
            ),
        ),
    )
    path = sessions.root / thread_id / GOAL_REVIEWS_FILENAME
    with path.open("a") as handle:
        # Valid JSON, wrong shape: never handed to clients.
        handle.write(json.dumps({"schemaVersion": 1, "goalId": goal.id}) + "\n")
    (review,) = goals.read_reviews(thread_id, goals.read(thread_id))
    schema = json.loads(
        (
            Path(__file__).resolve().parents[1] / "protocol" / "app-server.schema.json"
        ).read_text()
    )
    definitions = schema["$defs"]

    def conforms(value: dict, name: str) -> None:
        definition = definitions[name]
        assert set(value) <= set(definition["properties"]), set(value) - set(
            definition["properties"]
        )
        assert set(definition["required"]) <= set(value)

    conforms(review, "GoalReview")
    for entry in review["checks"]:
        conforms(entry, "GoalReviewCheck")
    assert review["stage"] in definitions["GoalReview"]["properties"]["stage"]["enum"]


def test_goal_update_events_carry_a_slim_latest_review() -> None:
    from core.application.application import _slim_review

    review = {
        "checks": [{"output": "x" * 5000 + "END", "exitCode": 1}],
        "result": "rejected",
    }
    slim = _slim_review(review)
    assert len(slim["checks"][0]["output"]) == 500
    assert slim["checks"][0]["output"].endswith("END")
    assert review["checks"][0]["output"].startswith("x")


@pytest.mark.asyncio
async def test_a_hung_sub_agent_holds_back_only_a_few_claims() -> None:
    from core.agent_runtime.goal_runtime import MAX_HELD_CLAIMS

    handler = _Handler()
    passed = GoalVerdict(confirmed=True, justification="ok", stage="checks")
    router = _router(handler, _Fixed(passed))
    router.configure_running_work(lambda: 1)
    for _ in range(MAX_HELD_CLAIMS):
        held = await router.request(status="complete", reason="x")
        assert held["updated"] is False and "interrupt_agent" in held["note"]
    judged = await router.request(status="complete", reason="x")
    assert judged["verdict"]["confirmed"] is True


def test_removing_every_check_stops_asking_for_a_list(tmp_path) -> None:
    goals, _store, _sessions, thread_id, _ = _goals(tmp_path)
    goal = goals.create(
        thread_id, objective="Fix", checks=(GoalCheck("t", ("pytest",)),), start=False
    )
    removed = goals.confirm_checks(thread_id, expected_goal_id=goal.id, checks=())
    assert removed.checks_dismissed
    assert "call propose_goal_checks" not in goals._continuation_prompt(removed)


def test_a_review_with_malformed_entries_is_not_handed_out(tmp_path) -> None:
    goals, _store, sessions, thread_id, _ = _goals(tmp_path)
    goal = goals.create(thread_id, objective="Fix", start=False)
    good = review_record(
        goal_id=goal.id,
        turn_id="turn_1",
        result="passed",
        verdict=GoalVerdict(confirmed=True, justification="ok", stage="rule"),
    )
    path = sessions.root / thread_id / GOAL_REVIEWS_FILENAME
    with path.open("a") as handle:
        for broken in (
            {**good, "checks": [{}]},
            {
                **good,
                "requirements": [
                    {"requirement": "r", "finding": "maybe", "evidence": ""}
                ],
            },
            {**good, "stage": "magic"},
        ):
            handle.write(json.dumps({"schemaVersion": 1, **broken}) + "\n")
        handle.write(json.dumps({"schemaVersion": 1, **good}) + "\n")
    assert [r["result"] for r in goals.read_reviews(thread_id, goal)] == ["passed"]


def test_a_cmd_program_never_gets_cmd_syntax_in_its_arguments(monkeypatch) -> None:
    import core.harness.sandbox as sandbox
    from core.agent_runtime import goal_checks

    monkeypatch.setattr(goal_checks.os, "name", "nt")
    monkeypatch.setattr(
        goal_checks.shutil,
        "which",
        lambda name, path=None: "C:/Program Files/nodejs/npm.cmd",
    )
    monkeypatch.setattr(sandbox, "default_shell", lambda: "C:/Git/bin/bash.exe")
    with pytest.raises(OSError, match="cmd.exe"):
        goal_checks._runnable(("npm", "test", "--", "x&calc"), {})
    # As the agent runs it: through bash, each argument quoted for bash.
    assert goal_checks._runnable(("npm", "test", "--grep", "a b"), {}) == [
        "C:/Git/bin/bash.exe",
        "-c",
        "npm test --grep 'a b'",
    ]


@pytest.mark.asyncio
async def test_a_command_shared_by_several_checks_runs_once(tmp_path) -> None:
    counter = tmp_path / "count"
    code = f"open({str(counter)!r}, 'a').write('x')"
    runs = await run_checks(
        (_spec(code, "adds"), _spec("pass", "other"), _spec(code, "removes")),
        cwd=str(tmp_path),
    )
    assert counter.read_text() == "x"
    assert [run.requirement for run in runs] == ["adds; removes", "other"]
