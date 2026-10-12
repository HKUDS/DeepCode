"""Drive the real TUI in a pseudo-terminal.

The process gets a real terminal of its own (a controlling tty, a window
size, raw-mode key handling). Tests read it two ways:

- ``text()`` — the bytes the TUI wrote, escape sequences stripped. Good for
  "did this ever appear" and for checking where the TUI put newlines.
- ``screen()`` — what a user sees now, scrollback included, from a terminal
  emulator (pyte). Text that was only shown for a moment (the status line,
  the preview of an unfinished line) is redrawn many times in the byte
  stream but is not on this screen, so counts here are counts a user sees.
"""

from __future__ import annotations

import fcntl
import os
import re
import select
import struct
import subprocess
import termios
import threading
import time

import pyte

COLUMNS, ROWS = 160, 48
_SCROLLBACK = 10_000

ESC = "\x1b"
CTRL_C = "\x03"
CTRL_D = "\x04"
CTRL_O = "\x0f"
ENTER = "\r"

_ANSI = re.compile(
    rb"\x1b\[[0-?]*[ -/]*[@-~]"  # CSI
    rb"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC
    rb"|\x1b[()][0-9A-Za-z]"  # charset
    rb"|\x1b[=>78]"
)
_CURSOR_POSITION_REQUEST = b"\x1b[6n"


def _become_terminal_owner() -> None:  # runs in the child, before exec
    os.setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


class Terminal:
    """One TUI process attached to a pseudo-terminal."""

    def __init__(self, command: list[str], *, env: dict, cwd: str) -> None:
        master, slave = os.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLUMNS, 0, 0))
        self.process = subprocess.Popen(
            command,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env={
                **env,
                "TERM": "xterm-256color",
                "COLUMNS": str(COLUMNS),
                "LINES": str(ROWS),
            },
            cwd=cwd,
            preexec_fn=_become_terminal_owner,
            close_fds=True,
        )
        os.close(slave)
        self._fd = master
        self._raw = bytearray()
        self._screen = pyte.HistoryScreen(COLUMNS, ROWS, history=_SCROLLBACK)
        self._stream = pyte.ByteStream(self._screen)
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._read, name="tui-pty", daemon=True)
        self._reader.start()

    def _read(self) -> None:
        while True:
            try:
                ready, _, _ = select.select([self._fd], [], [], 0.1)
                if not ready:
                    if self.process.poll() is not None:
                        return
                    continue
                chunk = os.read(self._fd, 65536)
            except OSError:
                return
            if not chunk:
                return
            if _CURSOR_POSITION_REQUEST in chunk:
                # prompt_toolkit asks where the cursor is; a terminal answers.
                os.write(self._fd, b"\x1b[1;1R")
            with self._lock:
                self._raw += chunk
                self._stream.feed(chunk)

    # -- reading ------------------------------------------------------------

    def screen(self) -> str:
        """The terminal as a user sees it now: scrollback, then the window."""

        with self._lock:
            history = [
                "".join(line[column].data for column in range(COLUMNS))
                for line in self._screen.history.top
            ]
            window = list(self._screen.display)
        return "\n".join(line.rstrip() for line in history + window)

    def text(self, since: int = 0) -> str:
        """Everything shown so far, as plain text, from offset ``since``."""

        with self._lock:
            data = bytes(self._raw)
        plain = _ANSI.sub(b"", data).replace(b"\r", b"").decode("utf-8", "replace")
        return plain[since:]

    def mark(self) -> int:
        return len(self.text())

    def tail(self, since: int = 0, size: int = 2500) -> str:
        """The end of the screen with blank runs folded, for failure messages."""

        lines = [line.rstrip() for line in self.text(since).splitlines()]
        return "\n".join(line for line in lines if line)[-size:]

    def wait_for(
        self, pattern: str, *, timeout: float = 20.0, since: int = 0
    ) -> re.Match:
        deadline = time.monotonic() + timeout
        while True:
            match = re.search(pattern, self.text(since))
            if match:
                return match
            if time.monotonic() > deadline:
                raise AssertionError(
                    f"{pattern!r} did not appear within {timeout}s; screen tail:\n"
                    + self.tail(since)
                )
            if self.process.poll() is not None:
                raise AssertionError(
                    f"TUI exited ({self.process.returncode}) before {pattern!r}; screen tail:\n"
                    + self.tail(since)
                )
            time.sleep(0.05)

    def wait_for_screen(self, pattern: str, *, timeout: float = 20.0) -> re.Match:
        deadline = time.monotonic() + timeout
        while True:
            match = re.search(pattern, self.screen())
            if match:
                return match
            if time.monotonic() > deadline:
                raise AssertionError(
                    f"{pattern!r} was not on screen within {timeout}s; screen:\n"
                    + self.screen()[-2500:]
                )
            time.sleep(0.05)

    # -- typing -------------------------------------------------------------

    def send(self, keys: str, pause: float = 0.15) -> None:
        os.write(self._fd, keys.encode())
        time.sleep(pause)

    def submit(self, line: str) -> None:
        """Type a line, then press Enter once completion has settled."""

        self.send(line, 0.3)
        self.send(ENTER, 0.2)

    # -- shutdown -----------------------------------------------------------

    def close(self) -> int:
        if self.process.poll() is None:
            try:
                self.send(CTRL_D, 0.5)
                self.process.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.wait(timeout=10)
        self._reader.join(timeout=2)
        try:
            os.close(self._fd)
        except OSError:
            pass
        return self.process.returncode
