"""The TUI says what a Turn is waiting for, and what to do about it.

Waiting for an approval is not running; a queued Turn is not working; a
missing key is reported before the first message fails; leaving while a Turn
runs says where it went; the trust question accepts "y".
"""

from __future__ import annotations

import asyncio
import io
import sys
from types import SimpleNamespace

import pytest
from rich.console import Console

import cli.tui.app as tui_app
from cli.tui import approval_view, renderer as renderer_module
from cli.tui.commands import route_title
from cli.tui.renderer import EventRenderer
from core.domain.approval import ApprovalStatus
from core.domain.event import DomainEvent
from core.events import Event, TaskComplete, ToolCompleted, ToolStarted, TurnStarted

THREAD = "thread-1"
TURN = "turn_" + "a" * 26


def _approval(tool="bash", arguments=None, *, target="`rm …` and `echo …`"):
    return {
        "id": "apr_1",
        "turnId": TURN,
        "status": "pending",
        "request": {
            "toolName": tool,
            "arguments": arguments
            if arguments is not None
            else {"command": "rm -rf build && echo removed"},
            "reason": "Running a command needs your approval.",
            "sessionScope": {
                "keys": ["cmd:rm", "cmd:echo"],
                "target": target,
                "label": f"Always allow {target} this session",
            },
        },
    }


def _render(renderable, width=120) -> str:
    output = io.StringIO()
    Console(file=output, color_system=None, width=width).print(renderable)
    return output.getvalue()


# -- the approval card --------------------------------------------------------


def test_a_command_approval_shows_the_whole_command_where_it_runs_and_the_keys(
    tmp_path,
) -> None:
    command = "rm -rf build && echo removed && " + "x" * 150
    text = _render(
        approval_view.approval_card(
            _approval(arguments={"command": command}), workspace=str(tmp_path)
        ),
        width=100,
    )
    assert "◆ Approval needed · Run a command" in text
    assert "Running a command needs your approval." in text
    assert "x" * 60 in text.replace("\n", "").replace(" ", "")
    assert text.replace("\n", "").replace(" ", "").count("x") >= 150
    # Wrapped, never cut: the whole command and directory are on screen.
    assert f"in{tmp_path}" in "".join(text.split())
    assert (
        "y allow once · a always allow `rm …` and `echo …` this session · n deny"
        in " ".join(text.split())
    )
    assert "mutating" not in text


def test_an_edit_approval_shows_the_change_as_a_diff(tmp_path) -> None:
    (tmp_path / "app.py").write_text("a = 1\nb = 2\nc = 3\n")
    text = _render(
        approval_view.approval_card(
            _approval(
                "edit",
                {"file_path": "app.py", "old_string": "b = 2", "new_string": "b = 20"},
                target="edits in this workspace",
            ),
            workspace=str(tmp_path),
        )
    )
    assert "Approval needed · Edit app.py" in text
    assert "-b = 2" in text and "+b = 20" in text
    assert " a = 1" in text  # context
    assert "always allow edits in this workspace this session" in text


def test_a_new_file_shows_its_content_and_a_long_one_is_cut(tmp_path) -> None:
    content = "\n".join(f"line {index}" for index in range(100))
    text = _render(
        approval_view.approval_card(
            _approval("write", {"file_path": "notes.md", "content": content}),
            workspace=str(tmp_path),
        )
    )
    assert "Create notes.md · 100 lines" in text
    assert "+line 0" in text and "+line 39" in text
    assert "+line 40" not in text
    assert "… 60 more lines (/transcript verbose shows up to 400)" in text

    verbose = _render(
        approval_view.approval_card(
            _approval("write", {"file_path": "notes.md", "content": content}),
            workspace=str(tmp_path),
            verbose=True,
        )
    )
    assert "+line 99" in verbose and "more line" not in verbose


def test_overwriting_a_file_shows_a_diff_against_it(tmp_path) -> None:
    (tmp_path / "x.txt").write_text("old\nsame\n")
    text = _render(
        approval_view.approval_card(
            _approval(
                "write",
                {"file_path": str(tmp_path / "x.txt"), "content": "new\nsame\n"},
            ),
            workspace=str(tmp_path),
        )
    )
    assert "Overwrite x.txt" in text
    assert "-old" in text and "+new" in text


