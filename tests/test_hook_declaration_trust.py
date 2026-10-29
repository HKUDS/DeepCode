"""Project hook approval: declaration digest, gate, Project record, end to end.

Contract under test:

- the digest covers the canonical declarations (event, matcher, type,
  command, timeout, async, priority), so a real change gives a new digest
  while key order, whitespace and other keys do not;
- a project hook file appearing, breaking or disappearing changes the digest;
- the digest names files relative to the workspace, so a symlinked path or
  another checkout with the same declarations gets the same digest;
- with an approval, project hooks run only under the approved digest, and one
  warning is given only when project hooks were actually left out;
- the approved digest lives on the Project: trusting records it, a settings
  update cannot change it, a pre-existing trusted Project adopts its current
  hooks once, and approving a digest that is no longer current is refused;
- end to end over the App Server: unapproved hooks do not run on a Turn,
  ``hooks/approve`` makes them run, a later change skips them again.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.application.errors import ConflictError  # noqa: E402
from core.application.project_service import (  # noqa: E402
    HOOKS_DIGEST_SETTING,
    ProjectService,
)
from core.domain.project import Project, TrustState  # noqa: E402
from core.harness.hooks.discovery import discover_hooks  # noqa: E402
from core.harness.hooks.trust import (  # noqa: E402
    ProjectHookApproval,
    declaration_digest,
)

_DECLARATION_V1 = {
    "hooks": {
        "PostToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {"type": "command", "command": "python lint.py", "timeout": 30}
                ],
            }
        ]
    }
}


def _write(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _project_hooks(workspace: Path) -> Path:
    return workspace / ".deepcode" / "hooks.json"


# -- digest ------------------------------------------------------------------


def test_digest_is_stable_across_cosmetic_churn(tmp_path: Path) -> None:
    path = _write(_project_hooks(tmp_path), _DECLARATION_V1)
    before = declaration_digest(str(tmp_path))

    churned = {
        "version": 2,
        "hooks": {
            "PostToolUse": [
                {
                    "hooks": [
                        {
                            "timeout": 30,
                            "command": "python lint.py",
                            "statusMessage": "cosmetic, not hashed",
                            "type": "command",
                        }
                    ],
                    "matcher": "Bash",
                }
            ]
        },
    }
    path.write_text(json.dumps(churned, indent=4), encoding="utf-8")
    assert declaration_digest(str(tmp_path)) == before


def test_any_semantic_change_produces_a_new_digest(tmp_path: Path) -> None:
    _write(_project_hooks(tmp_path), _DECLARATION_V1)
    before = declaration_digest(str(tmp_path))

    for mutation in (
        {"command": "python lint.py && rm -rf /"},
        {"timeout": 5},
        {"async": True},
    ):
        payload = json.loads(json.dumps(_DECLARATION_V1))
        payload["hooks"]["PostToolUse"][0]["hooks"][0].update(mutation)
        _write(_project_hooks(tmp_path), payload)
        assert declaration_digest(str(tmp_path)) != before

    payload = json.loads(json.dumps(_DECLARATION_V1))
    payload["hooks"]["PostToolUse"][0]["matcher"] = "Edit"
    _write(_project_hooks(tmp_path), payload)
    assert declaration_digest(str(tmp_path)) != before


def test_files_appearing_breaking_or_moving_change_the_digest(
    tmp_path: Path,
) -> None:
    empty = declaration_digest(str(tmp_path))
    assert declaration_digest(str(tmp_path)) == empty  # stable without files

    _write(_project_hooks(tmp_path), _DECLARATION_V1)
    in_deepcode = declaration_digest(str(tmp_path))
    assert in_deepcode != empty

    # The same declarations in the Claude-compatible file are another file.
    _project_hooks(tmp_path).unlink()
    _write(tmp_path / ".claude" / "settings.json", _DECLARATION_V1)
    assert declaration_digest(str(tmp_path)) not in {empty, in_deepcode}

    (tmp_path / ".claude" / "settings.json").write_text("{not json", "utf-8")
    broken = declaration_digest(str(tmp_path))
    assert broken not in {empty, in_deepcode}


def test_digest_does_not_depend_on_the_path_the_workspace_is_opened_by(
    tmp_path: Path,
) -> None:
    real = tmp_path / "real"
    _write(_project_hooks(real), _DECLARATION_V1)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    copy = tmp_path / "another-checkout"
    _write(_project_hooks(copy), _DECLARATION_V1)

    digest = declaration_digest(str(real))
    assert declaration_digest(str(link)) == digest
    assert declaration_digest(str(copy)) == digest

    # Approved through the real path, the hooks run through the symlink.
    approved = discover_hooks(
        str(link),
        home=str(tmp_path / "home"),
        project_hook_approval=ProjectHookApproval(digest),
    )
    assert [handler.source for handler in approved.handlers] == ["project"]
    assert not approved.project_hooks_skipped


# -- discovery gate ----------------------------------------------------------


def test_gate_skips_unapproved_project_hooks_with_one_warning(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    home = tmp_path / "home"
    _write(_project_hooks(workspace), _DECLARATION_V1)
    _write(
        home / ".deepcode" / "hooks.json",
        {"hooks": {"Stop": [{"hooks": [{"command": "echo user"}]}]}},
    )

    ungated = discover_hooks(str(workspace), home=str(home))
    assert sorted(h.source for h in ungated.handlers) == ["project", "user"]
    assert ungated.warnings == []

    for approval in (ProjectHookApproval(None), ProjectHookApproval("0" * 64)):
        gated = discover_hooks(
            str(workspace), home=str(home), project_hook_approval=approval
        )
        # The user's own hooks are never gated.
        assert [h.source for h in gated.handlers] == ["user"]
        assert gated.project_hooks_skipped
        assert len(gated.warnings) == 1
        assert "not run" in gated.warnings[0]
        assert "deepcode hooks approve" in gated.warnings[0]

    approved = discover_hooks(
        str(workspace),
        home=str(home),
        project_hook_approval=ProjectHookApproval(ungated.project_digest),
    )
    assert sorted(h.source for h in approved.handlers) == ["project", "user"]
    assert approved.warnings == []


def test_gate_is_silent_when_the_project_declares_no_hooks(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    home = tmp_path / "home"
    _write(
        home / ".deepcode" / "hooks.json",
        {"hooks": {"Stop": [{"hooks": [{"command": "echo user"}]}]}},
    )
    result = discover_hooks(
        str(workspace),
        home=str(home),
        project_hook_approval=ProjectHookApproval(None),
    )
    assert [h.source for h in result.handlers] == ["user"]
    assert result.warnings == []
    assert not result.project_hooks_skipped


# -- Project record ----------------------------------------------------------


@pytest.fixture
def projects(tmp_path: Path):
    from core.application import DeepCodeApplication

    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    try:
        yield application.projects
    finally:
        application.close()


def test_trusting_a_project_approves_the_hooks_it_declares(
    tmp_path: Path, projects: ProjectService
) -> None:
    workspace = tmp_path / "ws"
    _write(_project_hooks(workspace), _DECLARATION_V1)
    digest = declaration_digest(str(workspace))

    project = projects.add(str(workspace))
    assert HOOKS_DIGEST_SETTING not in project.settings
    assert projects.hook_approval(project.id) == ProjectHookApproval(None)

    trusted = projects.update(project.id, trust_state=TrustState.TRUSTED)
    assert trusted.settings[HOOKS_DIGEST_SETTING] == digest

    # A settings update neither drops nor sets the approval.
    updated = projects.update(
        project.id, settings={"note": "x", HOOKS_DIGEST_SETTING: "f" * 64}
    )
    assert updated.settings == {"note": "x", HOOKS_DIGEST_SETTING: digest}

    # Trusting again does not re-approve changed hooks.
    payload = json.loads(json.dumps(_DECLARATION_V1))
    payload["hooks"]["PostToolUse"][0]["hooks"][0]["command"] = "python evil.py"
    _write(_project_hooks(workspace), payload)
    again = projects.update(project.id, trust_state=TrustState.TRUSTED)
    assert again.settings[HOOKS_DIGEST_SETTING] == digest
    assert projects.hook_approval(project.id) == ProjectHookApproval(digest)

    added_trusted = projects.add(
        str(tmp_path / "ws"), trust_state=TrustState.TRUSTED
    )  # existing Project: unchanged
    assert added_trusted.settings[HOOKS_DIGEST_SETTING] == digest


def test_a_project_trusted_before_hook_approval_adopts_its_hooks_once(
    tmp_path: Path, projects: ProjectService
) -> None:
    from core.persistence.project_repository import ProjectRepository

    workspace = tmp_path / "ws"
    _write(_project_hooks(workspace), _DECLARATION_V1)
    digest = declaration_digest(str(workspace))
    legacy = Project(
        canonical_path=str(workspace.resolve()),
        display_name="legacy",
        trust_state=TrustState.TRUSTED,
    )
    with projects.database.transaction() as connection:
        ProjectRepository(connection).add(legacy)

    assert projects.hook_approval(legacy.id) == ProjectHookApproval(digest)
    assert projects.read(legacy.id).settings[HOOKS_DIGEST_SETTING] == digest

    payload = json.loads(json.dumps(_DECLARATION_V1))
    payload["hooks"]["PostToolUse"][0]["hooks"][0]["command"] = "python evil.py"
    _write(_project_hooks(workspace), payload)
    assert projects.hook_approval(legacy.id) == ProjectHookApproval(digest)


def test_approving_requires_the_digest_that_is_current(
    tmp_path: Path, projects: ProjectService
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    project = projects.add(str(workspace), trust_state=TrustState.TRUSTED)
    _write(_project_hooks(workspace), _DECLARATION_V1)
    current = declaration_digest(str(workspace))

    with pytest.raises(ConflictError):
        projects.approve_hooks(project.id, "0" * 64)
    assert projects.hook_approval(project.id).approved_digest != current

    approved = projects.approve_hooks(project.id, current)
    assert approved.settings[HOOKS_DIGEST_SETTING] == current
    assert projects.hook_approval(project.id) == ProjectHookApproval(current)


# -- end to end --------------------------------------------------------------


class _Provider:
    def get_default_model(self) -> str:
        return "fake-model"

    async def chat_with_retry(self, **_kwargs: Any):
        from core.providers.base import LLMResponse

        return LLMResponse(content="done", finish_reason="stop")


class _Profile:
    model = "fake-model"


def _write_connection(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "deepcode_config.json").write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {"connection": "router-a", "model": "fake-model"}
                },
                "providers": {
                    "profiles": {
                        "router-a": {"label": "Router A", "template": "openrouter"}
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def _marker_hook(workspace: Path, marker: Path, *, tag: str) -> None:
    script = workspace / ".deepcode" / "mark.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "import sys\n"
        f"with open({str(marker)!r}, 'a', encoding='utf-8') as f:\n"
        "    f.write(sys.argv[1] + '\\n')\n",
        encoding="utf-8",
    )
    _write(
        _project_hooks(workspace),
        {
            "hooks": {
                "UserPromptSubmit": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": f'"{sys.executable}" "{script}" {tag}',
                                "timeout": 30,
                            }
                        ]
                    }
                ]
            }
        },
    )


def _run_turn(application, thread_id: str, prompt: str) -> None:
    from core.domain.turn import TurnStatus

    started = application.turns.start(thread_id, prompt=prompt)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        turn = application.turns.read(started.turn.id).turn
        if turn.status.is_terminal:
            assert turn.status is TurnStatus.COMPLETED, turn
            return
        time.sleep(0.02)
    raise AssertionError("turn did not finish")


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


@pytest.mark.skipif(sys.platform == "win32", reason="hooks run through sh")
def test_project_hooks_run_on_turns_only_once_approved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plain_shell
) -> None:
    from loguru import logger

    from app_server.connection import ConnectionState
    from app_server.dispatcher import Dispatcher
    from app_server.protocol.models import Request
    from core import agent_setup
    from core.application import DeepCodeApplication
    from core.application.agent_adapter import ConfiguredAgentSessionFactory
    from core.harness.permissions import PermissionMode
    from core.sessions import SessionStore

    home = tmp_path / "home"
    _write_connection(home)
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    monkeypatch.setattr(
        agent_setup,
        "get_workflow_provider",
        lambda **_kwargs: (_Provider(), _Profile()),
    )
    notices: list[str] = []
    sink = logger.add(
        lambda message: notices.append(str(message)),
        level="WARNING",
        filter=lambda record: "Project hooks were not run" in record["message"],
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    marker = tmp_path / "hook-ran.log"
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=ConfiguredAgentSessionFactory(
            default_permission_mode=PermissionMode.DEFAULT, streaming=False
        ),
        session_store=SessionStore(tmp_path / "sessions"),
    )
    dispatcher = Dispatcher(application, ConnectionState(application.broker))

    def call(method: str, params: dict[str, Any]) -> Any:
        return dispatcher.dispatch(Request(method, params))

    try:
        call(
            "initialize",
            {
                "protocolVersion": "1.0",
                "clientInfo": {"name": "hook-approval-test", "version": "1"},
            },
        )
        project_id = call("project/add", {"path": str(workspace)})["project"]["id"]
        # Trusted while it declares no hooks; a later pull adds one.
        call("project/update", {"projectId": project_id, "trustState": "trusted"})
        _marker_hook(workspace, marker, tag="v1")
        thread = application.threads.start(
            project_id,
            title="hooks",
            connection_id="router-a",
            model="fake-model",
        )

        listing = call("hooks/list", {"projectId": project_id})
        assert listing["projectHooks"]["status"] == "unapproved"
        assert [hook["source"] for hook in listing["hooks"]] == ["project"]

        _run_turn(application, thread.id, "first")
        assert _lines(marker) == []
        assert len(notices) == 1

        approved = call(
            "hooks/approve",
            {"projectId": project_id, "digest": listing["projectHooks"]["digest"]},
        )
        assert approved["projectHooks"]["status"] == "approved"
        assert (
            approved["projectHooks"]["approvedDigest"]
            == listing["projectHooks"]["digest"]
        )

        _run_turn(application, thread.id, "second")
        assert _lines(marker) == ["v1"]
        assert len(notices) == 1

        _marker_hook(workspace, marker, tag="v2")
        changed = call("hooks/list", {"projectId": project_id})
        assert changed["projectHooks"]["status"] == "unapproved"
        _run_turn(application, thread.id, "third")
        assert _lines(marker) == ["v1"]
        assert len(notices) == 2

        # Approving what was listed before the change is refused.
        with pytest.raises(ConflictError):
            call(
                "hooks/approve",
                {"projectId": project_id, "digest": listing["projectHooks"]["digest"]},
            )
    finally:
        logger.remove(sink)
        application.close()
