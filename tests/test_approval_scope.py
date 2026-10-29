"""What "always allow this session" covers: the same kind of call only."""

from __future__ import annotations

import os

import pytest

from core.harness.approval_scope import (
    SessionScope,
    command_keys,
    is_covered,
    requested_session_target,
    session_scope,
)


def _covered_after(granted: list[str], wanted: str) -> bool:
    """Whether approving each of ``granted`` for the session covers ``wanted``."""

    grants = [
        ("bash", session_scope("bash", {"command": command}, "/ws").keys)
        for command in granted
    ]
    return is_covered(session_scope("bash", {"command": wanted}, "/ws"), grants)


@pytest.mark.parametrize(
    ("command", "keys"),
    [
        ("ls", ("cmd:ls",)),
        ("ls -la /tmp", ("cmd:ls",)),
        ("git status", ("cmd:git status",)),
        ("git status --short", ("cmd:git status",)),
        ("git", ("cmd:git",)),
        ("npm run test -- --watch", ("cmd:npm run test",)),
        ("npm test", ("cmd:npm test",)),
        ("docker compose up -d", ("cmd:docker compose up",)),
        ("python -m pytest -x", ("cmd:python -m pytest",)),
        ("python3 tools/build.py --fast", ("cmd:python3 tools/build.py",)),
        ("uv run pytest -q", ("cmd:uv run pytest",)),
        ("npx prettier --write .", ("cmd:npx prettier",)),
        ("find . -name '*.py'", ("cmd:find",)),
        ("cat < input.txt", ("cmd:cat",)),
        ("make test 2>/dev/null", ("cmd:make test",)),
        ("{ ls; }", ("cmd:ls",)),
        ("echo 'a && b'", ("cmd:echo",)),
    ],
)
def test_a_command_is_keyed_by_program_and_subcommand(command, keys) -> None:
    assert command_keys(command) == keys


@pytest.mark.parametrize(
    ("command", "keys"),
    [
        ("rm -rf build && echo removed", ("cmd:rm", "cmd:echo")),
        ("npm test 2>&1 | tail -5", ("cmd:npm test", "cmd:tail")),
        ("(cd app && make build)", ("cmd:cd", "cmd:make build")),
        ("ls; ls -a\nls", ("cmd:ls",)),
        ("a & b", ("cmd:a", "cmd:b")),
        ("a || b |& c", ("cmd:a", "cmd:b", "cmd:c")),
    ],
)
def test_a_compound_command_has_one_key_per_part(command, keys) -> None:
    assert command_keys(command) == keys


@pytest.mark.parametrize(
    "command",
    [
        "echo hi > out.txt",  # writes a file
        "echo hi >> log.txt",
        "ls &> listing.txt",
        "sudo rm -rf /",  # wrappers run what their arguments say
        "env FOO=1 npm test",
        "xargs rm",
        "timeout 5 npm test",
        "FOO=1 npm test",  # an assignment changes what runs
        "python -c 'import os'",  # inline code
        "bash -c 'rm -rf x'",
        "uv run python -c 'x'",
        "git -C ../other push",  # options before the subcommand
        "find . -delete",
        "find . -exec rm {} +",
        "$TOOL --version",
    ],
)
def test_risky_commands_are_keyed_by_their_exact_text(command) -> None:
    (key,) = command_keys(command)
    assert key.startswith("cmd=")


@pytest.mark.parametrize(
    "command",
    [
        "echo $(whoami)",
        "echo `whoami`",
        "diff <(ls a) <(ls b)",
        "cat <<EOF\nrm -rf x\nEOF",
        "for f in *; do rm $f; done",
        "if true; then rm x; fi",
        "echo 'unbalanced",
        "echo >",
    ],
)
def test_a_line_the_parser_cannot_follow_is_keyed_whole(command) -> None:
    assert command_keys(command) == ("cmd=" + command.strip(),)


def test_approving_ls_does_not_cover_rm() -> None:
    assert _covered_after(["ls"], "ls -la /")
    assert not _covered_after(["ls"], "rm -rf /")
    assert not _covered_after(["ls"], "lsof")


