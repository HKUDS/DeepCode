"""Local Speech-to-Text inference runner for Parakeet.

Runs Parakeet models directly on Apple Silicon / local machine without requiring
a separate HTTP daemon.

Tries loading the model via:
1. Direct Python in-process worker / `parakeet-mlx` if available.
2. CLI fallback via `parakeet-mlx` command binary.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from core.dictation.client import TranscriptionFailed

_PARAKEET_CLI = shutil.which("parakeet-mlx") or os.path.expanduser(
    "~/.local/bin/parakeet-mlx"
)


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
        cli = _PARAKEET_CLI
        if not (cli and (os.path.isfile(cli) and os.access(cli, os.X_OK))):
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
            env = dict(os.environ)
            env["NUMBA_DISABLE_JIT"] = "1"

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
