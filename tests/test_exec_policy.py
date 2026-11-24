"""Load-time self-tests and strictest-wins semantics for the exec policy."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.harness import exec_policy
from core.harness.command_guard import screen_all
from core.harness.exec_policy import (
    ALLOW,
    FORBID,
    PROMPT,
    ExecPolicyError,
    PrefixRule,
    evaluate,
    forbid_reason,
    load_policy_file,
    load_policy_text,
)

BASIC = """
[[rule]]
pattern = ["git", ["push", "pull"]]
decision = "prompt"
justification = "network side effect"
match = [["git", "push"], "git pull --rebase"]
not_match = [["git", "status"], "git  pullx"]
"""


def test_alternatives_and_prefix_match() -> None:
    rules = load_policy_text(BASIC)
    assert evaluate("git push origin main", rules).decision == PROMPT
    assert evaluate("git pull --rebase", rules).decision == PROMPT
    assert evaluate("git status", rules).decision is None


def test_strictest_decision_wins_regardless_of_order() -> None:
    broad = PrefixRule(
        pattern=("deploy",),
        decision=ALLOW,
        match=(("deploy", "prod"),),
    )
    narrow = PrefixRule(
        pattern=("deploy", "prod"),
        decision=FORBID,
        justification="production is not scriptable",
        match=(("deploy", "prod", "--force"),),
    )
    for rules in ([broad, narrow], [narrow, broad]):
        decision = evaluate("deploy prod --force", rules)
        assert decision.decision == FORBID
        assert decision.blocked is True
        assert decision.matched_prefix == ("deploy", "prod")


def test_unparseable_command_has_no_decision() -> None:
    rules = load_policy_text(BASIC)
    assert evaluate("git 'unclosed", rules).decision is None


def test_match_example_that_does_not_match_fails_at_load() -> None:
    broken = """
[[rule]]
pattern = ["git", "push"]
match = [["git", "pull"]]
"""
    with pytest.raises(ExecPolicyError, match="does not match the pattern"):
        load_policy_text(broken)


def test_not_match_example_that_matches_fails_at_load() -> None:
    broken = """
[[rule]]
pattern = ["git", "push"]
not_match = [["git", "push", "--force"]]
"""
    with pytest.raises(ExecPolicyError, match="unexpectedly matches"):
        load_policy_text(broken)


def test_rule_without_examples_is_rejected() -> None:
    with pytest.raises(ExecPolicyError, match="at least one"):
        load_policy_text("""
[[rule]]
pattern = ["rm"]
decision = "forbid"
""")


def test_unknown_decision_is_rejected() -> None:
    broken = """
[[rule]]
pattern = ["rm"]
decision = "maybe"
match = [["rm", "-rf", "/"]]
"""
    with pytest.raises(ExecPolicyError, match="decision"):
        load_policy_text(broken)


def test_invalid_toml_is_reported_not_swallowed() -> None:
    with pytest.raises(ExecPolicyError, match="invalid TOML"):
        load_policy_text("[[rule]\npattern = ")


def test_policy_file_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "policy.toml"
    path.write_text(BASIC, encoding="utf-8")
    rules = load_policy_file(path)
    assert evaluate("git push", rules).decision == PROMPT


def test_forbid_reason_carries_the_justification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = tmp_path / "policy.toml"
    policy.write_text(
        """
[[rule]]
pattern = ["terraform", "apply"]
decision = "forbid"
justification = "apply through the pipeline, not from an agent"
match = [["terraform", "apply", "-auto-approve"]]
not_match = [["terraform", "plan"]]
""",
        encoding="utf-8",
    )
    monkeypatch.setenv(exec_policy.POLICY_ENV, str(policy))
    reason = forbid_reason("terraform apply -auto-approve")
    assert reason is not None
    assert "apply through the pipeline" in reason
    assert forbid_reason("terraform plan") is None


def test_unset_env_means_no_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(exec_policy.POLICY_ENV, raising=False)
    assert exec_policy.active_policy() == ()
    assert forbid_reason("terraform apply") is None


def test_unreadable_policy_degrades_to_no_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(exec_policy.POLICY_ENV, str(tmp_path / "missing.toml"))
    assert exec_policy.active_policy() == ()


def test_edited_policy_is_picked_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = tmp_path / "policy.toml"
    policy.write_text(BASIC, encoding="utf-8")
    monkeypatch.setenv(exec_policy.POLICY_ENV, str(policy))
    assert forbid_reason("git push") is None  # prompt, not forbid
    policy.write_text(
        BASIC.replace('decision = "prompt"', 'decision = "forbid"'), encoding="utf-8"
    )
    os.utime(policy, (0, 0))
    assert forbid_reason("git push") is not None


def test_shallow_screen_honours_forbid_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = tmp_path / "policy.toml"
    policy.write_text(
        """
[[rule]]
pattern = ["rm"]
decision = "allow"
match = [["rm", "-rf", "/"]]
""",
        encoding="utf-8",
    )
    monkeypatch.setenv(exec_policy.POLICY_ENV, str(policy))
    # A policy allow must never suppress the built-in destructive screen.
    assert screen_all("rm -rf /") is not None


def test_shallow_screen_reports_the_policy_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = tmp_path / "policy.toml"
    policy.write_text(
        """
[[rule]]
pattern = ["echo", "deploy"]
decision = "forbid"
justification = "deploys go through CI"
match = [["echo", "deploy", "prod"]]
""",
        encoding="utf-8",
    )
    monkeypatch.setenv(exec_policy.POLICY_ENV, str(policy))
    reason = screen_all("echo deploy prod")
    assert reason is not None
    assert "deploys go through CI" in reason
    assert screen_all("echo hello") is None