def test_a_patch_shows_its_lines(tmp_path) -> None:
    patch = "*** Begin Patch\n*** Update File: a.py\n@@\n-x = 1\n+x = 2\n*** End Patch"
    text = _render(
        approval_view.approval_card(
            _approval("apply_patch", {"patch": patch}), workspace=str(tmp_path)
        )
    )
    assert "Apply a patch" in text
    assert "*** Update File: a.py" in text and "+x = 2" in text
    assert "Begin Patch" not in text


def test_another_tool_shows_its_arguments(tmp_path) -> None:
    text = _render(
        approval_view.approval_card(
            _approval("mcp_tracker_create_issue", {"title": "Bug", "body": "x" * 500}),
            workspace=str(tmp_path),
        )
    )
    assert "Approval needed · mcp_tracker_create_issue" in text
    assert "title: Bug" in text
    assert "…" in text


def test_an_approval_from_an_older_service_still_offers_its_keys() -> None:
    approval = _approval()
    del approval["request"]["sessionScope"]
    assert approval_view.answer_hint(approval) == (
        "y allow once · a always allow this tool this session · n deny"
    )


def test_outcomes_say_what_was_allowed() -> None:
    approval = _approval(target="`git status …`")
    assert approval_view.outcome_text("approved_once", approval) == "Allowed once."
    assert approval_view.outcome_text("approved_session", approval) == (
        "Allowed `git status …` for the rest of this session."
    )
    assert approval_view.outcome_text("denied", approval) == "Denied."


# -- the status line ----------------------------------------------------------


def _renderer() -> EventRenderer:
    return EventRenderer(Console(file=io.StringIO(), color_system=None, width=120))


def test_waiting_for_approval_is_still_and_shows_the_keys() -> None:
    renderer = _renderer()
    renderer.on_event(Event("1", TurnStarted()))
    renderer.on_event(Event("2", ToolStarted("call-1", "bash", "rm -rf build")))
    assert renderer.is_working()
    assert "bash" in renderer.status_line()

    renderer.approval_requested(
        "apr_1", "y allow once · a always allow `rm …` · n deny"
    )
    assert not renderer.is_working()
    assert renderer.status_line() == (
        " ◆ Waiting for your approval · y allow once · a always allow `rm …` · n deny "
    )
    fragments = renderer.status_fragments()
    assert [text for _style, text in fragments] == [
        " ◆ Waiting for your approval ",
        "· y allow once · a always allow `rm …` · n deny ",
    ]

    renderer.approval_resolved("apr_1")
    assert renderer.is_working()
    assert "bash" in renderer.status_line()


def test_settling_a_turn_forgets_its_approvals() -> None:
    renderer = _renderer()
    renderer.on_event(Event("1", TurnStarted()))
    renderer.approval_requested("apr_1", "keys")
    renderer.settle_turn()
    assert renderer.approval_hint is None
    assert "Transcript" in renderer.status_line()


def test_a_queued_turn_says_queued_then_working_once_it_starts(monkeypatch) -> None:
    clock = [100.0]
    monkeypatch.setattr(renderer_module, "monotonic", lambda: clock[0])
    renderer = _renderer()
    renderer.turn_queued(TURN)
    renderer.on_event(Event("1", TurnStarted()))
    # A moment in the queue is normal; it is not worth a word.
    assert "Working" in renderer.status_line()
    clock[0] = 105.0
    # Until the app has learned why, it says only that it waits.
    assert renderer.status_line() == " Queued · 5s · waiting to start "
    renderer.set_queue_reason("waiting for a free slot (2 running)")
    assert renderer.status_line() == (
        " Queued · 5s · waiting for a free slot (2 running) "
    )
    assert "Working" not in renderer.status_line()

    renderer.turn_running(TURN)
    clock[0] = 107.0
    # The clock counts from the start, not from when it queued.
    assert renderer.status_line() == " Working · 2s "


def test_a_turn_that_started_at_once_never_says_queued(monkeypatch) -> None:
    clock = [0.0]
    monkeypatch.setattr(renderer_module, "monotonic", lambda: clock[0])
    renderer = _renderer()
    renderer.turn_queued(TURN)
    renderer.on_event(Event("1", TurnStarted()))
    clock[0] = 0.05
    renderer.turn_running(TURN)
    clock[0] = 30.0
    assert "Queued" not in renderer.status_line()


