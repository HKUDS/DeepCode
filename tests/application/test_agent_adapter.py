from __future__ import annotations

from core.application.agent_adapter import DefaultAgentSessionFactory
from core.domain.execution_permission import ExecutionPermissionMode
from core.harness.permissions import PermissionMode
from core.providers.credentials import CredentialStore


def test_desktop_factory_uses_approval_first_only_as_its_client_default(
    monkeypatch,
) -> None:
    captured = {}
    sentinel = object()

    def build(**kwargs):
        captured.update(kwargs)
        return sentinel, "model", object()

    monkeypatch.setattr("core.application.agent_adapter.build_agent_session", build)

    callback = object()
    result = DefaultAgentSessionFactory().create(
        workspace="/workspace",
        model=None,
        approval_callback=callback,
    )

    assert result is sentinel
    assert captured["approval_callback"] is callback
    assert captured["default_permission_mode"] is PermissionMode.DEFAULT
    assert captured["permission_mode_override"] is None
    assert captured["runtime"].config is not None


def test_factory_adapts_durable_execution_permission_to_harness_mode(
    monkeypatch,
) -> None:
    captured = {}
    sentinel = object()

    def build(**kwargs):
        captured.update(kwargs)
        return sentinel, "model", object()

    monkeypatch.setattr("core.application.agent_adapter.build_agent_session", build)

    result = DefaultAgentSessionFactory().create(
        workspace="/workspace",
        model=None,
        permission_mode_override=ExecutionPermissionMode.PLAN,
        approval_callback=object(),
    )

    assert result is sentinel
    assert captured["default_permission_mode"] is PermissionMode.DEFAULT
    assert captured["permission_mode_override"] is PermissionMode.PLAN


def test_desktop_runtime_key_changes_after_credential_rotation(
    tmp_path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    runtime = object()
    monkeypatch.setattr(
        "core.application.agent_adapter.get_runtime",
        lambda: runtime,
    )
    factory = DefaultAgentSessionFactory()

    before = factory.runtime_key(
        workspace=str(workspace),
        model="moonshotai/kimi-k2.6",
    )
    CredentialStore(home / "credentials.json").set("router", "first-key")
    after = factory.runtime_key(
        workspace=str(workspace),
        model="moonshotai/kimi-k2.6",
    )

    assert before != after


def test_factory_injects_plugin_mcp_and_invalidates_on_plugin_revision(
    tmp_path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    captured = {}
    sentinel_session = object()
    sentinel_server = object()
    revision = ["revision-one"]

    def build(**kwargs):
        captured.update(kwargs)
        return sentinel_session, "model", object()

    monkeypatch.setattr("core.application.agent_adapter.build_agent_session", build)
    factory = DefaultAgentSessionFactory()
    factory.configure_plugin_mcp(
        lambda path: (sentinel_server,),
        revision_provider=lambda: revision[0],
    )

    before = factory.runtime_key(workspace=str(workspace), model=None)
    result = factory.create(
        workspace=str(workspace),
        model=None,
        approval_callback=object(),
    )
    revision[0] = "revision-two"
    after = factory.runtime_key(workspace=str(workspace), model=None)

    assert result is sentinel_session
    assert captured["plugin_mcp_servers"] == (sentinel_server,)
    assert before != after
