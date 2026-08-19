"""Two real processes, one Session, a genuine mid-flight collision.

The dsh session contract — one live writer per Session — enforced by the
run lease and verified end to end: process A executes a turn (its provider
sleeps), process B submits against the same Session while A is mid-flight.
B must receive the human-readable refusal and exit cleanly; A must finish
undisturbed; the canonical record must hold exactly A's work.

This drives the real TUI entry point in real subprocesses; it takes a few
seconds by design.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_DRIVER = """
import io, os, sys, json, time
sys.path.insert(0, %(root)r)
from core.providers.base import LLMResponse
from core import agent_setup
import cli.tui.app as tui_app


class _Profile:
    model = "fake-model"


MARKER = os.environ.get("MARKER")
HOLD = os.environ.get("HOLD")


class _Provider:
    def get_default_model(self):
        return "fake-model"

    async def chat_with_retry(self, **_kwargs):
        if MARKER:
            open(MARKER, "w").write("turn-started")
        if HOLD:
            # Deterministic collision window: the turn stays mid-flight until
            # the test deletes the hold file. No timing assumptions.
            deadline = time.monotonic() + 60
            while os.path.exists(HOLD) and time.monotonic() < deadline:
                time.sleep(0.1)
        return LLMResponse(
            content=os.environ["TAG"] + " done", finish_reason="stop"
        )


agent_setup.get_workflow_provider = lambda **_k: (_Provider(), _Profile())
agent_setup.get_runtime = lambda: type(
    "R", (), {"config": type("C", (), {"security": None})()}
)()
sys.stdin = io.StringIO(os.environ["SCRIPT"])
sys.argv = ["tui"]
raise SystemExit(tui_app.main(json.loads(os.environ["ARGV"])))
"""


def _run(script: Path, env: dict, *, wait: bool = True):
    proc = subprocess.Popen(
        [sys.executable, str(script)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=ROOT,
    )
    if not wait:
        return proc
    out, err = proc.communicate(timeout=90)
    return proc.returncode, out.decode(), err.decode()


def test_submitting_into_anothers_running_turn_is_refused_politely(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    script = tmp_path / "drive.py"
    script.write_text(_DRIVER % {"root": str(ROOT)})
    base_env = {
        **__import__("os").environ,
        "DEEPCODE_HOME": str(tmp_path / "home"),
        "DEEPCODE_SESSIONS_DIR": str(tmp_path / "sessions"),
    }
    argv = ["--workspace", str(workspace), "--trust", "--access", "full-access"]

    # Seed the Session sequentially — no concurrency yet.
    rc, _out, err = _run(
        script,
        {
            **base_env,
            "TAG": "seed",
            "SCRIPT": "hello\n/exit\n",
            "ARGV": json.dumps(argv),
        },
    )
    assert rc == 0, err[-800:]
    sessions = tmp_path / "sessions"
    session_id = next(
        d.name for d in sessions.iterdir() if d.is_dir() and not d.name.startswith(".")
    )

    # A executes with a provider that sleeps; the marker file tells us the
    # turn is genuinely mid-flight before B submits.
    marker = tmp_path / "turn-started.marker"
    hold = tmp_path / "hold-the-turn"
    hold.write_text("held")
    proc_a = _run(
        script,
        {
            **base_env,
            "TAG": "A",
            "SCRIPT": "long job\n/exit\n",
            "ARGV": json.dumps([*argv, "--resume", session_id]),
            "MARKER": str(marker),
            "HOLD": str(hold),
        },
        wait=False,
    )
    # A pre-existing, documented crash can kill B at STARTUP — a WAL
    # "disk I/O error" while opening the shared database mid-turn of the
    # other process (docs/investigations/2026-08-19-concurrent-turn-submit.md;
    # reproduced on ubuntu CI runners at resume-time projection reads). That
    # failure happens before the collision under test here, so B retries a
    # bounded number of times on exactly that signature. Any other death is
    # a real failure of this test's subject.
    _PREEXISTING = ("disk I/O error", "FOREIGN KEY constraint failed")
    try:
        deadline = time.monotonic() + 20
        while not marker.exists():
            assert time.monotonic() < deadline, "A's turn never started"
            time.sleep(0.1)

        for attempt in range(3):
            rc_b, out_b, err_b = _run(
                script,
                {
                    **base_env,
                    "TAG": "B",
                    "SCRIPT": "quick question\n/exit\n",
                    "ARGV": json.dumps([*argv, "--resume", session_id]),
                },
            )
            startup_crash = rc_b != 0 and any(
                signature in err_b for signature in _PREEXISTING
            )
            if not startup_crash:
                break
    finally:
        # Only after B's outcome is decided does A's turn get to finish.
        hold.unlink(missing_ok=True)
        out_a, err_a = proc_a.communicate(timeout=90)

    assert rc_b == 0, f"B must exit cleanly, not crash:\n{err_b[-800:]}"
    assert "currently running a turn in" in out_b, out_b[-800:]
    assert proc_a.returncode == 0, err_a.decode()[-800:]
    assert "A done" in out_a.decode()

    # The canonical record holds exactly A's work; B's refused submission
    # left no residue.
    record = sessions / session_id / "session.jsonl"
    contents = [
        json.loads(line)
        for line in record.read_text().splitlines()
        if line.strip() and json.loads(line).get("_type") == "message"
    ]
    texts = [str(message.get("content")) for message in contents]
    assert any("long job" in text for text in texts)
    assert any("A done" in text for text in texts)
    assert not any("quick question" in text for text in texts)