# -- the app ------------------------------------------------------------------


class _Client:
    runtime_mode = "service"
    session_id = THREAD

    def __init__(self) -> None:
        self.pending = None
        self.sent: list[str] = []
        self.active = False
        self.views: list[dict] = []
        self.execution_profile = SimpleNamespace(
            connection_id="openai", model_id="gpt-x"
        )
        self.llm = SimpleNamespace(
            list_connections=lambda _project: {"connections": self.views}
        )
        self.project = SimpleNamespace(id="proj-1")

    def pending_approval(self):
        return self.pending

    def respond_to_approval(self, approval_id, decision):
        return SimpleNamespace(status=decision)

    def send(self, text, *, skill_ids=()):
        self.sent.append(text)
        return SimpleNamespace(kind="started")

    def has_active_turn(self):
        return self.active


class _Reader:
    interactive = True

    def __init__(self) -> None:
        self.draft = ""

    def keep_draft(self, text):
        self.draft = text


def _app(client=None):
    app = object.__new__(tui_app.TuiApp)
    output = io.StringIO()
    app.console = Console(file=output, color_system=None, width=200)
    app.thread_client = client or _Client()
    app.renderer = EventRenderer(app.console)
    app.reader = _Reader()
    app.workspace = "/ws"
    app.model = "gpt-x"
    app._prompted_approval_ids = set()
    app._answered_approval_ids = set()
    app._shown_approvals = {}
    app._last_send_delivered = True
    app.selected_skill_ids = []
    app._external_refresh_task = None
    app._external_refresh_dirty = False
    return app, output


def _event(type_, payload, *, turn_id=TURN, thread_id=THREAD):
    return DomainEvent(
        sequence=1, type=type_, thread_id=thread_id, turn_id=turn_id, payload=payload
    )


def test_an_approval_event_prints_the_card_and_holds_the_status_line() -> None:
    app, output = _app()
    app._on_domain_event(_event("approval.requested", {"approval": _approval()}))
    text = output.getvalue()
    assert "◆ Approval needed · Run a command" in text
    assert "$ rm -rf build && echo removed" in text
    assert app.renderer.approval_hint is not None
    assert "Waiting for your approval" in app.renderer.status_line()

    app._on_domain_event(
        _event("approval.resolved", {"approval": {"id": "apr_1", "status": "denied"}})
    )
    assert app.renderer.approval_hint is None


def test_text_typed_while_an_approval_waits_is_held_not_steered() -> None:
    client = _Client()
    app, output = _app(client)
    app._on_domain_event(_event("approval.requested", {"approval": _approval()}))
    client.pending = SimpleNamespace(id="apr_1", request=_approval()["request"])

    asyncio.run(app._handle_line("what is this?"))

    assert client.sent == []
    assert app.reader.draft == "what is this?"
    assert "Answer the approval first (y, a or n)." in output.getvalue()
    assert "it is back in the prompt" in output.getvalue()


def test_text_is_sent_once_the_approval_is_gone() -> None:
    client = _Client()
    app, _ = _app(client)
    app._on_domain_event(_event("approval.requested", {"approval": _approval()}))
    client.pending = None  # settled without an event reaching this terminal

    asyncio.run(app._handle_line("next task"))

    assert client.sent == ["next task"]
    assert app._prompted_approval_ids == set()


def test_answering_always_says_what_it_covers() -> None:
    client = _Client()
    app, _ = _app(client)
    app._on_domain_event(_event("approval.requested", {"approval": _approval()}))
    client.pending = SimpleNamespace(id="apr_1", request=_approval()["request"])

    status = app._respond_to_pending_approval("a")

    assert status == "Allowed `rm …` and `echo …` for the rest of this session."
    assert app.renderer.approval_hint is None


def test_the_prompt_asks_for_the_answer_while_an_approval_waits() -> None:
    assert [text for _style, text in tui_app.theme.prompt_fragments()] == ["› "]
    assert [text for _style, text in tui_app.theme.prompt_fragments(approval=True)] == [
        "allow? y/a/n ",
        "› ",
    ]


