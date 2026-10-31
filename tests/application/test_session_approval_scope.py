"""An "always allow this Session" answer covers the same kind of call only.

Each Turn below asks for one tool call (named by its prompt) through the real
ApprovalService and the durable grant table, the way every client's answer
arrives.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from core.application import DeepCodeApplication
from core.domain import TrustState
from core.domain.approval import ApprovalGrant, ApprovalStatus
from core.domain.turn import TurnStatus
from core.events import AgentMessage, Event, TaskComplete, TurnStarted
from core.harness.permissions import confirmation_reason
from core.persistence import ApprovalGrantRepository
from core.sessions import SessionStore


class _OneCallSession:
    """Asks to run the tool call its prompt spells as JSON."""

    def __init__(self, approval_callback) -> None:
        self._approval_callback = approval_callback
        self.history: list[dict[str, str]] = []

    def load_history(self, messages) -> None:
        self.history = list(messages)

    async def run_stream(self, operation):
        call = json.loads(operation.text)
        self.history.append({"role": "user", "content": operation.text})
        yield Event("1", TurnStarted())
        await self._approval_callback(
            call["tool"], call["arguments"], confirmation_reason(call["tool"])
        )
        yield Event("2", AgentMessage("done"))
        yield Event("3", TaskComplete("done", "completed"))
        self.history.append({"role": "assistant", "content": "done"})

    async def aclose(self) -> None:
        return None


class _Factory:
    def create(self, *, workspace, model, approval_callback):
        return _OneCallSession(approval_callback)


@pytest.fixture
def session(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=_Factory(),
        session_store=SessionStore(tmp_path / "sessions"),
        host_surface="approval-scope",
        run_automation_scheduler=False,
    )
    try:
        project = application.projects.add(
            str(workspace), trust_state=TrustState.TRUSTED
        )
        thread = application.threads.start(project.id, title="Scopes")
        yield application, thread.id, workspace
    finally:
        application.close()


def _wait(application, turn_id, condition):
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        snapshot = application.turns.read(turn_id)
        if condition(snapshot):
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"Turn did not settle: {application.turns.read(turn_id)}")


def _call(application, thread_id, tool, arguments, *, answer=None):
    """Run one Turn; returns the approval it asked for, or ``None``."""

    started = application.turns.start(
        thread_id, prompt=json.dumps({"tool": tool, "arguments": arguments})
    )
    snapshot = _wait(
        application,
        started.turn.id,
        lambda s: s.turn.status in {TurnStatus.WAITING_APPROVAL, TurnStatus.COMPLETED},
    )
    if snapshot.turn.status is TurnStatus.COMPLETED:
        return None
    approval = snapshot.approvals[0]
    application.approvals.respond(
        approval.id, decision=answer or ApprovalStatus.APPROVED_ONCE
    )
    _wait(application, started.turn.id, lambda s: s.turn.status is TurnStatus.COMPLETED)
    return approval


def test_approving_ls_for_the_session_still_asks_for_rm(session) -> None:
    application, thread_id, _ = session
    first = _call(
        application,
        thread_id,
        "bash",
        {"command": "ls"},
        answer=ApprovalStatus.APPROVED_SESSION,
    )
    assert first is not None
    assert first.request["sessionScope"]["label"] == (
        "Always allow `ls …` this Session"
    )
    assert first.request["reason"] == "Running a command needs your approval."

    assert _call(application, thread_id, "bash", {"command": "ls -la /"}) is None
    asked = _call(application, thread_id, "bash", {"command": "rm -rf build"})
    assert asked is not None
    assert asked.request["sessionScope"]["label"] == (
        "Always allow `rm …` this Session"
    )


def test_a_compound_command_asks_until_every_part_was_allowed(session) -> None:
    application, thread_id, _ = session
    _call(
        application,
        thread_id,
        "bash",
        {"command": "git status"},
        answer=ApprovalStatus.APPROVED_SESSION,
    )
    assert (
        _call(
            application,
            thread_id,
            "bash",
            {"command": "git status && git push --force"},
        )
        is not None
    )
    _call(
        application,
        thread_id,
        "bash",
        {"command": "git status && git push"},
        answer=ApprovalStatus.APPROVED_SESSION,
    )
    assert (
        _call(
            application,
            thread_id,
            "bash",
            {"command": "git push origin main; git status"},
        )
        is None
    )


def test_allowing_one_edit_allows_edits_in_the_workspace_only(session) -> None:
    application, thread_id, workspace = session
    first = _call(
        application,
        thread_id,
        "write",
        {"file_path": "a.txt", "content": "x"},
        answer=ApprovalStatus.APPROVED_SESSION,
    )
    assert first.request["sessionScope"]["label"] == (
        "Always allow edits in this workspace this Session"
    )
    assert first.request["reason"] == "Changing files needs your approval."
    assert (
        _call(
            application,
            thread_id,
            "edit",
            {
                "file_path": str(workspace / "b.py"),
                "old_string": "a",
                "new_string": "b",
            },
        )
        is None
    )
    assert (
        _call(
            application,
            thread_id,
            "write",
            {"file_path": str(workspace.parent / "outside.txt"), "content": "x"},
        )
        is not None
    )
    # Commands are a different kind of call.
    assert _call(application, thread_id, "bash", {"command": "ls"}) is not None


def test_approving_once_grants_nothing(session) -> None:
    application, thread_id, _ = session
    _call(application, thread_id, "bash", {"command": "ls"})
    assert _call(application, thread_id, "bash", {"command": "ls"}) is not None
    with application.database.read() as connection:
        assert ApprovalGrantRepository(connection).list_for_thread(thread_id) == []


def test_other_tools_are_still_allowed_as_a_whole(session) -> None:
    application, thread_id, _ = session
    _call(
        application,
        thread_id,
        "mcp_tracker_create_issue",
        {"title": "one"},
        answer=ApprovalStatus.APPROVED_SESSION,
    )
    assert (
        _call(application, thread_id, "mcp_tracker_create_issue", {"title": "two"})
        is None
    )
    assert (
        _call(application, thread_id, "mcp_tracker_close_issue", {"id": 1}) is not None
    )


def test_a_whole_tool_command_grant_from_an_older_release_asks_again(session) -> None:
    application, thread_id, _ = session
    approval = _call(application, thread_id, "bash", {"command": "true"})
    with application.database.transaction() as connection:
        ApprovalGrantRepository(connection).add_if_absent(
            ApprovalGrant(
                thread_id=thread_id,
                tool_name="bash",
                source_approval_id=approval.id,
            )
        )
    assert _call(application, thread_id, "bash", {"command": "rm -rf /"}) is not None


def test_scoped_grants_are_stored_so_an_older_release_does_not_match_them(
    session,
) -> None:
    application, thread_id, _ = session
    approval = _call(
        application,
        thread_id,
        "bash",
        {"command": "npm test | tail"},
        answer=ApprovalStatus.APPROVED_SESSION,
    )
    with application.database.read() as connection:
        grants = ApprovalGrantRepository(connection)
        (grant,) = grants.list_for_thread(thread_id)
        # The query an older release runs for "bash".
        assert grants.allows(thread_id, "bash") is False
    assert grant.tool_name == "bash"
    assert grant.scopes == ("cmd:npm test", "cmd:tail")
    assert grant.source_approval_id == approval.id


def test_an_unreadable_stored_scope_does_not_widen_to_the_whole_tool(session) -> None:
    application, thread_id, _ = session
    approval = _call(application, thread_id, "mcp_x", {})
    with application.database.transaction() as connection:
        connection.execute(
            "INSERT INTO approval_grants "
            "(thread_id, tool_name, source_approval_id, granted_at) "
            "VALUES (?, ?, ?, '2026-10-29T00:00:00+00:00')",
            (thread_id, "mcp_x\x1fnot json", approval.id),
        )
    with application.database.read() as connection:
        (grant,) = ApprovalGrantRepository(connection).list_for_thread(thread_id)
    assert grant.tool_name == "mcp_x"
    assert grant.scopes and grant.scopes[0].startswith("unreadable:")
    assert _call(application, thread_id, "mcp_x", {}) is not None


def test_a_request_from_before_scopes_is_scoped_when_answered(session) -> None:
    """An approval stored without ``sessionScope`` still grants narrowly."""

    application, thread_id, _ = session
    started = application.turns.start(
        thread_id,
        prompt=json.dumps({"tool": "bash", "arguments": {"command": "ls"}}),
    )
    snapshot = _wait(
        application,
        started.turn.id,
        lambda s: s.turn.status is TurnStatus.WAITING_APPROVAL,
    )
    approval = snapshot.approvals[0]
    with application.database.transaction() as connection:
        request = dict(approval.request)
        request.pop("sessionScope")
        connection.execute(
            "UPDATE approvals SET request_json = ? WHERE id = ?",
            (json.dumps(request), approval.id),
        )
    application.approvals.respond(approval.id, decision=ApprovalStatus.APPROVED_SESSION)
    _wait(application, started.turn.id, lambda s: s.turn.status is TurnStatus.COMPLETED)
    with application.database.read() as connection:
        (grant,) = ApprovalGrantRepository(connection).list_for_thread(thread_id)
    assert grant.scopes == ("cmd:ls",)
