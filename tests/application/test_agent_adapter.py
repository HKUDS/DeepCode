from __future__ import annotations

from core.application.agent_adapter import DefaultAgentSessionFactory
from core.harness.permissions import PermissionMode


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
    assert captured["runtime"].config is not None
