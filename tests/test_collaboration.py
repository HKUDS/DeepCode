"""Tests for collaboration modes (C1 — working-style preamble)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.collaboration import collaboration_preamble  # noqa: E402
from core.harness.permissions import PermissionMode  # noqa: E402


def test_plan_mode_preamble_is_non_mutating_and_plan_first():
    text = collaboration_preamble(PermissionMode.PLAN)
    assert "plan mode" in text.lower()
    assert "do not modify" in text.lower()
    assert "read-only" in text.lower()


def test_default_and_full_auto_are_autonomous():
    for mode in (PermissionMode.DEFAULT, PermissionMode.FULL_AUTO):
        text = collaboration_preamble(mode)
        assert "autonomously" in text.lower()
        assert "plan mode" not in text.lower()


def test_only_plan_mode_differs():
    plan = collaboration_preamble(PermissionMode.PLAN)
    default = collaboration_preamble(PermissionMode.DEFAULT)
    assert plan != default


def test_posture_section_follows_the_security_profile():
    approval = collaboration_preamble(PermissionMode.DEFAULT)
    assert "## Permissions: approval required" in approval
    unattended = collaboration_preamble(PermissionMode.FULL_AUTO)
    assert "## Permissions: unattended" in unattended
    # An explicit flag beats what the mode implies.
    assert "## Permissions: unattended" in collaboration_preamble(
        PermissionMode.DEFAULT, unattended=True
    )
    # Plan mode is read-only by construction and carries no posture section.
    assert "## Permissions" not in collaboration_preamble(PermissionMode.PLAN)


def test_base_prompt_carries_the_working_rules():
    from core.agent_setup import SYSTEM_PROMPT

    for heading in (
        "## Tone",
        "## Using tools",
        "## Keep the user informed",
        "## Planning",
        "## Making changes",
        "## Validating your work",
        "## Debugging",
        "## Long tasks",
        "## Final message",
    ):
        assert heading in SYSTEM_PROMPT
    # The working style moves with the mode, so it is not in the base prompt.
    assert "## Working style" not in SYSTEM_PROMPT


def _built_system_prompt(tmp_path, monkeypatch, security):
    from core import agent_setup

    llm_profile = type("Profile", (), {"model": "test-model"})()
    monkeypatch.setattr(
        agent_setup,
        "get_workflow_provider",
        lambda **_kwargs: (object(), llm_profile),
    )
    monkeypatch.setattr(
        agent_setup,
        "get_runtime",
        lambda: type("Runtime", (), {"config": object()})(),
    )
    session, _model, _engine = agent_setup.build_agent_session(
        workspace=str(tmp_path),
        execution_security_profile=security,
    )
    return session._system_prompt


def test_session_prompt_names_the_posture_it_runs_under(tmp_path, monkeypatch):
    from core.domain.execution_security import (
        ExecutionAccessPreset,
        ExecutionSecurityProfile,
    )

    expected = {
        ExecutionAccessPreset.ASK: "## Permissions: approval required",
        ExecutionAccessPreset.FULL_ACCESS: "## Permissions: unattended",
    }
    for preset, heading in expected.items():
        prompt = _built_system_prompt(
            tmp_path, monkeypatch, ExecutionSecurityProfile.for_preset(preset)
        )
        assert prompt.startswith("You are DeepCode")
        assert "## Working style\n" in prompt
        assert heading in prompt
        assert prompt.count("## Permissions") == 1
    # The read-only preset runs in plan mode, whose working style says it all.
    prompt = _built_system_prompt(
        tmp_path,
        monkeypatch,
        ExecutionSecurityProfile.for_preset(ExecutionAccessPreset.READ_ONLY),
    )
    assert "## Working style: plan mode" in prompt
    assert "## Permissions" not in prompt
