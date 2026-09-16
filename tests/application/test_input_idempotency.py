from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import time

import pytest

from core.application import DeepCodeApplication
from core.application.errors import (
    DuplicateMessageConflictError,
    ProjectNotTrustedError,
)
from core.domain import TrustState
from core.events import AgentMessage, Event, TaskComplete, TurnStarted


class CountingFactory:
    def __init__(self):
        self.runs = 0

    def create(self, *, workspace, model, approval_callback):
        owner = self

        class Session:
            def load_history(self, _history):
                pass

            async def run_stream(self, _operation):
                owner.runs += 1
                yield Event("start", TurnStarted())
                yield Event("answer", AgentMessage("finished"))
                yield Event("done", TaskComplete("finished", "completed"))

            async def aclose(self):
                pass

        return Session()


@contextmanager
def application(tmp_path):
    factory = CountingFactory()
    app = DeepCodeApplication.open(tmp_path / "state.sqlite3", session_factory=factory)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = app.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
    thread = app.threads.start(project.id, title="Idempotency")
    try:
        yield app, thread.id, factory
    finally:
        app.close()


def completed(app, turn_id):
    deadline = time.monotonic() + 5
    while app.turns.read(turn_id).turn.status.value != "completed":
        assert time.monotonic() < deadline
        time.sleep(0.01)


@pytest.mark.parametrize("method", ["start", "enqueue"])
@pytest.mark.parametrize(
    "changed",
    [
        {"model": "other-model"},
        {"connection_id": "other-connection"},
        {"reasoning_effort": "high"},
        {"skill_ids": ("sk_" + "a" * 24,)},
        {"prompt": "different task"},
    ],
)
def test_same_key_cannot_change_submission_intent(tmp_path, method, changed):
    with application(tmp_path) as (app, thread_id, factory):
        app.execution_coordinator.pause_admission()
        submit = getattr(app.turns, method)
        original = submit(thread_id, prompt="run", message_id="stable")
        with pytest.raises(DuplicateMessageConflictError):
            submit(thread_id, **{"prompt": "run", "message_id": "stable", **changed})
        assert (
            submit(thread_id, prompt="run", message_id="stable").turn.id
            == original.turn.id
        )
        assert factory.runs == 0


def test_concurrent_identical_submissions_create_one_turn(tmp_path):
    with application(tmp_path) as (app, thread_id, factory):
        app.execution_coordinator.pause_admission()
        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(
                pool.map(
                    lambda _: (
                        app.turns.start(
                            thread_id, prompt="run", message_id="shared"
                        ).turn.id
                    ),
                    range(16),
                )
            )
        assert len(set(ids)) == 1
        with app.database.read() as connection:
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM turns WHERE thread_id = ?", (thread_id,)
                ).fetchone()[0]
                == 1
            )
        assert factory.runs == 0


def test_retry_uses_original_receipt_after_selection_and_goal_defaults_change(
    tmp_path, monkeypatch
):
    with application(tmp_path) as (app, thread_id, factory):
        first = app.turns.start(thread_id, prompt="run", message_id="stable")
        completed(app, first.turn.id)
        # An inherited Skill can change between an admitted request and retry.
        monkeypatch.setattr(
            app.turns,
            "_merge_goal_skills",
            lambda skills, association: (*skills, "sk_" + "b" * 24),
        )

        def must_not_resolve(*args, **kwargs):
            raise AssertionError("A retry must not resolve the current model defaults")

        monkeypatch.setattr(app.llm, "resolve", must_not_resolve)
        duplicate = app.turns.start(thread_id, prompt="run", message_id="stable")
        assert duplicate.turn.id == first.turn.id
        assert factory.runs == 1


@pytest.mark.parametrize("rebuild", [False, True])
def test_receipt_survives_restart_and_canonical_rebuild(tmp_path, rebuild):
    with application(tmp_path) as (app, thread_id, factory):
        first = app.turns.start(thread_id, prompt="run", message_id="stable")
        completed(app, first.turn.id)
        assert app.turns.read_input(thread_id, "missing") is None
        receipt = app.turns.read_input(thread_id, "stable")
        assert receipt.payload["requestFingerprint"].startswith("v1:")
        assert factory.runs == 1
    replacement = CountingFactory()
    reopened = DeepCodeApplication.open(
        tmp_path / ("rebuilt.sqlite3" if rebuild else "state.sqlite3"),
        session_factory=replacement,
    )
    try:
        item = reopened.turns.read_input(thread_id, "stable")
        assert (
            item.payload["requestFingerprint"] == receipt.payload["requestFingerprint"]
        )
        if rebuild:
            with pytest.raises(ProjectNotTrustedError):
                reopened.turns.start(thread_id, prompt="run", message_id="stable")
            reopened.projects.update(
                reopened.threads.read(thread_id).project_id,
                trust_state=TrustState.TRUSTED,
            )
        duplicate = reopened.turns.start(thread_id, prompt="run", message_id="stable")
        assert duplicate.turn.id == item.turn_id
        assert duplicate.turn.status.value == "completed"
        assert replacement.runs == 0
        with pytest.raises(DuplicateMessageConflictError):
            reopened.turns.start(
                thread_id, prompt="run", message_id="stable", model="changed"
            )
    finally:
        reopened.close()


def test_legacy_receipt_checks_supplied_selection_without_reexecuting(tmp_path):
    with application(tmp_path) as (app, thread_id, _factory):
        app.execution_coordinator.pause_admission()
        first = app.turns.start(thread_id, prompt="run", message_id="legacy")
        item = app.turns.read_input(thread_id, "legacy")
        payload = {
            key: value
            for key, value in item.payload.items()
            if key != "requestFingerprint"
        }
        with app.database.transaction() as connection:
            connection.execute(
                "UPDATE items SET payload_json = ? WHERE id = ?",
                (json.dumps(payload), item.id),
            )
        assert (
            app.turns.start(thread_id, prompt="run", message_id="legacy").turn.id
            == first.turn.id
        )
        with pytest.raises(DuplicateMessageConflictError):
            app.turns.start(
                thread_id, prompt="run", message_id="legacy", model="changed"
            )


def test_steer_duplicate_must_target_the_same_turn(tmp_path):
    with application(tmp_path) as (app, thread_id, _factory):
        app.execution_coordinator.pause_admission()
        first = app.turns.start(
            thread_id, prompt="steering text", message_id="steer-key"
        )
        # Seed a persisted steer receipt; duplicate lookup must work after the
        # live mailbox is gone, without accepting a different target Turn.
        item = app.turns.read_input(thread_id, "steer-key")
        with app.database.transaction() as connection:
            connection.execute(
                "UPDATE items SET payload_json = ? WHERE id = ?",
                (
                    json.dumps(
                        {**item.payload, "source": "steer", "deliveryState": "accepted"}
                    ),
                    item.id,
                ),
            )
        assert app.turns.steer(
            thread_id,
            prompt="steering text",
            message_id="steer-key",
            expected_turn_id=first.turn.id,
        ).duplicate
        with pytest.raises(DuplicateMessageConflictError):
            app.turns.steer(
                thread_id,
                prompt="steering text",
                message_id="steer-key",
                expected_turn_id="different-turn",
            )
