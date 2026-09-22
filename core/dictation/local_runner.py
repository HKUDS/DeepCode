"""Local speech-to-text runner backed by the ``parakeet-mlx`` command line.

``dictation.endpoint = "local"`` transcribes without any HTTP server: the clip
is written to a private temporary directory and handed to ``parakeet-mlx``
(``pip install parakeet-mlx``), which writes a JSON transcript next to it. The
CLI is located on every call, so installing it does not require a restart, and
the child gets a minimal environment rather than the harness's provider keys.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from core.dictation.client import TranscriptionFailed

_CLI_NAME = "parakeet-mlx"
# Variables a CLI needs to run and to find its model cache; nothing else from
# the harness environment (provider keys in particular) is forwarded.
_CHILD_ENV_KEYS = (
    "PATH",
    "HOME",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "HF_HOME",
    "HF_HUB_CACHE",
    "HF_HUB_OFFLINE",
    "XDG_CACHE_HOME",
)


def _find_cli() -> str | None:
    """The executable path of ``parakeet-mlx``, or ``None`` when absent."""
    found = shutil.which(_CLI_NAME)
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / _CLI_NAME
    if fallback.is_file() and os.access(fallback, os.X_OK):
        return str(fallback)
    return None


def _child_env() -> dict[str, str]:
    env = {key: os.environ[key] for key in _CHILD_ENV_KEYS if key in os.environ}
    env["NUMBA_DISABLE_JIT"] = "1"
    return env


class LocalParakeetClient:
    """Synchronous speech-to-text runner using local parakeet-mlx."""

    def __init__(
        self,
        model: str,
        *,
        language: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._model = model
        self._language = language
        self._timeout_seconds = timeout_seconds

    @property
    def url(self) -> str:
        return "local:parakeet-mlx"

    def transcribe(self, audio: bytes, *, filename: str, mime_type: str) -> str:
        """Run local transcription on the clip."""
        cli = _find_cli()
        if cli is None:
            raise TranscriptionFailed(
                "Local dictation requires 'parakeet-mlx' installed and available in PATH. "
                "Run: pip install parakeet-mlx",
                retryable=False,
            )

        ext = filename.split(".", 1)[-1] if "." in filename else "wav"
        with tempfile.TemporaryDirectory(prefix="deepcode-dictation-") as tmpdir:
            input_file = Path(tmpdir) / f"input.{ext}"
            input_file.write_bytes(audio)

            cmd = [
                cli,
                str(input_file),
                "--model",
                self._model,
                "--output-format",
                "json",
                "--output-dir",
                tmpdir,
            ]
            if self._language:
                cmd += ["--language", self._language]
            env = _child_env()

            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=self._timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise TranscriptionFailed(
                    f"Local dictation did not finish within {self._timeout_seconds:g}s",
                    retryable=True,
                ) from exc
            except Exception as exc:
                raise TranscriptionFailed(
                    f"Local dictation process failed ({type(exc).__name__})",
                    retryable=True,
                ) from exc

            if proc.returncode != 0:
                err_msg = proc.stderr.strip() or "exit code " + str(proc.returncode)
                # Keep error message concise and safe
                first_err = err_msg.splitlines()[-1] if err_msg else "unknown error"
                raise TranscriptionFailed(
                    f"Local dictation failed: {first_err}",
                    retryable=False,
                )

            json_out = Path(tmpdir) / "input.json"
            if not json_out.exists():
                return ""

            try:
                data = json.loads(json_out.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise TranscriptionFailed(
                    "Local dictation returned invalid json",
                    retryable=False,
                ) from exc

            text = data.get("text", "")
            return text if isinstance(text, str) else ""
