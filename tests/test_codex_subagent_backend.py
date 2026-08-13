"""Codex CLI as a sub-agent backend (``core.harness.agents.codex_backend``).

The contract under test, borrowed from dsh's ``subagent-codex`` provider:

- acceptance is "exit 0 AND a non-blank last-message file" — every other
  outcome raises, so the caller's envelope reports what happened rather than
  passing a blank string off as an answer;
- the child gets the credential-scrubbed parent environment (its own auth
  store under ``$HOME`` still works; the harness's keys never leak);
- cancellation tears down the child's whole process group and waits;
- capability rules fail loud at spawn: no parent-context inheritance, no
  send_message, no unknown backend, no missing executable.

The fake ``codex`` executable is a script that reads argv/stdin the way the
real CLI's ``exec --output-last-message`` mode does.
"""

from __future__ import annotations

import asyncio
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.agents.codex_backend import (
    CodexBackendError,
    run_codex_subagent,
    scrubbed_parent_env,
)
from core.harness.agents.control import AgentControl, AgentLimitError


def _fake_codex(tmp_path: Path, body: str) -> str:
    """Write an executable fake ``codex`` whose behavior is ``body``.

    The scaffold parses ``exec --skip-git-repo-check --output-last-message
    <file> -`` and exposes ``out`` (the last-message path) and ``task`` (the
    stdin text) to the body.
    """
    script = tmp_path / "codex"
    script.write_text(
        "#!/usr/bin/env python3\n"
        + textwrap.dedent(
            """\
            import os, sys
            args = sys.argv[1:]
            assert args[0] == "exec", args
            out = args[args.index("--output-last-message") + 1]
            task = sys.stdin.read()
            """
        )
        + textwrap.dedent(body),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


def test_success_returns_the_last_message() -> None:
    def run(tmp_path: Path) -> str:
        exe = _fake_codex(
            tmp_path,
            """\
            with open(out, "w") as f:
                f.write("answer for: " + task.strip() + "\\n")
            """,
        )
        return asyncio.run(
            run_codex_subagent("count the files", tmp_path, executable=exe)
        )

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        assert run(Path(tmp)) == "answer for: count the files"


def test_nonzero_exit_raises_with_stderr_tail(tmp_path: Path) -> None:
    exe = _fake_codex(
        tmp_path,
        """\
        print("model refused politely", file=sys.stderr)
        sys.exit(3)
        """,
    )
    with pytest.raises(CodexBackendError) as excinfo:
        asyncio.run(run_codex_subagent("task", tmp_path, executable=exe))
    assert "exit code 3" in str(excinfo.value)
    assert "model refused politely" in str(excinfo.value)


def test_blank_message_after_exit_zero_raises(tmp_path: Path) -> None:
    # Codex does not write the file on a failed turn; an unwritten (or blank)
    # file after exit 0 is still "no accepted answer", never an empty answer.
    exe = _fake_codex(tmp_path, "pass\n")
    with pytest.raises(CodexBackendError) as excinfo:
        asyncio.run(run_codex_subagent("task", tmp_path, executable=exe))
    assert "no final message" in str(excinfo.value)


def test_child_env_is_credential_scrubbed(tmp_path: Path) -> None:
    exe = _fake_codex(
        tmp_path,
        """\
        with open(out, "w") as f:
            f.write("SECRET" if "DEEPCODE_API_KEY" in os.environ else "clean")
        """,
    )
    os.environ["DEEPCODE_API_KEY"] = "hunter2"
    try:
        answer = asyncio.run(run_codex_subagent("task", tmp_path, executable=exe))
    finally:
        del os.environ["DEEPCODE_API_KEY"]
    assert answer == "clean"


def test_scrub_keeps_the_ordinary_environment() -> None:
    env = scrubbed_parent_env({"FORWARDED_TOKEN": "on purpose"})
    assert "PATH" in env
    # An explicitly forwarded entry survives because it merges after the scrub.
    assert env["FORWARDED_TOKEN"] == "on purpose"
    assert not any(k for k in env if "API_KEY" in k.upper() and k != "FORWARDED_TOKEN")


def test_cancellation_terminates_the_child(tmp_path: Path) -> None:
    marker = tmp_path / "started"
    exe = _fake_codex(
        tmp_path,
        f"""\
        import time
        open({str(marker)!r}, "w").close()
        time.sleep(60)
        """,
    )

    async def scenario() -> None:
        run = asyncio.ensure_future(
            run_codex_subagent("task", tmp_path, executable=exe)
        )
        while not marker.exists():  # child is definitely alive
            await asyncio.sleep(0.01)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

    asyncio.run(asyncio.wait_for(scenario(), timeout=30))


# -- AgentControl capability rules --------------------------------------------


def _control(tmp_path: Path) -> AgentControl:
    return AgentControl(str(tmp_path))


def test_spawn_rejects_unknown_backend(tmp_path: Path) -> None:
    with pytest.raises(AgentLimitError, match="unknown backend"):
        _control(tmp_path).spawn("t", name="x", backend="claude-code")


def test_spawn_rejects_fork_turns_on_codex(tmp_path: Path) -> None:
    with pytest.raises(AgentLimitError, match="does not inherit"):
        _control(tmp_path).spawn("t", name="x", backend="codex", fork_turns="all")


def test_spawn_fails_loud_without_the_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(AgentLimitError, match="not found on PATH"):
        _control(tmp_path).spawn("t", name="x", backend="codex")


def test_send_message_refused_for_external_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> str:
        control = _control(tmp_path)
        exe = _fake_codex(
            tmp_path,
            """\
            with open(out, "w") as f:
                f.write("done")
            """,
        )
        monkeypatch.setattr("shutil.which", lambda _: exe)
        monkeypatch.setattr(
            "core.harness.agents.codex_backend.resolve_codex_executable",
            lambda executable="codex": exe,
        )
        agent_id = control.spawn("t", name="x", backend="codex", isolate=False)
        reply = control.send_message(agent_id, "extra context")
        await control.cancel_running()
        return reply

    reply = asyncio.run(scenario())
    assert "accepts no messages" in reply