def test_a_subcommand_covers_only_itself() -> None:
    assert _covered_after(["git status"], "git status -s")
    assert not _covered_after(["git status"], "git push --force")
    assert not _covered_after(["git status"], "git")
    assert not _covered_after(["npm run test"], "npm run deploy")
    assert not _covered_after(["python script.py"], "python other.py")
    assert not _covered_after(["python script.py"], "python -c 'x'")
    assert not _covered_after(["uv run pytest"], "uv run python -c 'x'")


def test_a_compound_command_needs_every_part_covered() -> None:
    assert not _covered_after(["ls"], "ls && rm -rf /")
    assert not _covered_after(["ls"], "ls; rm -rf /")
    assert not _covered_after(["ls"], "ls | xargs rm")
    assert not _covered_after(["ls"], "(ls) && rm x")
    assert not _covered_after(["ls"], "ls\nrm x")
    assert _covered_after(["ls", "rm x"], "ls && rm y")
    assert _covered_after(["rm -rf build && echo removed"], "echo done; rm -f a")


def test_redirects_and_substitution_are_not_covered_by_a_prefix() -> None:
    assert not _covered_after(["echo hi"], "echo hi > ~/.bashrc")
    assert not _covered_after(["echo hi > out.txt"], "echo hi > other.txt")
    assert _covered_after(["echo hi > out.txt"], "echo hi > out.txt")
    assert not _covered_after(["echo hi"], "echo $(rm -rf /)")
    assert not _covered_after(["cat a"], "cat <<EOF > x\nEOF")
    assert _covered_after(["echo hi"], "echo hi 2>&1 >/dev/null")


def test_wrapped_commands_are_not_covered_by_the_inner_program() -> None:
    assert not _covered_after(["rm x"], "sudo rm x")
    assert not _covered_after(["sudo ls"], "sudo rm -rf /")
    assert not _covered_after(["npm test"], "FOO=1 npm test")


def test_command_labels_say_what_is_covered() -> None:
    assert (
        session_scope("bash", {"command": "git status"}, "/ws").label
        == "Always allow `git status …` this session"
    )
    assert (
        session_scope("bash", {"command": "rm -rf b && echo ok"}, "/ws").label
        == "Always allow `rm …` and `echo …` this session"
    )
    assert (
        session_scope("bash", {"command": "echo hi > out.txt"}, "/ws").label
        == "Always allow exactly `echo hi > out.txt` this session"
    )


def test_a_long_exact_command_is_shortened_in_the_label() -> None:
    scope = session_scope("bash", {"command": "sudo " + "x" * 200}, "/ws")
    assert len(scope.target) < 80
    assert scope.target.endswith("…`")


def test_edits_in_the_workspace_share_one_scope_across_edit_tools(tmp_path) -> None:
    workspace = str(tmp_path)
    write = session_scope("write", {"file_path": "a.txt", "content": "x"}, workspace)
    edit = session_scope(
        "edit",
        {
            "file_path": str(tmp_path / "sub" / "b.py"),
            "old_string": "a",
            "new_string": "b",
        },
        workspace,
    )
    patch = session_scope(
        "apply_patch",
        {"patch": "*** Begin Patch\n*** Update File: c.py\n@@\n-a\n+b\n*** End Patch"},
        workspace,
    )
    assert write.label == "Always allow edits in this workspace this session"
    assert write.keys == edit.keys == patch.keys
    assert is_covered(edit, [("write", write.keys)])
    assert is_covered(patch, [("write", write.keys)])