def test_a_queued_turn_event_reaches_the_status_line(monkeypatch) -> None:
    clock = [10.0]
    monkeypatch.setattr(renderer_module, "monotonic", lambda: clock[0])
    app, _ = _app()
    app._on_domain_event(_event("turn.started", {"turn": {"status": "queued"}}))
    app.renderer.on_event(Event("1", TurnStarted()))
    clock[0] = 14.0
    assert app.renderer.status_line().startswith(" Queued · 4s")
    app._on_domain_event(_event("turn.updated", {"turn": {"status": "running"}}))
    assert "Working" in app.renderer.status_line()


def test_another_sessions_turn_events_are_ignored(monkeypatch) -> None:
    app, _ = _app()
    app._on_domain_event(
        _event("turn.started", {"turn": {"status": "queued"}}, thread_id="other")
    )
    assert app.renderer._queued == {}


SECOND = "turn_" + "b" * 26


def test_a_turn_queued_behind_this_sessions_turn_is_shown_then_waits(
    monkeypatch,
) -> None:
    clock = [10.0]
    monkeypatch.setattr(renderer_module, "monotonic", lambda: clock[0])
    app, _ = _app()
    app._on_domain_event(_event("turn.started", {"turn": {"status": "queued"}}))
    app._on_domain_event(_event("turn.updated", {"turn": {"status": "running"}}))
    app.renderer.on_event(Event("1", TurnStarted()))
    # A second message, queued behind the running Turn.
    app._on_domain_event(
        _event("turn.queued", {"turn": {"status": "queued"}}, turn_id=SECOND)
    )
    clock[0] = 13.0
    assert app.renderer.status_line() == " Working · 3s · 1 queued next "

    # The first Turn ends; the second has not started yet.
    app._on_domain_event(_event("turn.completed", {"turn": {"status": "completed"}}))
    app.renderer.on_event(Event("2", TaskComplete(None, "completed")))
    clock[0] = 15.0
    assert app.renderer.is_working()
    app.renderer.set_queue_reason(
        "waiting for another session in this workspace to finish"
    )
    assert app.renderer.status_line() == (
        " Queued · 5s · waiting for another session in this workspace to finish "
    )

    app._on_domain_event(
        _event("turn.updated", {"turn": {"status": "running"}}, turn_id=SECOND)
    )
    app.renderer.on_event(Event("3", TurnStarted()))
    clock[0] = 16.0
    assert app.renderer.status_line() == " Working · 1s "


def test_an_interrupted_queued_turn_stops_being_shown() -> None:
    app, _ = _app()
    app._on_domain_event(_event("turn.queued", {"turn": {"status": "queued"}}))
    app._on_domain_event(
        _event("turn.interrupted", {"turn": {"status": "interrupted"}})
    )
    assert not app.renderer.has_queued
    assert "Transcript" in app.renderer.status_line()


class _QueueClient(_Client):
    def __init__(self, *, own=None, others=(), running=0, worktree=None) -> None:
        super().__init__()
        self.thread = SimpleNamespace(worktree_path=worktree)
        executing = {THREAD: own, **{thread: "turn" for thread in others}}
        self.turns = SimpleNamespace(
            executing_for_thread=lambda thread_id: executing.get(thread_id)
        )
        listing = [
            {"id": THREAD, "status": "running", "worktreePath": None},
            *(
                {"id": thread, "status": "running", "worktreePath": None}
                for thread in others
            ),
            {"id": "idle-thread", "status": "idle", "worktreePath": None},
        ]
        self.running = running
        self.rpc = SimpleNamespace(
            call=lambda method, params: {"thread/list": {"threads": listing}}[method],
            files=self,
        )


def _service_status(monkeypatch) -> None:
    """``status`` is the service's control call, read with its files."""

    class _ServiceClient:
        def __init__(self, files) -> None:
            self.files = files

        def call(self, method, timeout=5.0):
            assert method == "status"
            return {"activeTurns": self.files.running}

    import app_server.service_client as service_client

    monkeypatch.setattr(service_client, "ServiceClient", _ServiceClient)


