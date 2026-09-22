"""LocalParakeetClient drives the parakeet-mlx CLI with a bounded environment."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from core.dictation import local_runner
from core.dictation.client import TranscriptionFailed
from core.dictation.local_runner import LocalParakeetClient

pytestmark = pytest.mark.skipif(os.name == "nt", reason="fake CLI is a POSIX script")


def _fake_cli(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "parakeet-mlx"
    script.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def _install(monkeypatch, script: Path) -> None:
    monkeypatch.setattr(local_runner, "_find_cli", lambda: str(script))


def test_transcribes_through_the_cli_and_reads_its_json(tmp_path, monkeypatch):
    # Echo the argv into the JSON so the test can see what the CLI received.
    script = _fake_cli(
        tmp_path,
        'out=""; while [ $# -gt 0 ]; do case "$1" in --output-dir) out="$2"; shift;; esac; shift; done\n'
        'printf \'{"text": "hello world"}\' > "$out/input.json"\n',
    )
    _install(monkeypatch, script)
    client = LocalParakeetClient("some/model", language="en")
    assert (
        client.transcribe(b"RIFF....", filename="input.wav", mime_type="audio/wav")
        == "hello world"
    )


def test_language_hint_and_model_reach_the_cli(tmp_path, monkeypatch):
    log = tmp_path / "argv.txt"
    script = _fake_cli(
        tmp_path,
        f'printf "%s\\n" "$@" > "{log}"\n'
        'out=""; while [ $# -gt 0 ]; do case "$1" in --output-dir) out="$2"; shift;; esac; shift; done\n'
        'printf \'{"text": ""}\' > "$out/input.json"\n',
    )
    _install(monkeypatch, script)
    LocalParakeetClient("m/x", language="de").transcribe(
        b"x", filename="a.webm", mime_type="audio/webm"
    )
    argv = log.read_text(encoding="utf-8").splitlines()
    assert "--model" in argv and argv[argv.index("--model") + 1] == "m/x"
    assert "--language" in argv and argv[argv.index("--language") + 1] == "de"


def test_child_environment_excludes_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    log = tmp_path / "env.txt"
    script = _fake_cli(
        tmp_path,
        f'env > "{log}"\n'
        'out=""; while [ $# -gt 0 ]; do case "$1" in --output-dir) out="$2"; shift;; esac; shift; done\n'
        'printf \'{"text": ""}\' > "$out/input.json"\n',
    )
    _install(monkeypatch, script)
    LocalParakeetClient("m").transcribe(b"x", filename="a.wav", mime_type="audio/wav")
    env = log.read_text(encoding="utf-8")
    assert "DEEPSEEK_API_KEY" not in env
    assert "NUMBA_DISABLE_JIT=1" in env
    assert "PATH=" in env


def test_nonzero_exit_is_a_non_retryable_failure_without_the_body(
    tmp_path, monkeypatch
):
    script = _fake_cli(tmp_path, 'echo "model not found: token=abc" >&2; exit 3\n')
    _install(monkeypatch, script)
    with pytest.raises(TranscriptionFailed) as info:
        LocalParakeetClient("m").transcribe(
            b"x", filename="a.wav", mime_type="audio/wav"
        )
    assert not info.value.retryable
    assert "Local dictation failed" in str(info.value)


def test_missing_cli_is_reported(monkeypatch):
    monkeypatch.setattr(local_runner, "_find_cli", lambda: None)
    with pytest.raises(TranscriptionFailed, match="parakeet-mlx"):
        LocalParakeetClient("m").transcribe(
            b"x", filename="a.wav", mime_type="audio/wav"
        )


def test_timeout_is_retryable(tmp_path, monkeypatch):
    script = _fake_cli(tmp_path, "sleep 5\n")
    _install(monkeypatch, script)
    with pytest.raises(TranscriptionFailed) as info:
        LocalParakeetClient("m", timeout_seconds=0.2).transcribe(
            b"x", filename="a.wav", mime_type="audio/wav"
        )
    assert info.value.retryable


def test_find_cli_prefers_path_then_local_bin(tmp_path, monkeypatch):
    monkeypatch.setattr(local_runner.shutil, "which", lambda name: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    assert local_runner._find_cli() is None
    script = tmp_path / ".local" / "bin" / "parakeet-mlx"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    assert local_runner._find_cli() == str(script)
