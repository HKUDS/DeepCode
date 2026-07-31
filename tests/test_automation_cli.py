from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from cli import automation_cli
from core.application import DeepCodeApplication
from core.application.automation_service import (
    AutomationCreation,
    AutomationExecution,
    AutomationInventory,
    AutomationRunPage,
)
from core.application.errors import ProjectNotTrustedError
from core.application.views import automation_run_view, turn_view
from core.domain.automation import (
    Automation,
    AutomationActivationStatus,
    AutomationRun,
    AutomationRunStatus,
    AutomationScheduleKind,
    AutomationStatus,
    AutomationTrigger,
)
from core.domain.common import utc_now
from core.domain.project import Project, TrustState
from core.domain.thread import Thread, ThreadMode
from core.domain.turn import Turn
from core.events import AgentMessage, Event, TaskComplete, TurnStarted
from core.sessions import SessionStore


class _Projects:
    def __init__(self, project: Project) -> None:
        self.project = project
        self.read_calls: list[str] = []
        self.add_calls: list[str] = []

    def read(self, project_id: str) -> Project:
        self.read_calls.append(project_id)
        return self.project

    def add(self, workspace: str) -> Project:
        self.add_calls.append(workspace)
        return self.project


class _Automations:
    def __init__(
        self,
        automation: Automation,
        thread: Thread,
        run: AutomationRun,
        turn: Turn,
    ) -> None:
        self.automation = automation
        self.thread = thread
        self.run = run
        self.turn = turn
        self.calls: list[tuple[str, tuple, dict]] = []
        self.trust_error = False

    def list(self, project_id=None, *, limit=100, offset=0):
        self.calls.append(("list", (project_id,), {"limit": limit, "offset": offset}))
        return AutomationInventory(
            (self.automation,),
            (self.run,),
            True,
            True,
            offset + 1,
        )

    def create(self, **kwargs):
        self.calls.append(("create", (), kwargs))
        if self.trust_error:
            raise ProjectNotTrustedError(
                "project must be trusted before an automation can be created"
            )
        return AutomationCreation(
            replace(
                self.automation,
                schedule_kind=kwargs["schedule_kind"],
                interval_seconds=kwargs["interval_seconds"],
                status=(
                    AutomationStatus.ENABLED
                    if kwargs["enabled"]
                    else AutomationStatus.PAUSED
                ),
                next_run_at=(
                    utc_now()
                    if kwargs["enabled"]
                    and kwargs["schedule_kind"] is AutomationScheduleKind.INTERVAL
                    else None
                ),
            ),
            self.thread,
        )

    def update(self, automation_id, **kwargs):
        self.calls.append(("update", (automation_id,), kwargs))
        status = kwargs.get("status")
        if status is not None:
            return SimpleNamespace(
                **{
                    field: getattr(self.automation, field)
                    for field in self.automation.__dataclass_fields__
                    if field != "status"
                },
                status=status,
            )
        return self.automation

    def run_now(self, automation_id, *, request_id=None):
        self.calls.append(("run_now", (automation_id,), {"request_id": request_id}))
        return AutomationExecution(self.run, self.turn)

    def wait_until_terminal(self, run_id):
        self.calls.append(("wait_until_terminal", (run_id,), {}))
        return self.run

    def list_runs(self, automation_id, *, limit=100, offset=0):
        self.calls.append(
            (
                "list_runs",
                (automation_id,),
                {"limit": limit, "offset": offset},
            )
        )
        return AutomationRunPage((self.run,), True, offset + 1)

    def remove(self, automation_id):
        self.calls.append(("remove", (automation_id,), {}))
        return True


