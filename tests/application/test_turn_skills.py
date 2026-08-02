from __future__ import annotations

import time
from pathlib import Path

from core.application import DeepCodeApplication
from core.domain.project import TrustState
from core.domain.turn import TurnStatus
from core.events import (
    AgentMessage,
    Event,
    SkillLoaded,
    TaskComplete,
    TurnStarted,
)
from core.sessions import SessionStore
from core.skills.models import (
    SkillInvocation,
    SkillInvocationKind,
    SkillSelection,
)

SKILL_ID = "sk_0123456789abcdef01234567"


class SkillSession:
    def __init__(self) -> None:
        self.history = []
        self.submitted = None

    def load_history(self, messages):
        self.history = list(messages)

    async def run_stream(self, op):
        self.submitted = op
        invocation = SkillInvocation(
            skill_id=SKILL_ID,
            name="review",
            revision="sha256:" + ("a" * 64),
            source="project:deepcode",
            kind=SkillInvocationKind.EXPLICIT,
        )
        yield Event("1", TurnStarted((invocation,)))
        yield Event("2", SkillLoaded(invocation))
        yield Event("3", AgentMessage("done"))
        yield Event("4", TaskComplete("done", "completed"))

    async def aclose(self):
        return None


class SkillFactory:
    def __init__(self) -> None:
        self.session = SkillSession()

    def create(self, **_kwargs):
        return self.session


def _wait(application: DeepCodeApplication, turn_id: str):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        snapshot = application.turns.read(turn_id)
        if snapshot.turn.status.is_terminal:
            return snapshot
        time.sleep(0.01)
    raise AssertionError("turn did not finish")


def test_desktop_turn_persists_selection_and_invocation_ledger(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "sessions")
    factory = SkillFactory()
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=factory,
        session_store=store,
    )
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    thread = application.threads.start(project.id, title="Skills")
    try:
        started = application.turns.start(
            thread.id,
            prompt="Review this",
            skill_ids=(SKILL_ID,),
        )
        snapshot = _wait(application, started.turn.id)

        assert snapshot.turn.status is TurnStatus.COMPLETED
        assert snapshot.turn.skill_ids == (SKILL_ID,)
        assert factory.session.submitted.skills == (SkillSelection(SKILL_ID),)
        user_item = snapshot.items[0]
        assert user_item.payload["skillIds"] == [SKILL_ID]
        assert user_item.payload["skills"][0]["name"] == "review"

        stored = store.get_session(thread.id)
        assert stored is not None
        assert stored.messages[0].metadata["schemaVersion"] == 3
        assert stored.messages[0].metadata["skillInvocations"][0]["skillId"] == SKILL_ID
        assert (
            stored.messages[1]
            .metadata["skillInvocations"][0]["revision"]
            .startswith("sha256:")
        )
    finally:
        application.close()


def test_turn_skill_selection_survives_application_reopen(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "sessions")
    factory = SkillFactory()
    database_path = tmp_path / "state.sqlite3"
    application = DeepCodeApplication.open(
        database_path,
        session_factory=factory,
        session_store=store,
    )
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    thread = application.threads.start(project.id, title="Queued Skills")
    turn_id = ""
    try:
        turn = application.turns.start(
            thread.id,
            prompt="Use it",
            skill_ids=(SKILL_ID,),
        )
        turn_id = turn.turn.id
        snapshot = _wait(application, turn.turn.id)
        assert snapshot.turn.skill_ids == (SKILL_ID,)
    finally:
        application.close()

    reopened = DeepCodeApplication.open(
        database_path,
        session_factory=SkillFactory(),
        session_store=store,
    )
    try:
        restored = reopened.turns.read(turn_id)
        assert restored.turn.skill_ids == (SKILL_ID,)
        assert restored.items[0].payload["skillIds"] == [SKILL_ID]
    finally:
        reopened.close()
