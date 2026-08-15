"""Offline tests for the interactive TUI (piped mode, scripted provider).

The TUI's InputReader falls back to plain stdin when not a TTY, so the whole
REPL is drivable by monkeypatched stdin — no pty needed. The provider is
scripted (no network); session persistence goes to a tmp store via
DEEPCODE_SESSIONS_DIR.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path
from typing import Any, ClassVar

import pytest
from rich.console import Console

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cli.tui.app as tui_app
from cli.transcript import TranscriptMode
from cli.tui.input import InputInterrupted, InputReader, expand_file_refs
from cli.tui.renderer import EventRenderer
from core import agent_setup
from core.events import (
    AgentMessage,
    AgentMessageCompleted,
    AgentMessageDelta,
    AgentReasoningCompleted,
    AgentReasoningDelta,
    AgentReasoningStarted,
    Event,
    ToolCompleted,
)
from core.providers.base import LLMResponse, ToolCallRequest
from core.reasoning import ReasoningAvailability, ReasoningChannel


class _ScriptedProvider:
    def __init__(
        self,
        replies: list[Any],
        *,
        first_call_delay: float = 0,
    ):
        self.replies = list(replies)
        self.calls = 0
        self.first_call_delay = first_call_delay

    def get_default_model(self):
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any):
        i = min(self.calls, len(self.replies) - 1)
        self.calls += 1
        if i == 0 and self.first_call_delay:
            await asyncio.sleep(self.first_call_delay)
        reply = self.replies[i]
        if isinstance(reply, LLMResponse):
            return reply
        return LLMResponse(content=reply, finish_reason="stop")


class _Profile:
    model = "fake-model"


def _patch_provider(monkeypatch, provider):
    monkeypatch.setattr(
        agent_setup, "get_workflow_provider", lambda **kw: (provider, _Profile())
    )
    monkeypatch.setattr(
        agent_setup,
        "get_runtime",
        lambda: type("R", (), {"config": type("C", (), {"security": None})()})(),
    )


def _run_tui(
    monkeypatch,
    tmp_path,
    stdin_text: str,
    replies: list[Any],
    workspace: str = "ws",
    first_call_delay: float = 0,
) -> tuple[int, Any]:
    (tmp_path / workspace).mkdir(parents=True, exist_ok=True)
    provider = _ScriptedProvider(
        replies,
        first_call_delay=first_call_delay,
    )
    _patch_provider(monkeypatch, provider)
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DEEPCODE_SESSIONS_DIR", str(tmp_path / "sessions"))
    # Fresh default store per test (the singleton caches the env root).
    import core.sessions.store as store_mod

    monkeypatch.setattr(store_mod, "_DEFAULT_STORE", None)
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    rc = tui_app.main(["--workspace", str(tmp_path / workspace), "--trust"])
    return rc, provider


def test_multi_turn_conversation(monkeypatch, tmp_path, capsys):
    rc, provider = _run_tui(
        monkeypatch,
        tmp_path,
        "first task\nsecond task\n/exit\n",
        ["reply one", "reply two"],
    )
    assert rc == 0
    assert provider.calls == 2
    out = capsys.readouterr().out
    assert "reply one" in out and "reply two" in out


def test_noninteractive_tui_requires_trust_without_creating_a_session(
    monkeypatch,
    tmp_path,
    capsys,
):
    from core.sessions import SessionStore

    workspace = tmp_path / "untrusted"
    workspace.mkdir()
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DEEPCODE_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setattr("sys.stdin", io.StringIO("/exit\n"))

    assert tui_app.main(["--workspace", str(workspace)]) == 1

    assert SessionStore(tmp_path / "sessions").list_sessions() == []
    assert "--trust" in capsys.readouterr().err


def test_slash_help_lists_registry(monkeypatch, tmp_path, capsys):
    rc, _ = _run_tui(monkeypatch, tmp_path, "/help\n/exit\n", ["unused"])
    assert rc == 0
    out = capsys.readouterr().out
    for name in (
        "/new",
        "/resume",
        "/model",
        "/effort",
        "/permissions",
        "/transcript",
        "/skills",
        "/skill",
        "/plugins",
        "/mcp",
        "/goal",
        "/clear",
        "/exit",
    ):
        assert name in out


def test_tui_lists_plugin_and_its_mcp_without_a_model_turn(
    monkeypatch,
    tmp_path,
    capsys,
):
    from core.plugins.registry import LocalPluginRegistry
    from core.plugins.resolver import resolve_plugin

    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    (plugin_root / "plugin.json").write_text(
        json.dumps(
            {
                "$schema": (
                    "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
                ),
                "name": "review-tools",
            }
        ),
        encoding="utf-8",
    )
    (plugin_root / "mcp.json").write_text(
        json.dumps(
            {
                "$schema": ("https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"),
                "mcpServers": {
                    "context": {
                        "type": "streamable-http",
                        "url": "http://127.0.0.1:8765/mcp",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    LocalPluginRegistry(tmp_path / "home" / "plugins" / "registry.json").add(
        resolve_plugin(plugin_root)
    )

    rc, provider = _run_tui(
        monkeypatch,
        tmp_path,
        "/plugins\n/mcp\n/exit\n",
        ["unused"],
    )

    assert rc == 0
    assert provider.calls == 0
    output = capsys.readouterr().out
    assert "review-tools" in output
    assert "mcp:ready" in output
    assert "plugin" in output
    assert "review-tools/context" in output


def test_tui_adds_presets_and_really_probes_without_a_model_turn(
    monkeypatch,
    tmp_path,
    capsys,
):
    home = tmp_path / "home"
    fixture = Path(__file__).parent / "fixtures" / "mcp_runtime_server.py"
    home.mkdir()
    (home / "deepcode_config.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fixture": {
                        "type": "stdio",
                        "command": sys.executable,
                        "args": [str(fixture)],
                        "enabled": False,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    rc, provider = _run_tui(
        monkeypatch,
        tmp_path,
        "/mcp presets\n/mcp add context7\n/mcp test fixture\n/mcp\n/exit\n",
        ["unused"],
    )

    assert rc == 0
    assert provider.calls == 0
    output = capsys.readouterr().out
    assert "Bundled MCP presets" in output
    assert "notion" in output
    assert "added MCP preset context7 in disabled state" in output
    assert "MCP test passed for fixture: 3 tools, 1 resources, 1 prompts" in output
    assert "tested" in output


def test_unknown_command_hints(monkeypatch, tmp_path, capsys):
    rc, _ = _run_tui(monkeypatch, tmp_path, "/nope\n/exit\n", ["unused"])
    assert rc == 0
    assert "unknown command" in capsys.readouterr().out


def test_cli_permissions_are_session_scoped_and_full_access_is_confirmed(
    monkeypatch,
    tmp_path,
    capsys,
):
    rc, _ = _run_tui(
        monkeypatch,
        tmp_path,
        (
            "/permissions\n"
            "/permissions read-only\n"
            "/permissions full-access\n"
            "no\n"
            "/permissions\n"
            "/permissions full-access\n"
            "yes\n"
            "/permissions\n"
            "/permissions inherit\n"
            "/permissions unsafe\n"
            "/exit\n"
        ),
        ["unused"],
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "New Turns: inherit · effective: default (ask)" in out
    assert "New Turns: read only" in out
    assert "Full access was not enabled." in out
    assert "New Turns: full access" in out
    assert "New submissions use this access" in out
    assert "usage: /permissions [ask|read-only|full-access|inherit]" in out


def test_cli_renderer_marks_a_nonzero_command_as_failed():
    output = io.StringIO()
    renderer = EventRenderer(
        Console(file=output, color_system=None, width=120),
    )

    renderer.on_event(
        Event(
            "1",
            ToolCompleted(
                "bash-failed",
                "bash",
                True,
                "[exit 2]\nruff failed",
            ),
        )
    )

    rendered = output.getvalue()
    assert "bash failed" in rendered
    assert "[exit 2]" in rendered


def _reasoning_events() -> list[Event]:
    return [
        Event("1", AgentReasoningStarted("reasoning-1", effort="high")),
        Event(
            "2",
            AgentReasoningDelta(
                "reasoning-1",
                ReasoningChannel.SUMMARY,
                "Checked inputs.\nAdditional summary detail.",
            ),
        ),
        Event(
            "3",
            AgentReasoningDelta(
                "reasoning-1",
                ReasoningChannel.PROVIDER_TRACE,
                "Provider trace detail.",
            ),
        ),
        Event(
            "4",
            AgentReasoningCompleted(
                "reasoning-1",
                summary_text="Checked inputs.\nAdditional summary detail.",
                trace_text="Provider trace detail.",
                availability=ReasoningAvailability.AVAILABLE,
                effort="high",
                duration_ms=2200,
            ),
        ),
    ]


@pytest.mark.parametrize(
    ("mode", "included", "excluded"),
    [
        (
            TranscriptMode.NORMAL,
            ("Thought for 2s", "Checked inputs."),
            ("Additional summary detail.", "Provider trace detail."),
        ),
        (
            TranscriptMode.VERBOSE,
            (
                "Thought for 2s",
                "Additional summary detail.",
                "Provider trace detail.",
            ),
            (),
        ),
        (
            TranscriptMode.SUMMARY,
            (),
            ("Thought for 2s", "Checked inputs.", "Provider trace detail."),
        ),
    ],
)
def test_cli_reasoning_respects_transcript_mode(mode, included, excluded):
    output = io.StringIO()
    renderer = EventRenderer(
        Console(file=output, color_system=None, width=120),
        transcript_mode=mode,
    )

    for event in _reasoning_events():
        renderer.on_event(event)

    rendered = output.getvalue()
    for text in included:
        assert text in rendered
    for text in excluded:
        assert text not in rendered


def test_cli_status_line_tracks_live_reasoning_without_printing_deltas():
    output = io.StringIO()
    renderer = EventRenderer(Console(file=output, color_system=None, width=120))
    events = _reasoning_events()

    renderer.on_event(events[0])
    renderer.on_event(events[1])

    status = renderer.status_line()
    assert "Thinking · High" in status
    assert "Checked inputs." in status
    assert output.getvalue() == ""


def test_summary_mode_suppresses_stream_but_keeps_final_answer():
    output = io.StringIO()
    renderer = EventRenderer(
        Console(file=output, color_system=None, width=120),
        transcript_mode=TranscriptMode.SUMMARY,
    )

    renderer.on_event(Event("1", AgentMessageDelta("final answer", "message-1")))
    renderer.on_event(
        Event(
            "2",
            AgentMessageCompleted(
                "message-1",
                "final answer",
            ),
        ),
    )
    renderer.on_event(Event("3", AgentMessage("final answer", "message-1")))

    assert output.getvalue().count("final answer") == 1


def test_interactive_input_accepts_status_and_transcript_callbacks(
    monkeypatch,
    tmp_path,
):
    captured: dict[str, Any] = {}

    class InteractiveInput(io.StringIO):
        def isatty(self) -> bool:
            return True

    class FakePromptSession:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("sys.stdin", InteractiveInput())
    monkeypatch.setattr("cli.tui.input.PromptSession", FakePromptSession)
    monkeypatch.setattr("cli.tui.input._HISTORY_PATH", tmp_path / "history")

    def status_provider() -> str:
        return "Thinking"

    def toggle() -> str:
        return "verbose"

    reader = InputReader(
        str(tmp_path),
        status_provider=status_provider,
        toggle_transcript=toggle,
    )

    assert reader.interactive is True
    assert captured["bottom_toolbar"] is status_provider
    assert captured["refresh_interval"] == 0.5


def test_skill_command_is_one_turn_only_and_persists_invocation_metadata(
    monkeypatch,
    tmp_path,
    capsys,
):
    workspace = tmp_path / "ws"
    skill = workspace / ".deepcode" / "skills" / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: review\n"
        "description: Review a change\n"
        "---\n"
        "Inspect concrete evidence.\n",
        encoding="utf-8",
    )

    rc, provider = _run_tui(
        monkeypatch,
        tmp_path,
        "/skill missing\n/skills\n/skill review\nrun review\n/exit\n",
        ["review complete"],
    )

    assert rc == 0
    assert provider.calls == 1
    output = capsys.readouterr().out
    assert "Skill error:" in output
    assert "selected review for the next turn" in output
    assert "Skill review (explicit)" in output

    from core.sessions.store import SessionStore

    store = SessionStore(tmp_path / "sessions")
    stored = store.get_session(store.list_sessions()[0].session_id)
    assert stored is not None
    invocation = stored.messages[0].metadata["skillInvocations"][0]
    assert invocation["name"] == "review"
    assert invocation["invocation"] == "explicit"


def _configure_fake_goal_provider(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    (home / "deepcode_config.json").write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "connection": "legacy",
                        "model": "fake-model",
                    }
                },
                "providers": {
                    "profiles": {
                        "legacy": {
                            "template": "openai",
                            "manualModels": ["fake-model"],
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def test_goal_command_uses_shared_goal_ledger_and_selected_skill(
    monkeypatch,
    tmp_path,
    capsys,
):
    from core.domain import ThreadGoalStatus
    from core.sessions import SessionStore, ThreadGoalStore

    workspace = tmp_path / "ws"
    skill = workspace / ".deepcode" / "skills" / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: review\n"
        "description: Review the result\n"
        "---\n"
        "Inspect concrete evidence before declaring completion.\n",
        encoding="utf-8",
    )
    _configure_fake_goal_provider(monkeypatch, tmp_path)

    rc, provider = _run_tui(
        monkeypatch,
        tmp_path,
        "/skill review\n/goal inspect and finish the work\n/goal wait\n/exit\n",
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(id="goal-read", name="get_goal", arguments={})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="goal-complete",
                        name="update_goal",
                        arguments={
                            "status": "complete",
                            "reason": "The requested work is complete.",
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            "goal work complete",
        ],
    )

    assert rc == 0
    assert provider.calls == 3
    output = capsys.readouterr().out
    assert "Goal complete" in output
    assert "The requested work is complete." in output
    assert "get_goal" in output
    assert "update_goal" in output

    store = SessionStore(tmp_path / "sessions")
    summary = store.list_sessions()[0]
    goal = ThreadGoalStore(store).read(summary.session_id)
    assert goal is not None
    assert goal.status is ThreadGoalStatus.COMPLETE
    assert goal.skill_ids
    stored = store.get_session(summary.session_id)
    assert stored is not None
    invocations = stored.messages[0].metadata["skillInvocations"]
    assert invocations[0]["name"] == "review"


def test_goal_command_renders_tools_from_an_automatic_continuation(
    monkeypatch,
    tmp_path,
    capsys,
):
    _configure_fake_goal_provider(monkeypatch, tmp_path)

    rc, provider = _run_tui(
        monkeypatch,
        tmp_path,
        "/goal finish across turns\n/goal wait\n/exit\n",
        [
            "The first Turn gathered evidence; more work remains.",
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="goal-complete-on-continuation",
                        name="update_goal",
                        arguments={
                            "status": "complete",
                            "reason": "The continuation finished the work.",
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            "The Goal is complete.",
        ],
    )

    assert rc == 0
    assert provider.calls == 3
    output = capsys.readouterr().out
    assert "update_goal" in output
    assert "The continuation finished the work." in output


def test_goal_edit_and_steer_remain_available_while_work_runs_in_background(
    monkeypatch,
    tmp_path,
    capsys,
):
    from core.domain import ThreadGoalStatus
    from core.sessions import SessionStore, ThreadGoalStore

    _configure_fake_goal_provider(monkeypatch, tmp_path)
    rc, _provider = _run_tui(
        monkeypatch,
        tmp_path,
        (
            "/goal preserve the current behavior\n"
            "Keep the public API compatible.\n"
            "/goal edit preserve the behavior and public API\n"
            "/goal pause\n"
            "/exit\n"
        ),
        ["work remains"],
        first_call_delay=0.5,
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "Steered the active turn" in output or "Queued — runs after" in output
    assert "Goal saved with the same identity" in output
    store = SessionStore(tmp_path / "sessions")
    goal = ThreadGoalStore(store).read(store.list_sessions()[0].session_id)
    assert goal is not None
    assert goal.status is ThreadGoalStatus.PAUSED
    assert goal.objective == "preserve the behavior and public API"


def test_goal_edit_uses_stable_identity_without_a_revision_retry_loop():
    from types import SimpleNamespace

    from cli.tui.goal_controller import TuiGoalController
    from core.domain import ThreadGoal

    thread_id = "ses_goal_edit_retry"
    original = ThreadGoal(
        thread_id=thread_id,
        objective="original objective",
    )

    class GoalExtension:
        goal = original
        edit_calls: ClassVar[list[dict[str, Any]]] = []

        def read(self, requested_thread_id):
            assert requested_thread_id == thread_id
            return self.goal

        def edit(self, requested_thread_id, **kwargs):
            assert requested_thread_id == thread_id
            self.edit_calls.append(kwargs)
            self.goal = ThreadGoal(
                thread_id=thread_id,
                id=self.goal.id,
                objective=kwargs["objective"],
                status=self.goal.status,
                token_budget=kwargs["token_budget"],
                tokens_used=self.goal.tokens_used,
                time_used_seconds=self.goal.time_used_seconds,
                skill_ids=kwargs["skill_ids"],
                created_at=self.goal.created_at,
            )
            return self.goal

    extension = GoalExtension()
    application = SimpleNamespace(
        goals=extension,
    )
    owner = SimpleNamespace(
        thread_client=SimpleNamespace(
            application=application,
            session_id=thread_id,
        )
    )
    controller = TuiGoalController(owner)

    result = controller._edit("revised objective", resume=False)

    assert len(extension.edit_calls) == 1
    assert extension.edit_calls[0]["expected_goal_id"] == original.id
    assert extension.edit_calls[0]["continue_work"] is True
    assert extension.goal.id == original.id
    assert extension.goal.objective == "revised objective"
    assert "same identity" in result.message


def test_goal_continue_command_uses_the_shared_goal_extension():
    from types import SimpleNamespace

    from cli.tui.goal_controller import TuiGoalController
    from core.application.goal_extension import (
        GoalContinueDisposition,
        GoalContinueResult,
    )
    from core.domain import ThreadGoal

    thread_id = "ses_goal_continue"
    goal = ThreadGoal(thread_id=thread_id, objective="finish the task")

    class GoalExtension:
        def read(self, requested_thread_id):
            assert requested_thread_id == thread_id
            return goal

        def continue_goal(
            self,
            requested_thread_id,
            *,
            expected_goal_id,
            **_kwargs,
        ):
            assert requested_thread_id == thread_id
            assert expected_goal_id == goal.id
            return GoalContinueResult(
                goal=goal,
                disposition=GoalContinueDisposition.STARTED,
                turn_id="turn_000000000000000000000001",
            )

    controller = TuiGoalController(
        SimpleNamespace(
            thread_client=SimpleNamespace(
                application=SimpleNamespace(goals=GoalExtension()),
                session_id=thread_id,
            )
        )
    )

    result = asyncio.run(controller.execute("continue"))

    assert "continuation started" in result.message
    assert result.refresh_session is True


def test_new_resets_history_and_model_switch_keeps_it(monkeypatch, tmp_path, capsys):
    rc, _provider = _run_tui(
        monkeypatch,
        tmp_path,
        "hello\n/new\n/model other-model\n/exit\n",
        ["hi there"],
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "started a new conversation" in out
    assert "model switched to other-model" in out


def test_effort_switch_preserves_history_and_session_selection(
    monkeypatch, tmp_path, capsys
):
    rc, provider = _run_tui(
        monkeypatch,
        tmp_path,
        "hello\n/effort high\ncontinue\n/exit\n",
        ["first reply", "second reply"],
    )

    assert rc == 0
    assert provider.calls == 2
    assert "effort switched to high" in capsys.readouterr().out

    from core.sessions.store import SessionStore

    store = SessionStore(tmp_path / "sessions")
    stored = store.get_session(store.list_sessions()[0].session_id)
    assert stored is not None
    assert stored.metadata["reasoning_effort"] == "high"
    assert [message.content for message in stored.messages] == [
        "hello",
        "first reply",
        "continue",
        "second reply",
    ]


def test_clear_keeps_the_same_persistent_session(monkeypatch, tmp_path, capsys):
    rc, _ = _run_tui(
        monkeypatch,
        tmp_path,
        "before clear\n/clear\nafter clear\n/exit\n",
        ["first reply", "second reply"],
    )
    assert rc == 0

    from core.sessions.store import SessionStore

    sessions = SessionStore(tmp_path / "sessions").list_sessions()
    assert len(sessions) == 1
    assert sessions[0].message_count == 4


def test_session_persisted_and_resumable(monkeypatch, tmp_path, capsys):
    # Conversation 1: one turn, then read the store to find the session id.
    rc, _ = _run_tui(
        monkeypatch, tmp_path, "remember the number 42\n/exit\n", ["noted: 42"]
    )
    assert rc == 0
    from core.sessions.store import SessionStore

    store = SessionStore(tmp_path / "sessions")
    sessions = store.list_sessions()
    assert len(sessions) == 1
    sid = sessions[0].session_id
    assert sessions[0].message_count == 2  # user + assistant
    # The session was titled from the first message.
    assert "remember the number" in sessions[0].title

    # Conversation 2: /resume restores the transcript into the live agent.
    rc2, _provider2 = _run_tui(
        monkeypatch,
        tmp_path,
        f"/resume {sid}\nwhat number?\n/exit\n",
        ["you said 42"],
    )
    assert rc2 == 0
    out = capsys.readouterr().out
    assert f"resumed {sid}" in out


def test_resume_without_arg_lists_sessions(monkeypatch, tmp_path, capsys):
    _run_tui(monkeypatch, tmp_path, "task one\n/exit\n", ["done"])
    capsys.readouterr()
    rc, _ = _run_tui(monkeypatch, tmp_path, "/resume\n/exit\n", ["unused"])
    assert rc == 0
    assert "recent sessions" in capsys.readouterr().out


# --- directory scoping (P2-L5c: central storage, per-directory view) --------


def test_session_metadata_stamped(monkeypatch, tmp_path, capsys):
    _run_tui(monkeypatch, tmp_path, "hello\n/exit\n", ["hi"], workspace="A")
    from core.sessions.store import SessionStore

    store = SessionStore(tmp_path / "sessions")
    stored = store.get_session(store.list_sessions()[0].session_id)
    assert stored.metadata.get("kind") == "tui"
    assert stored.metadata.get("workspace") == str(tmp_path / "A")


def test_resume_scoped_to_directory(monkeypatch, tmp_path, capsys):
    # A conversation born in directory A...
    _run_tui(monkeypatch, tmp_path, "task in A\n/exit\n", ["done A"], workspace="A")
    capsys.readouterr()
    # ...is invisible from directory B's default picker, visible via `all`
    # with its origin annotated.
    rc, _ = _run_tui(
        monkeypatch,
        tmp_path,
        "/resume\n/resume all\n/exit\n",
        ["unused"],
        workspace="B",
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "no sessions for this directory" in out
    assert "task in A" in out  # shown in the `all` view
    assert str(tmp_path / "A") in out  # origin directory annotated


def test_cross_directory_resume_hints_origin(monkeypatch, tmp_path, capsys):
    _run_tui(monkeypatch, tmp_path, "remember A\n/exit\n", ["ok"], workspace="A")
    capsys.readouterr()
    from core.sessions.store import SessionStore

    store = SessionStore(tmp_path / "sessions")
    sid = store.list_sessions()[0].session_id
    rc, _ = _run_tui(
        monkeypatch, tmp_path, f"/resume {sid}\n/exit\n", ["unused"], workspace="B"
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert f"resumed {sid}" in out
    assert "started in" in out and str(tmp_path / "A") in out


def test_same_directory_resume_has_no_hint(monkeypatch, tmp_path, capsys):
    _run_tui(monkeypatch, tmp_path, "stay here\n/exit\n", ["ok"], workspace="A")
    capsys.readouterr()
    from core.sessions.store import SessionStore

    store = SessionStore(tmp_path / "sessions")
    sid = store.list_sessions()[0].session_id
    _run_tui(
        monkeypatch, tmp_path, f"/resume {sid}\n/exit\n", ["unused"], workspace="A"
    )
    out = capsys.readouterr().out
    assert f"resumed {sid}" in out
    assert "started in" not in out


def test_expand_file_refs(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "notes.txt").write_text("the secret is blue\n")
    expanded = expand_file_refs("summarize @notes.txt please", str(ws))
    assert "the secret is blue" in expanded
    assert "attached file: notes.txt" in expanded
    # Non-file tokens stay untouched, no attachment added.
    assert expand_file_refs("email @bob about it", str(ws)) == "email @bob about it"


def test_file_refs_fenced_to_workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (tmp_path / "outside.txt").write_text("secret")
    out = expand_file_refs("read @../outside.txt", str(ws))
    assert "secret" not in out  # escape attempt is not attached


@pytest.mark.asyncio
async def test_interactive_ctrl_c_becomes_a_turn_interrupt_request() -> None:
    class InterruptingPrompt:
        async def prompt_async(self, _prompt: str) -> str:
            raise KeyboardInterrupt

    reader = InputReader.__new__(InputReader)
    reader.interactive = True
    reader._prompt_session = InterruptingPrompt()

    with pytest.raises(InputInterrupted):
        await reader.read()


def test_rename_command_updates_the_stored_session_title(monkeypatch, tmp_path, capsys):
    rc, _provider = _run_tui(
        monkeypatch,
        tmp_path,
        "hello\n/rename Renamed conversation\n/exit\n",
        ["hi"],
    )
    assert rc == 0
    assert "session renamed to Renamed conversation" in capsys.readouterr().out

    from core.sessions.store import SessionStore

    store = SessionStore(tmp_path / "sessions")
    assert store.list_sessions()[0].title == "Renamed conversation"


def test_delete_command_removes_a_stored_session_but_needs_an_id(
    monkeypatch, tmp_path, capsys
):
    rc, _provider = _run_tui(
        monkeypatch,
        tmp_path,
        "first conversation\n/exit\n",
        ["reply"],
    )
    assert rc == 0

    from core.sessions.store import SessionStore

    store = SessionStore(tmp_path / "sessions")
    victim = store.list_sessions()[0].session_id

    rc, _provider = _run_tui(
        monkeypatch,
        tmp_path,
        f"/delete\n/delete {victim}\n/exit\n",
        [],
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "usage: /delete" in out
    assert f"deleted session {victim}" in out

    store = SessionStore(tmp_path / "sessions")
    assert victim not in [s.session_id for s in store.list_sessions()]


def test_retry_command_reruns_the_last_turn_as_a_new_turn(
    monkeypatch, tmp_path, capsys
):
    rc, provider = _run_tui(
        monkeypatch,
        tmp_path,
        "solve the task\n/retry\n/exit\n",
        ["first answer", "second answer"],
    )
    assert rc == 0
    assert provider.calls == 2
    out = capsys.readouterr().out
    assert "retrying: solve the task" in out

    from core.sessions.store import SessionStore

    store = SessionStore(tmp_path / "sessions")
    stored = store.get_session(store.list_sessions()[0].session_id)
    assert stored is not None
    assert [message.content for message in stored.messages] == [
        "solve the task",
        "first answer",
        "solve the task",
        "second answer",
    ]


def test_command_argument_completion_comes_from_the_registry(tmp_path):
    from prompt_toolkit.document import Document

    from cli.tui.input import TuiCompleter

    completer = TuiCompleter(str(tmp_path))
    completer.set_argument_provider(
        lambda name, prefix: ["86db0319"] if name == "resume" else []
    )

    def texts(line: str) -> list[str]:
        return [
            completion.text
            for completion in completer.get_completions(Document(line), None)
        ]

    assert texts("/resume 86") == ["86db0319"]
    assert texts("/resume 86db0319 extra") == []  # only the first argument
    assert texts("/rename my title") == []  # no provider, no path noise


def test_picker_filters_by_title_and_wraps_navigation():
    from cli.tui.picker import Picker, PickerItem, PickerScope

    picker = Picker(
        [
            PickerScope(
                "scope",
                [
                    PickerItem(1, "Alpha task", "aa11"),
                    PickerItem(2, "Beta task", "bb22"),
                    PickerItem(3, "Gamma", "cc33", disabled_reason="current session"),
                ],
            )
        ],
        title="Pick",
    )
    picker._buffer.text = "beta"
    assert [item.title for item in picker._filtered()] == ["Beta task"]
    picker._buffer.text = "bb2"  # detail (id) matches too
    assert [item.title for item in picker._filtered()] == ["Beta task"]
    picker._buffer.text = ""
    picker._move(-1)
    assert picker._selected == 2  # wraps to the end
    picker._move(1)
    assert picker._selected == 0


def test_picker_shift_tab_cycles_the_highlighted_variant():
    from cli.tui.picker import Picker, PickerItem, PickerScope, PickerVariant

    ladder = tuple(PickerVariant(value=v, label=v) for v in ("auto", "high", "xhigh"))
    item = PickerItem(1, "route", variants=ladder, initial_variant=1)
    plain = PickerItem(2, "no ladder")
    picker = Picker([PickerScope("scope", [item, plain])], title="Pick")
    assert picker._variant_of(item).value == "high"
    picker._cycle_variant(item)
    assert picker._variant_of(item).value == "xhigh"
    picker._cycle_variant(item)
    assert picker._variant_of(item).value == "auto"  # wraps
    assert picker._variant_of(plain) is None
    picker._cycle_variant(plain)  # no-op, no crash
    assert picker._variant_of(plain) is None


def test_model_picker_offers_catalog_routes_and_switches(monkeypatch):
    from types import SimpleNamespace

    import cli.tui.commands as commands_mod

    seen: dict[str, Any] = {}

    class FakePicker:
        def __init__(self, scopes, *, title, initial_scope=0, variant_hint="variant"):
            seen["scopes"] = scopes
            seen["variant_hint"] = variant_hint

        async def run(self):
            from cli.tui.picker import PickerChoice

            return PickerChoice(value=("openrouter", "model-b"), variant="high")

    monkeypatch.setattr(commands_mod, "Picker", FakePicker)

    async def switch_model(model, *, connection_id=None, reasoning_effort=None):
        seen["switch"] = (connection_id, model, reasoning_effort)

    app = SimpleNamespace(
        reader=SimpleNamespace(interactive=True),
        connection_views=lambda: [
            {
                "id": "openrouter",
                "configured": True,
                "enabled": True,
                "manualModels": ["model-a", "model-b"],
            },
            {"id": "unconfigured", "configured": False, "enabled": True},
        ],
        thread_client=SimpleNamespace(
            execution_profile=SimpleNamespace(
                connection_id="openrouter", model_id="model-a"
            ),
            has_active_turn=lambda: False,
        ),
        switch_model=switch_model,
        model="model-b",
        requested_reasoning_effort="auto",
        reasoning_options=lambda model_id, connection_id=None: ("high", "xhigh"),
        model_catalog_note=lambda: None,
    )
    status = asyncio.run(commands_mod._cmd_model(app, ""))
    assert seen["switch"] == ("openrouter", "model-b", "high")
    assert "model switched to model-b" in status
    items = list(seen["scopes"][0].items)
    assert [item.title for item in items] == [
        "openrouter/model-a",
        "openrouter/model-b",
    ]
    assert items[0].detail == "current"


def test_resume_picker_marks_current_session_and_resumes_choice(monkeypatch):
    from types import SimpleNamespace

    import cli.tui.commands as commands_mod

    seen: dict[str, Any] = {}

    class FakePicker:
        def __init__(self, scopes, *, title, initial_scope=0):
            seen["scopes"] = scopes
            seen["initial_scope"] = initial_scope

        async def run(self):
            from cli.tui.picker import PickerChoice

            return PickerChoice(value="bb22cc33")

    monkeypatch.setattr(commands_mod, "Picker", FakePicker)

    def listing(session_id, title, current=False):
        from datetime import UTC, datetime

        return SimpleNamespace(
            session_id=session_id,
            title=title,
            message_count=2,
            updated_at=datetime.now(UTC),
            workspace="/ws",
            is_current=current,
        )

    rows = [
        listing("aa11bb22", "Current one", current=True),
        listing("bb22cc33", "Older one"),
    ]
    app = SimpleNamespace(
        reader=SimpleNamespace(interactive=True),
        workspace="/ws",
        thread_client=SimpleNamespace(
            list_recent=lambda *, limit, include_all: rows,
            session_id="aa11bb22",
        ),
        resume_conversation=lambda sid: seen.setdefault("resumed", sid) and 2 or 2,
        render_resume_tail=lambda: None,
        bridge=SimpleNamespace(stored_workspace=lambda: "/ws"),
    )
    status = asyncio.run(commands_mod._cmd_resume(app, ""))
    assert seen["resumed"] == "bb22cc33"
    assert "resumed bb22cc33" in status
    local_items = list(seen["scopes"][0].items)
    assert local_items[0].disabled_reason == "current session"
    assert local_items[1].disabled_reason is None