class _Application:
    def __init__(self, projects: _Projects, automations: _Automations) -> None:
        self.projects = projects
        self.automations = automations
        self.turns = SimpleNamespace(
            read=lambda turn_id: SimpleNamespace(turn=automations.turn)
        )
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _ImmediateSession:
    def __init__(self, goal_runtime) -> None:
        self.goal_runtime = goal_runtime
        self.history: list[dict[str, str]] = []

    def load_history(self, messages) -> None:
        self.history = list(messages)

    async def run_stream(self, op):
        self.history.append({"role": "user", "content": op.text})
        yield Event("start", TurnStarted())
        self.goal_runtime.request(
            status="complete",
            reason="Automation CLI verification completed.",
        )
        yield Event("message", AgentMessage("completed"))
        yield Event("complete", TaskComplete("completed", "completed"))
        self.history.append({"role": "assistant", "content": "completed"})

    async def aclose(self) -> None:
        await asyncio.sleep(0)


class _ImmediateFactory:
    def create(self, **kwargs):
        return _ImmediateSession(kwargs["goal_runtime"])


def _application(tmp_path: Path) -> tuple[_Application, _Automations, _Projects]:
    project = Project(
        id="proj_cli",
        canonical_path=str(tmp_path),
        display_name="CLI project",
        trust_state=TrustState.TRUSTED,
    )
    thread = Thread(
        id="thread-cli",
        project_id=project.id,
        title="Nightly review",
        mode=ThreadMode.GOAL,
        workspace_path=str(tmp_path),
    )
    automation = Automation(
        id="auto_cli",
        project_id=project.id,
        thread_id=thread.id,
        name="Nightly review",
        current_revision_id="arev_cli",
        prompt="Review and verify the project",
        schedule_kind=AutomationScheduleKind.MANUAL,
    )
    run = AutomationRun(
        id="arun_cli",
        automation_id=automation.id,
        revision_id=automation.current_revision_id,
        occurrence_id="aocc_cli",
        thread_id=thread.id,
        goal_id="goal_cli",
        turn_id="turn_cli",
        trigger=AutomationTrigger.MANUAL,
        status=AutomationRunStatus.RUNNING,
        scheduled_for=utc_now(),
    )
    turn = Turn(
        id="turn_cli",
        thread_id=thread.id,
        ordinal=1,
        prompt=automation.prompt,
        goal_id="goal_cli",
    )
    projects = _Projects(project)
    automations = _Automations(automation, thread, run, turn)
    return _Application(projects, automations), automations, projects


def test_parser_accepts_json_and_project_selectors_on_either_side() -> None:
    before = automation_cli._parser().parse_args(["--json", "--workspace", ".", "list"])
    after = automation_cli._parser().parse_args(["list", "--workspace", ".", "--json"])

    assert before.json is True
    assert after.json is True
    assert before.workspace == after.workspace == "."

    paged = automation_cli._parser().parse_args(
        ["list", "--limit", "25", "--offset", "50"]
    )
    assert paged.limit == 25
    assert paged.offset == 50


