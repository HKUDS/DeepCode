from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app_server.connection import ConnectionState
from app_server.dispatcher import Dispatcher, Params
from app_server.errors import InvalidParams
from core.application.event_service import EventBroker
from core.application.views import automation_view
from core.domain.automation import AutomationActivationStatus


class RecordingAutomations:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.update_calls: list[tuple[str, dict[str, object]]] = []
        self.list_calls: list[tuple[str | None, int, int]] = []
        self.run_list_calls: list[tuple[str, int, int]] = []

    def list(self, project_id: str | None, *, limit: int, offset: int):
        self.list_calls.append((project_id, limit, offset))
        return SimpleNamespace(
            automations=(),
            latest_runs=(),
            scheduler_active=True,
            has_more=True,
            next_offset=offset + limit,
        )

    def list_runs(self, automation_id: str, *, limit: int, offset: int):
        self.run_list_calls.append((automation_id, limit, offset))
        return SimpleNamespace(
            runs=(),
            has_more=True,
            next_offset=offset + limit,
        )

    def run_now(self, automation_id: str, *, request_id: str | None):
        self.calls.append((automation_id, request_id))
        now = datetime.now(UTC)
        run = SimpleNamespace(
            id="arun_transport",
            automation_id=automation_id,
            revision_id="arev_transport",
            occurrence_id="aocc_transport",
            thread_id="thread-transport",
            goal_id="goal_transport",
            turn_id=None,
            trigger=SimpleNamespace(value="manual"),
            status=SimpleNamespace(value="queued"),
            scheduled_for=now,
            detail="",
            created_at=now,
            updated_at=now,
            started_at=None,
            completed_at=None,
        )
        return SimpleNamespace(run=run, turn=None)

    def update(self, automation_id: str, **changes: object):
        self.update_calls.append((automation_id, changes))
        now = datetime.now(UTC)
        return SimpleNamespace(
            id=automation_id,
            project_id="proj_transport",
            thread_id="thread-transport",
            name=str(changes.get("name") or "Transport contract"),
            current_revision_id="arev_transport",
            prompt=str(changes.get("prompt") or "Run it"),
            status=SimpleNamespace(
                value=(
                    changes["status"].value
                    if isinstance(changes.get("status"), AutomationActivationStatus)
                    else "enabled"
                )
            ),
            schedule_kind=SimpleNamespace(value="manual"),
            interval_seconds=None,
            next_run_at=None,
            last_run_at=None,
            created_at=now,
            updated_at=now,
        )


def _dispatcher() -> tuple[Dispatcher, RecordingAutomations]:
    automations = RecordingAutomations()
    application = SimpleNamespace(automations=automations)
    broker = EventBroker()
    return Dispatcher(application, ConnectionState(broker)), automations


def test_automation_view_projects_current_revision_identity() -> None:
    now = datetime.now(UTC)
    automation = SimpleNamespace(
        id="auto_transport",
        project_id="proj_transport",
        thread_id="thread-transport",
        name="Transport contract",
        current_revision_id="arev_transport",
        prompt="Run it",
        status=SimpleNamespace(value="enabled"),
        schedule_kind=SimpleNamespace(value="manual"),
        interval_seconds=None,
        next_run_at=None,
        last_run_at=None,
        created_at=now,
        updated_at=now,
    )

    assert automation_view(automation)["currentRevisionId"] == "arev_transport"


def test_automation_run_forwards_optional_request_id_and_projects_p0_identity() -> None:
    dispatcher, automations = _dispatcher()
    request_id = "r" * 255

    result = dispatcher._automation_run(
        Params(
            {
                "automationId": "auto_transport",
                "requestId": request_id,
            }
        )
    )

    assert automations.calls == [("auto_transport", request_id)]
    assert result["run"]["revisionId"] == "arev_transport"
    assert result["run"]["occurrenceId"] == "aocc_transport"
    assert result["run"]["goalId"] == "goal_transport"


def test_automation_run_remains_compatible_without_request_id() -> None:
    dispatcher, automations = _dispatcher()

    dispatcher._automation_run(Params({"automationId": "auto_transport"}))

    assert automations.calls == [("auto_transport", None)]


def test_automation_update_accepts_only_live_activation_states() -> None:
    dispatcher, automations = _dispatcher()

    result = dispatcher._automation_update(
        Params({"automationId": "auto_transport", "status": "paused"})
    )

    assert automations.update_calls[0][0] == "auto_transport"
    assert automations.update_calls[0][1]["status"] is AutomationActivationStatus.PAUSED
    assert result["automation"]["status"] == "paused"


def test_automation_update_rejects_retired_lifecycle_state() -> None:
    dispatcher, automations = _dispatcher()

    with pytest.raises(InvalidParams, match="status must be enabled or paused"):
        dispatcher._automation_update(
            Params({"automationId": "auto_transport", "status": "retired"})
        )

    assert automations.update_calls == []


def test_automation_update_rejects_a_noop_request() -> None:
    dispatcher, automations = _dispatcher()

    with pytest.raises(
        InvalidParams,
        match="requires at least one changed field",
    ):
        dispatcher._automation_update(Params({"automationId": "auto_transport"}))

    assert automations.update_calls == []


def test_automation_pages_forward_bounded_limit_and_offset() -> None:
    dispatcher, automations = _dispatcher()

    inventory = dispatcher._automation_list(
        Params({"projectId": "proj_transport", "limit": 25, "offset": 50})
    )
    history = dispatcher._automation_runs(
        Params({"automationId": "auto_transport", "limit": 10, "offset": 20})
    )

    assert automations.list_calls == [("proj_transport", 25, 50)]
    assert inventory["hasMore"] is True
    assert inventory["nextOffset"] == 75
    assert automations.run_list_calls == [("auto_transport", 10, 20)]
    assert history == {"runs": [], "hasMore": True, "nextOffset": 30}


@pytest.mark.parametrize(
    ("method", "params", "message"),
    [
        ("list", {"limit": 0}, "limit"),
        ("list", {"offset": -1}, "offset"),
        ("runs", {"automationId": "auto_transport", "limit": 501}, "limit"),
        ("runs", {"automationId": "auto_transport", "offset": -1}, "offset"),
    ],
)
def test_automation_pages_reject_invalid_bounds(
    method: str,
    params: dict[str, object],
    message: str,
) -> None:
    dispatcher, automations = _dispatcher()

    with pytest.raises(InvalidParams, match=message):
        if method == "list":
            dispatcher._automation_list(Params(params))
        else:
            dispatcher._automation_runs(Params(params))

    assert automations.list_calls == []
    assert automations.run_list_calls == []


@pytest.mark.parametrize("request_id", ["", "   ", "r" * 256, 42])
def test_automation_run_rejects_invalid_request_id(request_id: object) -> None:
    dispatcher, automations = _dispatcher()

    with pytest.raises(InvalidParams, match="requestId"):
        dispatcher._automation_run(
            Params(
                {
                    "automationId": "auto_transport",
                    "requestId": request_id,
                }
            )
        )

    assert automations.calls == []
