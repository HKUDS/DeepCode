"""External CLI sub-agent backends (``core.harness.agents.external_backend``).

The contract under test, borrowed from dsh's subagent providers:

- acceptance is strict and per-CLI — codex: "exit 0 AND a non-blank
  last-message file"; claude-code: a JSON result envelope with
  ``subtype == "success"``, ``is_error`` false, and a non-blank ``result``.
  Every other outcome raises, so a blank string never passes for an answer;
- children get the credential-scrubbed parent environment, then the user's
  ``subagents.<backend>.env`` config layer (how the proxy the user's shell
  functions inject reaches the spawned CLI);
- every run has a wall-clock budget; expiry and cancellation both tear down
  the child's process tree;
- capability rules fail loud at spawn: no parent-context inheritance, no
  send_message, no unknown backend, no missing executable.
"""

from __future__ import annotations

import asyncio
import json
import stat
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.agents.control import AgentControl, AgentLimitError
from core.harness.agents.external_backend import (
    ExternalBackendError,
    run_external_subagent,
    scrubbed_parent_env,
)


def _fake_cli(tmp_path: Path, filename: str, body: str) -> str:
    """An executable fake CLI; ``body`` sees ``args``, ``task``, and ``out``
    (the last-message path when present in argv, else '')."""
    script = tmp_path / filename
    script.write_text(
        "#!/usr/bin/env python3\n"
        + textwrap.dedent(
            """\
            import os, sys
            args = sys.argv[1:]
            out = ""
            if "--output-last-message" in args:
                out = args[args.index("--output-last-message") + 1]
            task = sys.stdin.read()
            """
        )
        + textwrap.dedent(body),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


def _point_backend_at(monkeypatch: pytest.MonkeyPatch, executable: str) -> None:
    monkeypatch.setattr(
        "core.harness.agents.external_backend.resolve_executable",
        lambda cli: executable,
    )


def _no_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "core.harness.agents.external_backend.home_config_path",
        lambda: tmp_path / "absent-config.json",
    )


# -- codex acceptance ----------------------------------------------------------


def test_codex_success_returns_the_last_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_config(monkeypatch, tmp_path)
    _point_backend_at(
        monkeypatch,
        _fake_cli(
            tmp_path,
            "codex",
            """\
            with open(out, "w") as f:
                f.write("answer for: " + task.strip() + "\\n")
            """,
        ),
    )
    answer = asyncio.run(run_external_subagent("codex", "count files", tmp_path))
    assert answer == "answer for: count files"


def test_codex_blank_message_after_exit_zero_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_config(monkeypatch, tmp_path)
    _point_backend_at(monkeypatch, _fake_cli(tmp_path, "codex", "pass\n"))
    with pytest.raises(ExternalBackendError, match="no final message"):
        asyncio.run(run_external_subagent("codex", "task", tmp_path))


def test_codex_nonzero_exit_raises_with_stderr_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_config(monkeypatch, tmp_path)
    _point_backend_at(
        monkeypatch,
        _fake_cli(
            tmp_path,
            "codex",
            """\
            print("model refused politely", file=sys.stderr)
            sys.exit(3)
            """,
        ),
    )
    with pytest.raises(ExternalBackendError) as excinfo:
        asyncio.run(run_external_subagent("codex", "task", tmp_path))
    assert "exit code 3" in str(excinfo.value)
    assert "model refused politely" in str(excinfo.value)


# -- claude-code acceptance ----------------------------------------------------


def _claude_envelope_cli(tmp_path: Path, envelope: dict) -> str:
    payload = json.dumps(envelope)
    return _fake_cli(
        tmp_path,
        "claude",
        f"print({payload!r})\n",
    )


def test_claude_success_returns_the_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_config(monkeypatch, tmp_path)
    _point_backend_at(
        monkeypatch,
        _claude_envelope_cli(
            tmp_path,
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "the readme documents a game",
            },
        ),
    )
    answer = asyncio.run(run_external_subagent("claude-code", "task", tmp_path))
    assert answer == "the readme documents a game"


@pytest.mark.parametrize(
    "envelope",
    [
        {"type": "result", "subtype": "error_during_execution", "result": "x"},
        {"type": "result", "subtype": "success", "is_error": True, "result": "x"},
        {"type": "result", "subtype": "success", "is_error": False, "result": ""},
    ],
    ids=["error-subtype", "error-marked-success", "blank-result"],
)
def test_claude_rejects_every_non_success_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, envelope: dict
) -> None:
    _no_config(monkeypatch, tmp_path)
    _point_backend_at(monkeypatch, _claude_envelope_cli(tmp_path, envelope))
    with pytest.raises(ExternalBackendError, match="no accepted answer"):
        asyncio.run(run_external_subagent("claude-code", "task", tmp_path))


def test_claude_unparseable_output_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_config(monkeypatch, tmp_path)
    _point_backend_at(monkeypatch, _fake_cli(tmp_path, "claude", "print('not json')\n"))
    with pytest.raises(ExternalBackendError, match="unparseable"):
        asyncio.run(run_external_subagent("claude-code", "task", tmp_path))


# -- environment ---------------------------------------------------------------


