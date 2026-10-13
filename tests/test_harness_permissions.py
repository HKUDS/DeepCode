"""Tests for the P1 three-valued permission engine."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_setup import _wire_tool_permissions
from core.domain.execution_security import ApprovalPolicy
from core.harness.permissions import (
    PermissionDecision,
    PermissionEngine,
    PermissionMode,
    PermissionRule,
    make_engine,
    rules_from_config,
)
from core.harness.tools import default_coding_tools

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


def test_patch_paths_are_checked_by_central_sensitive_path_protection():
    engine = PermissionEngine(mode=PermissionMode.FULL_AUTO, cwd="/home/u/proj")
    patch = (
        "*** Begin Patch\n"
        "*** Update File: /home/u/.ssh/config\n"
        "@@\n-old\n+new\n"
        "*** End Patch\n"
    )

    assert _decide(engine, "apply_patch", patch=patch) is DENY


def test_sensitive_path_protection_can_only_be_disabled_centrally():
    engine = PermissionEngine(
        mode=PermissionMode.FULL_AUTO,
        cwd="/home/u/proj",
        protect_sensitive_paths=False,
    )

    assert _decide(engine, "write", file_path="/home/u/.ssh/config") is ALLOW


def test_ordinary_workspace_path_is_not_denylisted():
    engine = PermissionEngine(mode=PermissionMode.FULL_AUTO, cwd="/home/u/proj")
    assert _decide(engine, "write_file", file_path="src/model.py") is ALLOW


def test_denylist_blocks_a_credential_directory_as_the_argument():
    # grep/glob/read take a directory: searching ~/.aws reads what is inside.
    engine = PermissionEngine(mode=PermissionMode.FULL_AUTO, cwd="/home/u/proj")
    for directory in ("/home/u/.aws", "/home/u/.gnupg", "/home/u/.config/gcloud"):
        assert _decide(engine, "grep", pattern="key", path=directory) is DENY
    assert _decide(engine, "glob", pattern="**/*", path="/home/u/.azure") is DENY
    assert _decide(engine, "read", file_path="/home/u/.aws/sso/cache/t.json") is DENY


def test_denylist_checks_path_arguments_whatever_their_name():
    # MCP tools name their paths in many ways (the filesystem server alone
    # uses path, paths, source and destination), sometimes nested.
    engine = PermissionEngine(mode=PermissionMode.FULL_AUTO, cwd="/home/u/proj")
    assert (
        _decide(
            engine, "mcp_fs_read_multiple_files", paths=["a.py", "/home/u/.ssh/id_rsa"]
        )
        is DENY
    )
    assert (
        _decide(
            engine,
            "mcp_fs_move_file",
            source="/home/u/.aws/credentials",
            destination="/tmp/x",
        )
        is DENY
    )
    assert _decide(engine, "mcp_fs_move_file", source="a", destination=".env") is DENY
    assert (
        _decide(engine, "mcp_x_export", options={"output_dir": "/home/u/.gnupg"})
        is DENY
    )
    assert _decide(engine, "mcp_x_upload", files=[{"local_file": "server.pem"}]) is DENY


def test_denylist_leaves_arguments_that_are_not_paths_alone():
    # Text that merely mentions a protected name is not a path.
    engine = PermissionEngine(mode=PermissionMode.FULL_AUTO, cwd="/home/u/proj")
    assert (
        _decide(engine, "write", file_path="notes.md", content="keys: ~/.ssh/id_rsa")
        is ALLOW
    )
    assert _decide(engine, "grep", pattern=r"id_rsa|\.env") is ALLOW
    assert _decide(engine, "mcp_x_search", query=".env", options={"limit": 5}) is ALLOW


def test_denylist_follows_symlinks(tmp_path):
    secret = tmp_path / "home" / ".ssh" / "id_rsa"
    secret.parent.mkdir(parents=True)
    secret.write_text("not a real key")
    workspace = tmp_path / "proj"
    workspace.mkdir()
    (workspace / "notes.txt").symlink_to(secret)
    (workspace / "keys").symlink_to(secret.parent)
    engine = PermissionEngine(mode=PermissionMode.FULL_AUTO, cwd=str(workspace))

    assert _decide(engine, "read", file_path="notes.txt") is DENY
    assert _decide(engine, "grep", pattern="x", path="keys") is DENY


def test_denylist_ignores_case():
    # The default macOS and Windows filesystems ignore case: ~/.SSH is ~/.ssh.
    engine = PermissionEngine(mode=PermissionMode.FULL_AUTO, cwd="/home/u/proj")
    assert _decide(engine, "read", file_path="/home/u/.SSH/id_rsa") is DENY
    assert _decide(engine, "read", file_path="/home/u/proj/.ENV") is DENY
    assert _decide(engine, "read", file_path="/home/u/proj/Server.PEM") is DENY


# ---- modes -----------------------------------------------------------------


def test_default_mode_reads_allow_writes_ask():
    engine = PermissionEngine(mode=PermissionMode.DEFAULT, cwd="/w")
    assert _decide(engine, "read_file", file_path="/w/a.py") is ALLOW
    assert _decide(engine, "write_file", file_path="/w/a.py") is ASK
    assert _decide(engine, "execute_bash", command="pytest") is ASK


def test_native_tool_read_only_metadata_drives_default_mode(tmp_path):
    engine = PermissionEngine(mode=PermissionMode.DEFAULT, cwd=str(tmp_path))
    registry = default_coding_tools(tmp_path)

    _wire_tool_permissions(registry, engine)

    assert _decide(engine, "read", file_path="a.py") is ALLOW
    assert _decide(engine, "grep", pattern="needle") is ALLOW
    assert _decide(engine, "web_fetch", url="https://example.com") is ALLOW
    assert _decide(engine, "write", file_path="a.py") is ASK
    assert _decide(engine, "bash", command="python -m unittest") is ASK


def test_plan_mode_denies_mutations_allows_reads():
    engine = PermissionEngine(mode=PermissionMode.PLAN, cwd="/w")
    assert _decide(engine, "grep", pattern="foo") is ALLOW
    assert _decide(engine, "write_file", file_path="/w/a.py") is DENY


def test_enforced_read_only_cannot_be_broadened_by_allow_rule():
    engine = PermissionEngine(
        mode=PermissionMode.PLAN,
        rules=[PermissionRule("write", "*", ALLOW)],
        cwd="/w",
        enforce_read_only=True,
    )

    decision, reason = engine.evaluate("write", {"file_path": "/w/a.py"})

    assert decision is DENY
    assert "cannot be enabled by rules" in reason


def test_legacy_plan_rule_override_remains_compatible():
    engine = PermissionEngine(
        mode=PermissionMode.PLAN,
        rules=[PermissionRule("write", "*", ALLOW)],
        cwd="/w",
    )

    assert _decide(engine, "write", file_path="/w/a.py") is ALLOW


def test_full_auto_allows_mutations_without_ask():
    engine = PermissionEngine(mode=PermissionMode.FULL_AUTO, cwd="/w")
    assert _decide(engine, "write_file", file_path="/w/a.py") is ALLOW
    assert _decide(engine, "execute_bash", command="pytest") is ALLOW


def test_never_approval_policy_turns_explicit_ask_rule_into_deny():
    engine = PermissionEngine(
        mode=PermissionMode.FULL_AUTO,
        rules=[PermissionRule("bash", "git push *", ASK)],
        cwd="/w",
        approval_policy=ApprovalPolicy.NEVER,
    )

    decision, reason = engine.evaluate("bash", {"command": "git push origin main"})

    assert decision is DENY
    assert "approval policy is never" in reason


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