@pytest.mark.parametrize(
    ("client", "reason"),
    [
        (
            _QueueClient(own="turn"),
            "waiting for the current Turn in this session to finish",
        ),
        (
            _QueueClient(others=("other-thread",), running=1),
            "waiting for another session in this workspace to finish",
        ),
        (
            # In its own worktree, another session's work does not hold it.
            _QueueClient(others=("other-thread",), running=2, worktree="/wt"),
            "waiting for a free slot (2 running)",
        ),
        (_QueueClient(running=2), "waiting for a free slot (2 running)"),
        (_QueueClient(), None),
    ],
)
def test_the_queue_reason_names_what_holds_the_turn(
    monkeypatch, client, reason
) -> None:
    _service_status(monkeypatch)
    app, _ = _app(client)
    assert app._queue_reason() == reason


def test_the_queue_reason_is_found_while_the_turn_waits(monkeypatch) -> None:
    _service_status(monkeypatch)

    async def scenario():
        client = _QueueClient(running=2)
        app, _ = _app(client)
        app._on_domain_event(_event("turn.started", {"turn": {"status": "queued"}}))
        for _ in range(100):
            if app.renderer._queue_reason:
                break
            await asyncio.sleep(0.01)
        reason = app.renderer._queue_reason
        app._on_domain_event(_event("turn.updated", {"turn": {"status": "running"}}))
        await asyncio.sleep(0)
        app._queue_probe_task.cancel()
        return reason

    assert asyncio.run(scenario()) == "waiting for a free slot (2 running)"


# -- denied by the user -------------------------------------------------------


def _elbow(preview: str, *, is_error: bool = True) -> str:
    output = io.StringIO()
    renderer = EventRenderer(Console(file=output, color_system=None, width=100))
    renderer.on_event(Event("1", TurnStarted()))
    renderer.on_event(Event("2", ToolStarted("call-1", "bash", "rm -rf build")))
    renderer.on_event(Event("3", ToolCompleted("call-1", "bash", is_error, preview)))
    return output.getvalue()


def test_a_call_the_user_denied_reads_denied_by_you() -> None:
    text = _elbow(
        "Error: permission denied: user rejected: Running a command needs your approval."
    )
    assert "⎿ Denied by you" in text
    assert "failed" not in text
    assert "needs your approval" not in text


def test_other_failures_still_read_as_failures() -> None:
    text = _elbow(
        "Error: permission denied: Blocked by your permission rule `bash rm *`."
    )
    assert "bash failed" in text
    assert "Denied by you" not in text


# -- first run ----------------------------------------------------------------


def _view(**fields):
    view = {
        "id": "openai",
        "label": "OpenAI",
        "auth": "api_key",
        "configured": False,
        "enabled": True,
        "credentialSource": "missing",
        "explicit": False,
        "apiKeyEnv": "OPENAI_API_KEY",
    }
    view.update(fields)
    return view


def test_a_missing_key_is_reported_at_startup_with_the_fix(monkeypatch) -> None:
    client = _Client()
    client.views = [
        _view(),
        _view(
            id="deepseek", label="DeepSeek", configured=True, credentialSource="config"
        ),
        # Needs no key, but nothing says it is running.
        _view(
            id="ollama",
            label="Ollama",
            configured=True,
            credentialSource="not_required",
        ),
    ]
    app, output = _app(client)
    monkeypatch.setattr(app, "_uses_builtin_default_model", lambda: False)

    app._print_setup_problem()

    text = output.getvalue()
    assert "! OpenAI has no API key, so messages will fail." in text
    assert "deepcode provider set openai --api-key" in text
    assert "or set OPENAI_API_KEY before starting DeepCode." in text
    assert "/model (deepseek)" in text
    assert "Error" not in text


def test_the_built_in_default_model_is_named_as_such(monkeypatch) -> None:
    client = _Client()
    client.views = [_view()]
    app, output = _app(client)
    app.model = "openai/gpt-4o-mini"
    monkeypatch.setattr(app, "_uses_builtin_default_model", lambda: True)

    app._print_setup_problem()

    assert "openai/gpt-4o-mini is DeepCode's built-in default" in output.getvalue()
    assert "/model" not in output.getvalue()


