"""The prompt cue: env switch and the shape of the injected turn context."""

from __future__ import annotations

from core.loop.cue import _CUE_TEXT, build_cue_context, prompt_cue_enabled

OFF_VALUES = ("0", "false", "off", "no", "OFF", "No")


def test_cue_is_on_by_default(monkeypatch):
    monkeypatch.delenv("DEEPCODE_PROMPT_CUE", raising=False)
    assert prompt_cue_enabled() is True


def test_blank_env_value_keeps_the_cue_on(monkeypatch):
    monkeypatch.setenv("DEEPCODE_PROMPT_CUE", "   ")
    assert prompt_cue_enabled() is True


def test_each_off_spelling_disables_the_cue(monkeypatch):
    for value in OFF_VALUES:
        monkeypatch.setenv("DEEPCODE_PROMPT_CUE", value)
        assert prompt_cue_enabled() is False, value


def test_any_other_value_keeps_the_cue_on(monkeypatch):
    for value in ("1", "true", "yes", "on"):
        monkeypatch.setenv("DEEPCODE_PROMPT_CUE", value)
        assert prompt_cue_enabled() is True, value


def test_context_is_one_user_message_carrying_the_cue(monkeypatch):
    monkeypatch.delenv("DEEPCODE_PROMPT_CUE", raising=False)
    context = build_cue_context()
    assert len(context) == 1
    assert context[0] == {"role": "user", "content": _CUE_TEXT}


def test_disabled_cue_injects_nothing(monkeypatch):
    monkeypatch.setenv("DEEPCODE_PROMPT_CUE", "0")
    assert build_cue_context() == ()