def test_an_edit_outside_the_workspace_is_keyed_by_its_path(tmp_path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    inside = session_scope("write", {"file_path": "a.txt"}, str(workspace))
    escaping = session_scope("write", {"file_path": "../secrets.txt"}, str(workspace))
    absolute = session_scope("write", {"file_path": "/etc/hosts"}, str(workspace))
    assert not is_covered(escaping, [("write", inside.keys)])
    assert not is_covered(absolute, [("write", inside.keys)])
    assert "edits to `" in escaping.label
    assert not is_covered(absolute, [("write", escaping.keys)])
    assert is_covered(escaping, [("write", escaping.keys)])


def test_a_symlink_out_of_the_workspace_is_outside(tmp_path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, workspace / "link")
    inside = session_scope("write", {"file_path": "a.txt"}, str(workspace))
    through_link = session_scope("write", {"file_path": "link/x.txt"}, str(workspace))
    assert not is_covered(through_link, [("write", inside.keys)])


def test_a_patch_without_paths_is_only_this_exact_change(tmp_path) -> None:
    scope = session_scope("apply_patch", {"patch": "garbage"}, str(tmp_path))
    other = session_scope("apply_patch", {"patch": "other garbage"}, str(tmp_path))
    assert scope.target == "this exact change"
    assert not is_covered(other, [("apply_patch", scope.keys)])
    assert is_covered(scope, [("apply_patch", scope.keys)])


def test_other_tools_keep_whole_tool_grants() -> None:
    scope = session_scope("mcp_github_create_issue", {"title": "x"}, "/ws")
    assert scope.keys == ()
    assert scope.label == "Always allow `mcp_github_create_issue` this session"
    later = session_scope("mcp_github_create_issue", {"title": "y"}, "/ws")
    assert is_covered(later, [("mcp_github_create_issue", ())])
    assert not is_covered(later, [("mcp_github_close_issue", ())])


def test_a_whole_tool_grant_from_before_scopes_no_longer_covers_commands_or_edits() -> (
    None
):
    assert not is_covered(
        session_scope("bash", {"command": "ls"}, "/ws"), [("bash", ())]
    )
    assert not is_covered(
        session_scope("write", {"file_path": "a"}, "/ws"), [("write", ())]
    )


def test_scoped_grants_never_cover_a_whole_tool_request() -> None:
    scope = session_scope("mcp_x", {}, "/ws")
    assert not is_covered(scope, [("mcp_x", ("cmd:ls",))])


def test_the_payload_round_trips_and_rejects_malformed_values() -> None:
    scope = session_scope("bash", {"command": "git status"}, "/ws")
    assert SessionScope.from_payload("bash", scope.to_payload()) == scope
    assert SessionScope.from_payload("bash", None) is None
    assert SessionScope.from_payload("bash", {"keys": "cmd:ls", "target": "x"}) is None
    assert SessionScope.from_payload("bash", {"keys": [""], "target": "x"}) is None
    assert SessionScope.from_payload("bash", {"keys": ["cmd:ls"]}) is None


def test_prompts_name_the_scope_of_a_request() -> None:
    scope = session_scope("bash", {"command": "git status"}, "/ws")
    request = {"toolName": "bash", "sessionScope": scope.to_payload()}
    assert requested_session_target(request) == "`git status …`"
    assert requested_session_target({"toolName": "bash"}) == "this tool"


def test_exec_and_automation_prompts_say_what_always_allow_covers(
    monkeypatch, capsys
) -> None:
    import io
    import sys
    from types import SimpleNamespace

    from cli.automation_foreground import _resolve_interactively
    from cli.exec_cli import _approval_decider
    from core.domain.approval import ApprovalStatus

    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    scope = session_scope("bash", {"command": "npm test"}, "/ws")
    approval = SimpleNamespace(
        id="apr-1",
        request={
            "toolName": "bash",
            "reason": "Running a command needs your approval.",
            "sessionScope": scope.to_payload(),
        },
    )
    monkeypatch.setattr(sys, "stdin", _Tty("a\n"))
    assert _approval_decider(approval) is ApprovalStatus.APPROVED_SESSION
    assert "a = always allow `npm test …` this session" in capsys.readouterr().err

    prompts: list[str] = []
    answers = iter(["maybe", "a"])
    responses: list = []
    application = SimpleNamespace(
        approvals=SimpleNamespace(
            respond=lambda approval_id, decision: responses.append(decision)
        )
    )

    def reader(prompt: str) -> str:
        prompts.append(prompt)
        return next(answers)

    _resolve_interactively(application, approval, reader)
    assert "always allow `npm test …` this session [a]" in prompts[0]
    assert "a (always allow `npm test …` this session)" in prompts[1]
    assert responses == [ApprovalStatus.APPROVED_SESSION]