@pytest.mark.parametrize(
    ("view", "fix"),
    [
        (
            _view(enabled=False, configured=True),
            "deepcode provider set openai --enabled",
        ),
        (_view(auth="oauth"), "deepcode provider login openai"),
        (
            _view(credentialSource="config"),
            "deepcode provider set openai --api-base <url>",
        ),
    ],
)
def test_other_setup_problems_name_their_fix(monkeypatch, view, fix) -> None:
    client = _Client()
    client.views = [view]
    app, output = _app(client)
    monkeypatch.setattr(app, "_uses_builtin_default_model", lambda: False)
    app._print_setup_problem()
    assert fix in output.getvalue()


def test_a_ready_connection_says_nothing(monkeypatch) -> None:
    client = _Client()
    client.views = [_view(configured=True)]
    app, output = _app(client)
    app._print_setup_problem()
    assert output.getvalue() == ""


def test_a_service_that_cannot_list_connections_says_nothing() -> None:
    client = _Client()

    def fail(_project):
        raise RuntimeError("service down")

    client.llm = SimpleNamespace(list_connections=fail)
    app, output = _app(client)
    app._print_setup_problem()
    assert output.getvalue() == ""


def test_the_route_title_does_not_name_the_connection_twice() -> None:
    assert route_title("openai", "openai/gpt-4o-mini") == "openai/gpt-4o-mini"
    assert route_title("openai", "gpt-5") == "openai/gpt-5"
    assert route_title("openrouter", "openai/gpt-5") == "openrouter/openai/gpt-5"


# -- leaving ------------------------------------------------------------------


def test_leaving_while_a_turn_runs_says_how_to_come_back() -> None:
    client = _Client()
    client.active = True
    app, output = _app(client)
    app._print_exit_note()
    text = output.getvalue()
    assert "The current task keeps running in the background service." in text
    assert f"deepcode --resume {THREAD}" in text


def test_leaving_while_an_approval_waits_says_so() -> None:
    client = _Client()
    client.active = True
    client.pending = SimpleNamespace(id="apr_1")
    app, output = _app(client)
    app._print_exit_note()
    assert "is waiting for an approval in the background service" in output.getvalue()


def test_leaving_an_idle_session_says_nothing() -> None:
    app, output = _app()
    app._print_exit_note()
    assert output.getvalue() == ""


# -- trust --------------------------------------------------------------------


class _Stdin(io.StringIO):
    def isatty(self) -> bool:
        return True


def _trust(monkeypatch, capsys, answers: str) -> tuple[bool, str]:
    monkeypatch.setattr(sys, "stdin", _Stdin(answers))
    project = SimpleNamespace(canonical_path="/ws")
    result = tui_app._confirm_trust(project)
    return result, capsys.readouterr().err


@pytest.mark.parametrize("answer", ["y", "Y", "yes", "YES", " Yes "])
def test_trust_accepts_y_and_yes(monkeypatch, capsys, answer) -> None:
    trusted, _ = _trust(monkeypatch, capsys, answer + "\n")
    assert trusted


@pytest.mark.parametrize("answers", ["n\n", "no\n", ""])
def test_declining_trust_explains_how_to_trust_later(
    monkeypatch, capsys, answers
) -> None:
    trusted, err = _trust(monkeypatch, capsys, answers)
    assert not trusted
    assert "Not trusted" in err and "--trust" in err


def test_an_unclear_trust_answer_is_asked_again(monkeypatch, capsys) -> None:
    trusted, err = _trust(monkeypatch, capsys, "sure\ny\n")
    assert trusted
    assert "Please answer y (trust it) or n (leave)" in err


def test_trust_gives_up_after_three_unclear_answers(monkeypatch, capsys) -> None:
    trusted, err = _trust(monkeypatch, capsys, "a\nb\nc\ny\n")
    assert not trusted
    assert "Not trusted" in err


def test_approval_status_answers_are_unchanged() -> None:
    assert tui_app._APPROVAL_ANSWERS["a"] is ApprovalStatus.APPROVED_SESSION
    assert tui_app._APPROVAL_ANSWERS["y"] is ApprovalStatus.APPROVED_ONCE
    assert tui_app._APPROVAL_ANSWERS["n"] is ApprovalStatus.DENIED
