"""Agent presets — the dsh skeleton in the cross-product agent-file dialect.

Contract under test:

- discovery walks trust-ranked roots (project > user > system), nearest root
  wins a duplicate id, and the FILE STEM is the identity — frontmatter
  cannot claim another id (dsh's anti-spoofing rule);
- a Claude-Code-dialect agent file loads unchanged: TitleCase tool names
  fold mechanically onto this registry's shape, unknown keys are tolerated;
- broken files stay on the roster with a reason; an unknown id raises with
  the available roster (dsh's two error kinds);
- a preset resolves to a BY-VALUE snapshot persisted in Session metadata:
  editing the file later never changes an existing Session;
- the preset is locked once the conversation has started;
- the resident runtime picks the snapshot up from canonical metadata and a
  different snapshot yields a different runtime key (no stale reuse).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_presets import (
    METADATA_KEY,
    AgentPresetSnapshot,
    BrokenPresetError,
    UnknownPresetError,
    list_agent_presets,
    normalize_tool_name,
    resolve_agent_preset,
)


@pytest.fixture(autouse=True)
def _hermetic_user_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point every user-level root at empty scratch dirs."""
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "deepcode-home"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))


def _write_preset(root: Path, name: str, text: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.md"
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Discovery and dialect
# ---------------------------------------------------------------------------


def test_builtin_roster_ships_with_system_trust() -> None:
    roster = {p.id: p for p in list_agent_presets()}
    # No shipped "default" entry: the default composition is the ABSENCE of
    # a preset, and a roster row for it would double the picker's own
    # "Default" choice (observed live as two identical menu rows).
    assert "default" not in roster
    for expected in ("minimal", "code-reader"):
        assert expected in roster
        assert roster[expected].trust == "system"
        assert roster[expected].broken is None
    assert roster["code-reader"].tools == ("read", "grep", "glob", "skill")


def test_project_root_wins_a_duplicate_id(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _write_preset(
        workspace / ".agents" / "presets",
        "minimal",
        "---\ndescription: project override\ntools: read\n---\nbody",
    )
    roster = {p.id: p for p in list_agent_presets(workspace)}
    assert roster["minimal"].trust == "project"
    assert roster["minimal"].tools == ("read",)


def test_claude_code_agent_file_loads_unchanged(tmp_path: Path) -> None:
    """Zero-copy interop: a CC subagent definition becomes a preset as-is."""
    workspace = tmp_path / "ws"
    _write_preset(
        workspace / ".claude" / "agents",
        "docs-writer",
        "---\n"
        "name: docs-writer\n"
        "description: Writes documentation\n"
        "tools: Read, Grep, WebFetch\n"
        "model: sonnet\n"  # CC-only key: tolerated, advisory
        "---\n"
        "You write excellent documentation.",
    )
    snapshot = resolve_agent_preset("docs-writer", workspace)
    assert snapshot.tools == ("read", "grep", "web_fetch")
    assert "excellent documentation" in snapshot.prompt


def test_identity_is_the_file_stem_not_the_frontmatter_name(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _write_preset(
        workspace / ".agents" / "presets",
        "honest-id",
        "---\nname: code-reader\ndescription: impostor\ntools: bash\n---\n",
    )
    roster = {p.id: p for p in list_agent_presets(workspace)}
    # The shipped code-reader is untouched; the file answers to its stem only.
    assert roster["code-reader"].trust == "system"
    assert roster["code-reader"].tools == ("read", "grep", "glob", "skill")
    assert roster["honest-id"].display_name == "code-reader"  # display only


def test_broken_files_stay_on_the_roster_with_a_reason(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _write_preset(
        workspace / ".agents" / "presets",
        "damaged",
        "---\ntools: [unclosed\n---\nbody",
    )
    roster = {p.id: p for p in list_agent_presets(workspace)}
    assert roster["damaged"].broken is not None
    with pytest.raises(BrokenPresetError, match="damaged"):
        resolve_agent_preset("damaged", workspace)


def test_unknown_id_raises_with_the_available_roster() -> None:
    with pytest.raises(UnknownPresetError) as excinfo:
        resolve_agent_preset("no-such-preset")
    assert "code-reader" in str(excinfo.value)


def test_tool_name_normalization_is_mechanical() -> None:
    assert normalize_tool_name("Read") == "read"
    assert normalize_tool_name("WebFetch") == "web_fetch"
    assert normalize_tool_name(" bash ") == "bash"
    # MCP names are verbatim registry keys — never folded.
    assert normalize_tool_name("mcp__github__createIssue") == "mcp__github__createIssue"


def test_star_and_empty_tool_lists(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _write_preset(
        workspace / ".agents" / "presets",
        "everything",
        "---\ndescription: all\ntools: '*'\n---\n",
    )
    _write_preset(
        workspace / ".agents" / "presets",
        "chat-only",
        "---\ndescription: none\ntools: []\n---\nJust talk.",
    )
    assert resolve_agent_preset("everything", workspace).tools is None
    assert resolve_agent_preset("chat-only", workspace).tools == ()


# ---------------------------------------------------------------------------
# Snapshot semantics
# ---------------------------------------------------------------------------


def test_snapshot_roundtrip_and_tolerant_decoding() -> None:
    snapshot = resolve_agent_preset("code-reader")
    decoded = AgentPresetSnapshot.from_metadata(snapshot.to_metadata())
    assert decoded == snapshot
    assert AgentPresetSnapshot.from_metadata("garbage") is None
    assert AgentPresetSnapshot.from_metadata({"tools": ["x"]}) is None  # no id


def test_snapshot_composition_helpers() -> None:
    appended = AgentPresetSnapshot(id="p", prompt="PERSONA")
    assert appended.compose_system_prompt("BASE") == "BASE\n\n## Persona\nPERSONA"
    replaced = AgentPresetSnapshot(id="p", prompt="ONLY", prompt_mode="replace")
    assert replaced.compose_system_prompt("BASE") == "ONLY"
    narrowing = AgentPresetSnapshot(id="p", tools=("read", "grep"))
    assert narrowing.tool_filter()(("read", "bash", "grep")) == ("read", "grep")
    assert AgentPresetSnapshot(id="p").tool_filter() is None


# ---------------------------------------------------------------------------
# Application wiring
# ---------------------------------------------------------------------------


def _open_application(tmp_path: Path):
    from core.application import DeepCodeApplication
    from core.sessions import SessionStore

    store = SessionStore(tmp_path / "sessions")
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_store=store,
    )
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    project = application.projects.add(str(workspace))
    return application, project, workspace


def test_start_persists_a_by_value_snapshot(tmp_path: Path) -> None:
    application, project, workspace = _open_application(tmp_path)
    try:
        thread = application.threads.start(
            project.id,
            title="reader session",
            agent_preset="code-reader",
            workspace_path=str(workspace),
        )
        session = application.session_store.get_session(thread.id)
        stored = session.metadata[METADATA_KEY]
        assert stored["id"] == "code-reader"
        assert stored["tools"] == ["read", "grep", "glob", "skill"]
        assert stored["allowSpawn"] is False
    finally:
        application.close()


def test_start_rejects_an_unknown_preset(tmp_path: Path) -> None:
    from core.application.errors import InvalidArgumentError

    application, project, workspace = _open_application(tmp_path)
    try:
        with pytest.raises(InvalidArgumentError, match="unknown agent preset"):
            application.threads.start(
                project.id,
                title="t",
                agent_preset="nope",
                workspace_path=str(workspace),
            )
    finally:
        application.close()


def test_preset_is_locked_once_the_conversation_has_started(tmp_path: Path) -> None:
    from core.application.errors import ConflictError

    application, project, workspace = _open_application(tmp_path)
    try:
        thread = application.threads.start(
            project.id,
            title="t",
            workspace_path=str(workspace),
        )
        # Blank Session: selecting and clearing both work.
        application.threads.set_agent_preset(thread.id, "minimal")
        application.threads.set_agent_preset(thread.id, None)
        application.threads.set_agent_preset(thread.id, "code-reader")

        application.session_store.append_message(thread.id, "user", "hello")
        with pytest.raises(ConflictError, match="locked once the conversation"):
            application.threads.set_agent_preset(thread.id, "minimal")
    finally:
        application.close()


def test_runtime_receives_the_snapshot_and_keys_on_it(tmp_path: Path) -> None:
    from core.application.session_runtime import SessionRuntimeRegistry
    from core.sessions import SessionStore

    class _Agent:
        def load_history(self, messages) -> None:
            self.loaded = list(messages)

        async def aclose(self) -> None:
            pass

    class _Factory:
        def __init__(self) -> None:
            self.presets: list[object] = []

        def runtime_key(self, *, workspace, model, agent_preset=None) -> object:
            return (
                workspace,
                model,
                agent_preset.fingerprint() if agent_preset is not None else None,
            )

        def create(self, *, workspace, model, approval_callback, agent_preset=None):
            del workspace, model, approval_callback
            self.presets.append(agent_preset)
            return _Agent()

    store = SessionStore(tmp_path / "sessions")
    snapshot = resolve_agent_preset("code-reader")
    with_preset = store.create_session(
        title="p",
        metadata={
            "workspace": str(tmp_path),
            METADATA_KEY: snapshot.to_metadata(),
        },
    )
    without = store.create_session(title="q", metadata={"workspace": str(tmp_path)})
    factory = _Factory()
    registry = SessionRuntimeRegistry(store, factory)

    async def exercise() -> None:
        await registry.acquire(
            with_preset.session_id,
            workspace=str(tmp_path),
            model=None,
            approval_callback=lambda *_: False,
        )
        registry.release(with_preset.session_id)
        await registry.acquire(
            without.session_id,
            workspace=str(tmp_path),
            model=None,
            approval_callback=lambda *_: False,
        )
        registry.release(without.session_id)
        await registry.close_all()

    asyncio.run(exercise())

    assert factory.presets[0] == snapshot  # decoded from canonical metadata
    assert factory.presets[1] is None
    first = registry._runtime_key(
        workspace=str(tmp_path),
        model=None,
        execution_profile=None,
        execution_security_profile=None,
        permission_mode_override=None,
        agent_preset=snapshot,
    )
    second = registry._runtime_key(
        workspace=str(tmp_path),
        model=None,
        execution_profile=None,
        execution_security_profile=None,
        permission_mode_override=None,
        agent_preset=None,
    )
    assert first != second


# ---------------------------------------------------------------------------
# Subagent dialect: disallowed-tools (strict) / max-turns+memory (dropped)
# ---------------------------------------------------------------------------


def test_disallowed_tools_load_and_unparsed_dialect_keys_stay_out(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    _write_preset(
        workspace / ".agents" / "presets",
        "reviewer",
        "---\n"
        "name: reviewer\n"
        "description: cross-dialect fields\n"
        "tools: read, bash\n"
        "disallowed-tools: write, edit\n"
        "max-turns: 12\n"  # deliberately NOT parsed: nothing reads it
        "memory: true\n"  # deliberately NOT parsed (Claude Code: scope string)
        "permission-mode: plan\n"  # deliberately NOT parsed into the preset
        "---\nYou review code.",
    )
    snapshot = resolve_agent_preset("reviewer", workspace)
    assert snapshot.tools == ("read", "bash")
    assert snapshot.disallowed_tools == ("write", "edit")
    # The dropped fields leave no trace — nothing may look enforced.
    assert not hasattr(snapshot, "max_turns")
    assert not hasattr(snapshot, "memory")
    # Permission stays an independent session-level knob (dsh's split):
    # the preset never carries it.
    assert not hasattr(snapshot, "permission_mode")


def test_malformed_disallowed_tools_marks_preset_broken(tmp_path: Path) -> None:
    # Fail closed: a restriction that cannot be parsed must not silently
    # vanish (the agent would keep the tools the author tried to remove).
    workspace = tmp_path / "ws"
    _write_preset(
        workspace / ".agents" / "presets",
        "sloppy",
        "---\n"
        "disallowed-tools: 42\n"  # not a string or list -> broken
        "---\nbody",
    )
    roster = {p.id: p for p in list_agent_presets(workspace)}
    assert roster["sloppy"].broken == (
        "disallowed-tools must be a string or a list of strings"
    )
    with pytest.raises(BrokenPresetError, match="sloppy"):
        resolve_agent_preset("sloppy", workspace)


def test_non_string_entry_in_disallowed_tools_marks_preset_broken(
    tmp_path: Path,
) -> None:
    # ``disallowed-tools: [Bash, 3]`` — the reviewer's example: one bad
    # entry must break the preset, not drop the deny list.
    workspace = tmp_path / "ws"
    _write_preset(
        workspace / ".agents" / "presets",
        "mixed",
        "---\ndisallowed-tools: [Bash, 3]\n---\nbody",
    )
    roster = {p.id: p for p in list_agent_presets(workspace)}
    assert roster["mixed"].broken == "disallowed-tools list entries must be strings"


def test_star_disallowed_tools_marks_preset_broken(tmp_path: Path) -> None:
    # ``*`` would mean "deny everything"; the subtraction filter cannot
    # express that, and both spellings fall through to a deny-nothing or
    # unrestricted interpretation — fail closed on both.
    workspace = tmp_path / "ws"
    _write_preset(
        workspace / ".agents" / "presets",
        "star-string",
        "---\ndisallowed-tools: '*'\n---\nbody",
    )
    _write_preset(
        workspace / ".agents" / "presets",
        "star-list",
        "---\ndisallowed-tools: ['*']\n---\nbody",
    )
    roster = {p.id: p for p in list_agent_presets(workspace)}
    message = "disallowed-tools: '*' would deny every tool; list tools explicitly"
    assert roster["star-string"].broken == message
    assert roster["star-list"].broken == message


def test_unparsed_dialect_keys_never_break_a_preset(tmp_path: Path) -> None:
    # max-turns/memory are simply not read: garbage there is inert, not
    # broken (unknown frontmatter keys are tolerated).
    workspace = tmp_path / "ws"
    _write_preset(
        workspace / ".agents" / "presets",
        "sloppy",
        "---\nmax-turns: later\nmemory: sometimes\n---\nbody",
    )
    roster = {p.id: p for p in list_agent_presets(workspace)}
    assert roster["sloppy"].broken is None
    assert roster["sloppy"].disallowed_tools is None


def test_key_variant_precedence_is_kebab_then_camel_then_snake(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    _write_preset(
        workspace / ".agents" / "presets",
        "variants",
        "---\n"
        "disallowed-tools: Write\n"
        "disallowedTools: Edit\n"
        "disallowed_tools: Bash\n"
        "---\n",
    )
    snapshot = resolve_agent_preset("variants", workspace)
    assert snapshot.disallowed_tools == ("write",)

    # Without kebab, camel wins over snake.
    _write_preset(
        workspace / ".agents" / "presets",
        "variants2",
        "---\ndisallowedTools: Edit\ndisallowed_tools: Bash\n---\n",
    )
    snapshot = resolve_agent_preset("variants2", workspace)
    assert snapshot.disallowed_tools == ("edit",)


def test_tool_filter_subtracts_disallowed_from_any_roster() -> None:
    # Allowlist AND NOT disallowed: the deny side still wins.
    both = AgentPresetSnapshot(
        id="p", tools=("read", "bash"), disallowed_tools=("write", "edit")
    )
    assert both.tool_filter()(("read", "bash", "write", "glob")) == ("read", "bash")
    # Disallowed-only narrows an arbitrary roster.
    deny_only = AgentPresetSnapshot(id="q", disallowed_tools=("write",))
    assert deny_only.tool_filter()(("read", "write", "bash")) == ("read", "bash")
    # No declaration at all -> unrestricted.
    assert AgentPresetSnapshot(id="r").tool_filter() is None


def test_snapshot_roundtrip_and_fingerprint_cover_subagent_fields() -> None:
    snapshot = AgentPresetSnapshot(
        id="p",
        tools=("read",),
        disallowed_tools=("write",),
    )
    metadata = snapshot.to_metadata()
    assert metadata["disallowedTools"] == ["write"]
    # The dropped fields are gone from the wire — nothing may look enforced.
    assert "maxTurns" not in metadata
    assert "memory" not in metadata

    decoded = AgentPresetSnapshot.from_metadata(metadata)
    assert decoded == snapshot
    assert decoded.fingerprint() == snapshot.fingerprint()

    # A different deny-list yields a different runtime key (no stale reuse).
    other = AgentPresetSnapshot(id="p", tools=("read",), disallowed_tools=("edit",))
    assert other.fingerprint() != snapshot.fingerprint()

    # Tolerant decode: garbage deny list degrades to unset; stale keys from
    # older sessions (maxTurns/memory) are ignored.
    decoded = AgentPresetSnapshot.from_metadata(
        {"id": "p", "disallowedTools": "x", "maxTurns": -1, "memory": "yes"}
    )
    assert decoded is not None
    assert decoded.disallowed_tools is None