def test_create_resolves_workspace_through_project_service_and_delegates(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    application, automations, projects = _application(tmp_path)

    result = automation_cli.run(
        [
            "create",
            "Nightly review",
            "--prompt",
            "Review and verify the project",
            "--schedule",
            "interval",
            "--interval-seconds",
            "3600",
            "--disabled",
            "--workspace",
            str(tmp_path),
            "--json",
        ],
        application_factory=lambda: application,
    )

    assert result == 0
    assert application.closed is True
    assert projects.add_calls == [str(tmp_path)]
    assert projects.read_calls == []
    command, positional, keywords = automations.calls[-1]
    assert command == "create"
    assert positional == ()
    assert keywords == {
        "project_id": "proj_cli",
        "name": "Nightly review",
        "prompt": "Review and verify the project",
        "schedule_kind": AutomationScheduleKind.INTERVAL,
        "interval_seconds": 3600,
        "enabled": False,
    }
    assert json.loads(capsys.readouterr().out)["automation"]["id"] == "auto_cli"


def test_commands_are_thin_adapters_over_shared_automation_service(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    application, automations, projects = _application(tmp_path)

    assert (
        automation_cli.run(
            ["--project", "proj_cli", "list", "--json"],
            application_factory=lambda: application,
        )
        == 0
    )
    listed = json.loads(capsys.readouterr().out)
    assert listed["automations"][0]["id"] == "auto_cli"
    assert listed["latestRuns"][0]["id"] == "arun_cli"
    assert listed["hasMore"] is True
    assert listed["nextOffset"] == 1
    assert projects.read_calls == ["proj_cli"]
    assert automations.calls[-1] == (
        "list",
        ("proj_cli",),
        {"limit": 100, "offset": 0},
    )

    for command, expected_status in (
        ("disable", AutomationActivationStatus.PAUSED),
        ("enable", AutomationActivationStatus.ENABLED),
    ):
        application.closed = False
        assert (
            automation_cli.run(
                [command, "auto_cli", "--json"],
                application_factory=lambda: application,
            )
            == 0
        )
        capsys.readouterr()
        assert automations.calls[-1] == (
            "update",
            ("auto_cli",),
            {"status": expected_status},
        )

    application.closed = False
    assert (
        automation_cli.run(
            [
                "runs",
                "auto_cli",
                "--limit",
                "7",
                "--offset",
                "14",
                "--json",
            ],
            application_factory=lambda: application,
        )
        == 0
    )
    run_page = json.loads(capsys.readouterr().out)
    assert run_page["runs"][0]["id"] == "arun_cli"
    assert run_page["hasMore"] is True
    assert run_page["nextOffset"] == 15
    assert automations.calls[-1] == (
        "list_runs",
        ("auto_cli",),
        {"limit": 7, "offset": 14},
    )


def test_run_forwards_optional_request_id_without_reimplementing_idempotency(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, automations, _projects = _application(tmp_path)
    received: dict[str, object] = {}

    def foreground(
        candidate_application,
        automation_id,
        *,
        request_id,
        interactive,
    ):
        received.update(
            {
                "application": candidate_application,
                "automation_id": automation_id,
                "request_id": request_id,
                "interactive": interactive,
            }
        )
        completed = replace(
            automations.run,
            status=AutomationRunStatus.COMPLETED,
            completed_at=utc_now(),
        )
        return {
            "run": automation_run_view(completed),
            "turn": turn_view(automations.turn),
        }

    monkeypatch.setattr(automation_cli, "run_automation_foreground", foreground)

    assert (
        automation_cli.run(
            ["run", "auto_cli", "--request-id", "deploy-42", "--json"],
            application_factory=lambda: application,
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["run"]["id"] == "arun_cli"
    assert payload["turn"]["id"] == "turn_cli"
    assert received == {
        "application": application,
        "automation_id": "auto_cli",
        "request_id": "deploy-42",
        "interactive": False,
    }
    assert automations.calls == []


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("completed", 0),
        ("blocked", 1),
        ("failed", 1),
        ("interrupted", 1),
        ("skipped", 1),
    ],
)
def test_run_exit_code_reflects_terminal_outcome(
    status: str,
    expected: int,
) -> None:
    assert (
        automation_cli._result_exit_code(
            "run",
            {"run": {"status": status}},
        )
        == expected
    )


def test_human_output_discloses_pagination_and_live_runtime(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    application, _automations, _projects = _application(tmp_path)

    assert (
        automation_cli.run(
            ["list", "--limit", "1", "--offset", "7"],
            application_factory=lambda: application,
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "scheduler-enabled DeepCode Desktop or App Server" in output
    assert "continue with --offset 8" in output

    application.closed = False
    assert (
        automation_cli.run(
            [
                "create",
                "Scheduled review",
                "--prompt",
                "Review the project",
                "--schedule",
                "interval",
                "--interval-seconds",
                "3600",
            ],
            application_factory=lambda: application,
        )
        == 0
    )
    assert "scheduler-enabled DeepCode Desktop or App Server" in capsys.readouterr().out

    application.closed = False
    assert (
        automation_cli.run(
            ["runs", "auto_cli", "--limit", "1", "--offset", "7"],
            application_factory=lambda: application,
        )
        == 0
    )
    assert "continue with --offset 8" in capsys.readouterr().out


def test_real_cli_run_keeps_its_runtime_alive_until_the_goal_settles(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = tmp_path / "state.sqlite3"
    session_store = SessionStore(tmp_path / "sessions")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    seed = DeepCodeApplication.open(
        database_path,
        session_store=session_store,
        session_factory=_ImmediateFactory(),
        run_automation_scheduler=False,
    )
    try:
        project = seed.projects.add(
            str(workspace),
            trust_state=TrustState.TRUSTED,
        )
        created = seed.automations.create(
            project_id=project.id,
            name="CLI verification",
            prompt="Perform and verify the requested work",
            schedule_kind=AutomationScheduleKind.MANUAL,
        )
    finally:
        seed.close()

    def open_application() -> DeepCodeApplication:
        return DeepCodeApplication.open(
            database_path,
            session_store=session_store,
            session_factory=_ImmediateFactory(),
            host_surface="automation_cli_test",
            run_automation_scheduler=False,
        )

    assert (
        automation_cli.run(
            [
                "run",
                created.automation.id,
                "--request-id",
                "real-cli-run",
                "--json",
            ],
            application_factory=open_application,
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["run"]["status"] == "completed"
    assert payload["turn"]["status"] == "completed"

    audit = open_application()
    try:
        runs = audit.automations.list_runs(created.automation.id)
        assert len(runs) == 1
        assert runs[0].status is AutomationRunStatus.COMPLETED
    finally:
        audit.close()


def test_delete_requires_confirmation_before_opening_application(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    opened = False

    def factory():
        nonlocal opened
        opened = True
        raise AssertionError("application must not open before confirmation")

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    assert (
        automation_cli.run(
            ["delete", "auto_cli"],
            application_factory=factory,
        )
        == 2
    )
    assert opened is False
    assert "requires --yes" in capsys.readouterr().err


def test_delete_with_yes_delegates_to_retirement_service(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    application, automations, _projects = _application(tmp_path)

    assert (
        automation_cli.run(
            ["delete", "auto_cli", "--yes", "--json"],
            application_factory=lambda: application,
        )
        == 0
    )

    assert json.loads(capsys.readouterr().out) == {
        "removed": True,
        "automationId": "auto_cli",
    }
    assert automations.calls[-1] == ("remove", ("auto_cli",), {})


def test_untrusted_project_error_is_clear_and_cli_never_grants_trust(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    application, automations, projects = _application(tmp_path)
    automations.trust_error = True

    assert (
        automation_cli.run(
            [
                "create",
                "Review",
                "--prompt",
                "Review the project",
                "--workspace",
                str(tmp_path),
                "--json",
            ],
            application_factory=lambda: application,
        )
        == 1
    )

    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "PERMISSION_DENIED"
    assert "never grants trust" in error["message"]
    assert projects.add_calls == [str(tmp_path)]
    assert not hasattr(projects, "update")


def test_cli_rejects_pausing_a_manual_definition(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        run_automation_scheduler=False,
    )
    project = application.projects.add(
        str(tmp_path),
        trust_state=TrustState.TRUSTED,
    )

    assert (
        automation_cli.run(
            [
                "create",
                "Invalid paused manual",
                "--prompt",
                "This must not be created",
                "--disabled",
                "--project",
                project.id,
                "--json",
            ],
            application_factory=lambda: application,
        )
        == 1
    )

    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "INVALID_REQUEST"
    assert "manual automations are always enabled" in error["message"]


def test_launcher_routes_automation_without_replacing_legacy_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import deepcode

    received: list[str] = []
    monkeypatch.setattr(
        automation_cli,
        "run",
        lambda argv: received.extend(argv) or 17,
    )
    monkeypatch.setattr(
        "sys.argv",
        ["deepcode", "automation", "list", "--json"],
    )

    with pytest.raises(SystemExit) as raised:
        deepcode.main()

    assert raised.value.code == 17
    assert received == ["list", "--json"]