def test_config_env_reaches_the_child_after_the_scrub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``subagents.<backend>.env`` layer is how the user's shell-function
    proxy reaches the spawned CLI — and the scrub must not eat it."""
    config = tmp_path / "deepcode_config.json"
    config.write_text(
        json.dumps(
            {
                "subagents": {
                    "codex": {"env": {"HTTPS_PROXY": "http://proxy.local:10085"}}
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "core.harness.agents.external_backend.home_config_path", lambda: config
    )
    _point_backend_at(
        monkeypatch,
        _fake_cli(
            tmp_path,
            "codex",
            """\
            with open(out, "w") as f:
                f.write(os.environ.get("HTTPS_PROXY", "missing"))
            """,
        ),
    )
    answer = asyncio.run(run_external_subagent("codex", "task", tmp_path))
    assert answer == "http://proxy.local:10085"


def test_credential_shaped_parent_env_is_scrubbed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_config(monkeypatch, tmp_path)
    _point_backend_at(
        monkeypatch,
        _fake_cli(
            tmp_path,
            "codex",
            """\
            with open(out, "w") as f:
                f.write("LEAK" if "DEEPCODE_API_KEY" in os.environ else "clean")
            """,
        ),
    )
    monkeypatch.setenv("DEEPCODE_API_KEY", "hunter2")
    answer = asyncio.run(run_external_subagent("codex", "task", tmp_path))
    assert answer == "clean"


def test_scrub_keeps_the_ordinary_environment() -> None:
    env = scrubbed_parent_env({"FORWARDED_TOKEN": "on purpose"})
    assert "PATH" in env
    assert env["FORWARDED_TOKEN"] == "on purpose"


# -- budget and cancellation ---------------------------------------------------


def _hanging_cli(tmp_path: Path, marker: Path) -> str:
    return _fake_cli(
        tmp_path,
        "codex",
        f"""\
        import time
        open({str(marker)!r}, "w").close()
        time.sleep(60)
        """,
    )


def test_run_budget_expiry_terminates_and_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_config(monkeypatch, tmp_path)
    _point_backend_at(monkeypatch, _hanging_cli(tmp_path, tmp_path / "m"))
    with pytest.raises(ExternalBackendError, match="exceeded its 0.5s budget"):
        asyncio.run(
            asyncio.wait_for(
                run_external_subagent("codex", "task", tmp_path, timeout_s=0.5),
                timeout=30,
            )
        )


def test_config_can_set_the_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "deepcode_config.json"
    config.write_text(
        json.dumps({"subagents": {"codex": {"timeoutS": 0.5}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "core.harness.agents.external_backend.home_config_path", lambda: config
    )
    _point_backend_at(monkeypatch, _hanging_cli(tmp_path, tmp_path / "m"))
    with pytest.raises(ExternalBackendError, match="0.5s budget"):
        asyncio.run(
            asyncio.wait_for(
                run_external_subagent("codex", "task", tmp_path), timeout=30
            )
        )


def test_cancellation_terminates_the_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_config(monkeypatch, tmp_path)
    marker = tmp_path / "started"
    _point_backend_at(monkeypatch, _hanging_cli(tmp_path, marker))

    async def scenario() -> None:
        run = asyncio.ensure_future(run_external_subagent("codex", "task", tmp_path))
        while not marker.exists():
            await asyncio.sleep(0.01)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

    asyncio.run(asyncio.wait_for(scenario(), timeout=30))


# -- AgentControl capability rules --------------------------------------------


def _control(tmp_path: Path) -> AgentControl:
    return AgentControl(str(tmp_path))


def test_spawn_rejects_unknown_backend(tmp_path: Path) -> None:
    with pytest.raises(AgentLimitError, match="unknown external backend"):
        _control(tmp_path).spawn("t", name="x", backend="gemini")


@pytest.mark.parametrize("backend", ["codex", "claude-code"])
def test_spawn_rejects_fork_turns_on_external_backends(
    tmp_path: Path, backend: str
) -> None:
    with pytest.raises(AgentLimitError, match="does not inherit"):
        _control(tmp_path).spawn("t", name="x", backend=backend, fork_turns="all")


def test_spawn_fails_loud_without_the_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(AgentLimitError, match="not found on PATH"):
        _control(tmp_path).spawn("t", name="x", backend="claude-code")


def test_send_message_refused_for_external_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> str:
        control = _control(tmp_path)
        exe = _fake_cli(
            tmp_path,
            "codex",
            """\
            with open(out, "w") as f:
                f.write("done")
            """,
        )
        monkeypatch.setattr("shutil.which", lambda _: exe)
        monkeypatch.setattr(
            "core.harness.agents.external_backend.resolve_executable",
            lambda cli: exe,
        )
        monkeypatch.setattr(
            "core.harness.agents.external_backend.home_config_path",
            lambda: tmp_path / "absent-config.json",
        )
        agent_id = control.spawn("t", name="x", backend="codex", isolate=False)
        reply = control.send_message(agent_id, "extra context")
        await control.cancel_running()
        return reply

    reply = asyncio.run(scenario())
    assert "accepts no messages" in reply
