"""Tests for the P1 three-valued permission engine."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.permissions import (  # noqa: E402
    PermissionDecision,
    PermissionEngine,
    PermissionMode,
    PermissionRule,
    make_engine,
    rules_from_config,
)

ALLOW = PermissionDecision.ALLOW
ASK = PermissionDecision.ASK
DENY = PermissionDecision.DENY


def _decide(engine: PermissionEngine, tool: str, **args):
    decision, _reason = engine.evaluate(tool, args)
    return decision


# ---- non-overridable sensitive-path denylist -------------------------------


def test_denylist_blocks_ssh_read_even_in_full_auto():
    engine = PermissionEngine(mode=PermissionMode.FULL_AUTO, cwd="/home/u/proj")
    assert _decide(engine, "read_file", file_path="/home/u/.ssh/id_rsa") is DENY


def test_denylist_blocks_env_and_config_and_pem():
    engine = PermissionEngine(mode=PermissionMode.FULL_AUTO, cwd="/home/u/proj")
    assert _decide(engine, "read_file", file_path="/home/u/proj/.env") is DENY
    assert _decide(engine, "write_file", file_path="deepcode_config.json") is DENY
    assert _decide(engine, "read_file", file_path="/home/u/proj/server.pem") is DENY


def test_denylist_cannot_be_overridden_by_an_allow_rule():
    engine = PermissionEngine(
        mode=PermissionMode.FULL_AUTO,
        rules=[PermissionRule("*", "*", ALLOW)],
        cwd="/home/u/proj",
    )
    # Even a blanket allow rule loses to the denylist.
    assert _decide(engine, "read_file", file_path="/home/u/.aws/credentials") is DENY


def test_denylist_matches_relative_path_via_cwd():
    engine = PermissionEngine(mode=PermissionMode.FULL_AUTO, cwd="/home/u/proj")
    # ".env" resolves under cwd and is caught.
    assert _decide(engine, "write_file", file_path=".env") is DENY


def test_ordinary_workspace_path_is_not_denylisted():
    engine = PermissionEngine(mode=PermissionMode.FULL_AUTO, cwd="/home/u/proj")
    assert _decide(engine, "write_file", file_path="src/model.py") is ALLOW


# ---- modes -----------------------------------------------------------------


def test_default_mode_reads_allow_writes_ask():
    engine = PermissionEngine(mode=PermissionMode.DEFAULT, cwd="/w")
    assert _decide(engine, "read_file", file_path="/w/a.py") is ALLOW
    assert _decide(engine, "write_file", file_path="/w/a.py") is ASK
    assert _decide(engine, "execute_bash", command="pytest") is ASK


def test_plan_mode_denies_mutations_allows_reads():
    engine = PermissionEngine(mode=PermissionMode.PLAN, cwd="/w")
    assert _decide(engine, "grep", pattern="foo") is ALLOW
    assert _decide(engine, "write_file", file_path="/w/a.py") is DENY


def test_full_auto_allows_mutations_without_ask():
    engine = PermissionEngine(mode=PermissionMode.FULL_AUTO, cwd="/w")
    assert _decide(engine, "write_file", file_path="/w/a.py") is ALLOW
    assert _decide(engine, "execute_bash", command="pytest") is ALLOW


def test_mcp_prefixed_tools_recognized_as_read_only():
    engine = PermissionEngine(mode=PermissionMode.DEFAULT, cwd="/w")
    assert (
        _decide(engine, "mcp_code-implementation_read_file", file_path="/w/a.py")
        is ALLOW
    )


# ---- two-dimensional wildcard rules, last-match-wins ------------------------


def test_two_dimensional_bash_rule():
    engine = PermissionEngine(
        mode=PermissionMode.DEFAULT,
        rules=rules_from_config({"execute_bash": {"git push *": "ask", "*": "allow"}}),
        cwd="/w",
    )
    assert _decide(engine, "execute_bash", command="git status") is ALLOW
    assert _decide(engine, "execute_bash", command="git push origin main") is ASK


def test_last_match_wins():
    engine = PermissionEngine(
        mode=PermissionMode.DEFAULT,
        rules=[
            PermissionRule("write_file", "*", DENY),
            PermissionRule("write_file", "*", ALLOW),  # later wins
        ],
        cwd="/w",
    )
    assert _decide(engine, "write_file", file_path="/w/a.py") is ALLOW


def test_rule_does_not_override_denylist():
    engine = make_engine(
        "full_auto",
        rules_config={"read_file": {"*": "allow"}},
        cwd="/home/u/proj",
    )
    assert _decide(engine, "read_file", file_path="/home/u/.ssh/config") is DENY


def test_rules_from_config_rejects_bad_action():
    import pytest

    with pytest.raises(ValueError):
        rules_from_config({"write_file": "sometimes"})


def test_shorthand_string_rule():
    engine = PermissionEngine(
        mode=PermissionMode.DEFAULT,
        rules=rules_from_config({"write_file": "allow"}),
        cwd="/w",
    )
    assert _decide(engine, "write_file", file_path="/w/a.py") is ALLOW
