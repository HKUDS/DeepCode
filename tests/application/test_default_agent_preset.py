"""The configured default agent preset fills the blank at Session creation.

`agents.defaults.defaultPreset` names the composition NEW Sessions start
with when the caller does not pick one. It goes through the same by-value
snapshot as an explicit choice, an explicit choice always wins, and an
unresolvable name never blocks creating a Session.
"""

import json
from pathlib import Path

from core.agent_presets import METADATA_KEY as PRESET_METADATA_KEY
from core.application import DeepCodeApplication
from core.domain import TrustState

_PRESET_BODY = (
    "---\n"
    "name: reviewer\n"
    "description: Reads code and reports\n"
    "tools: read, grep\n"
    "---\n"
    "You review code carefully.\n"
)


def _workspace_with_preset(tmp_path: Path, *, configured: str | None) -> Path:
    workspace = tmp_path / "ws"
    presets = workspace / ".agents" / "presets"
    presets.mkdir(parents=True)
    (presets / "reviewer.md").write_text(_PRESET_BODY, encoding="utf-8")
    if configured is not None:
        (workspace / "deepcode_config.json").write_text(
            json.dumps({"agents": {"defaults": {"defaultPreset": configured}}}),
            encoding="utf-8",
        )
    return workspace


def _stored_preset(application: DeepCodeApplication, thread_id: str):
    session = application.session_store.get_session(thread_id)
    assert session is not None
    return session.metadata.get(PRESET_METADATA_KEY)


def test_configured_default_preset_is_snapshotted_at_creation(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "home"))
    workspace = _workspace_with_preset(tmp_path, configured="reviewer")
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(str(workspace), trust_state=TrustState.TRUSTED)

    thread = application.threads.start(project.id, title="Defaulted")

    stored = _stored_preset(application, thread.id)
    assert stored is not None and stored["id"] == "reviewer"
    # By value: the prompt travels with the Session, not a reference.
    assert "review code carefully" in stored["prompt"]


def test_explicit_preset_choice_wins_over_the_configured_default(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "home"))
    workspace = _workspace_with_preset(tmp_path, configured="reviewer")
    presets = workspace / ".agents" / "presets"
    (presets / "writer.md").write_text(
        "---\nname: writer\ndescription: Writes docs\ntools: read\n---\nWrite.\n",
        encoding="utf-8",
    )
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(str(workspace), trust_state=TrustState.TRUSTED)

    thread = application.threads.start(
        project.id, title="Explicit", agent_preset="writer"
    )

    stored = _stored_preset(application, thread.id)
    assert stored is not None and stored["id"] == "writer"


def test_unresolvable_default_preset_never_blocks_session_creation(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "home"))
    workspace = _workspace_with_preset(tmp_path, configured="no-such-preset")
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(str(workspace), trust_state=TrustState.TRUSTED)

    thread = application.threads.start(project.id, title="Still created")

    assert _stored_preset(application, thread.id) is None


def test_no_configured_default_keeps_sessions_preset_free(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "home"))
    workspace = _workspace_with_preset(tmp_path, configured=None)
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(str(workspace), trust_state=TrustState.TRUSTED)

    thread = application.threads.start(project.id, title="Plain")

    assert _stored_preset(application, thread.id) is None
