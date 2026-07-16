"""Ephemeral PTY sessions with explicit Thread ownership and process cleanup."""

from __future__ import annotations

import codecs
import os
import signal
import struct
import subprocess
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.application.errors import (
    ConflictError,
    InvalidArgumentError,
    NotSupportedApplicationError,
    TerminalNotFoundError,
)
from core.application.workspace_service import WorkspaceService

if os.name != "nt":
    import fcntl
    import pty
    import termios


TerminalListener = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True, slots=True)
class TerminalInfo:
    id: str
    thread_id: str
    pid: int
    columns: int
    rows: int
    workspace_path: str


@dataclass(slots=True)
class _TerminalSession:
    info: TerminalInfo
    process: subprocess.Popen[bytes]
    master_fd: int
    closing: bool = False


class TerminalService:
    def __init__(self, workspaces: WorkspaceService, *, max_sessions: int = 8) -> None:
        self.workspaces = workspaces
        self.max_sessions = max_sessions
        self._lock = threading.RLock()
        self._sessions: dict[str, _TerminalSession] = {}
        self._listeners: dict[str, TerminalListener] = {}
        self._creating = 0

    def subscribe(self, listener: TerminalListener) -> str:
        token = uuid.uuid4().hex
        with self._lock:
            self._listeners[token] = listener
        return token

    def unsubscribe(self, token: str) -> None:
        with self._lock:
            self._listeners.pop(token, None)

    def create(
        self, thread_id: str, *, columns: int = 100, rows: int = 30
    ) -> TerminalInfo:
        if os.name == "nt":
            raise NotSupportedApplicationError(
                "PTY terminals require the Windows ConPTY adapter"
            )
        self._validate_size(columns, rows)
        context = self.workspaces.resolve(thread_id, require_trusted=True)
        with self._lock:
            if len(self._sessions) + self._creating >= self.max_sessions:
                raise ConflictError("maximum terminal session count reached")
            self._creating += 1
        master_fd: int | None = None
        slave_fd: int | None = None
        registered = False
        try:
            master_fd, slave_fd = pty.openpty()
            self._set_size(master_fd, columns, rows)
            shell = _shell_path()
            environment = {
                **os.environ,
                "TERM": os.environ.get("TERM", "xterm-256color"),
                "DEEPCODE_THREAD_ID": thread_id,
            }
            try:
                process = subprocess.Popen(
                    [shell],
                    cwd=context.root,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    close_fds=True,
                    start_new_session=True,
                    env=environment,
                )
            finally:
                os.close(slave_fd)
                slave_fd = None
            terminal_id = f"term_{uuid.uuid4().hex}"
            info = TerminalInfo(
                id=terminal_id,
                thread_id=thread_id,
                pid=process.pid,
                columns=columns,
                rows=rows,
                workspace_path=str(context.root),
            )
            session = _TerminalSession(info=info, process=process, master_fd=master_fd)
            with self._lock:
                self._sessions[terminal_id] = session
                self._creating -= 1
                registered = True
            threading.Thread(
                target=self._read_output,
                args=(session,),
                name=f"deepcode-terminal-{terminal_id[-8:]}",
                daemon=True,
            ).start()
            return info
        except OSError as exc:
            raise ConflictError(f"terminal could not start: {exc}") from exc
        finally:
            if not registered:
                with self._lock:
                    self._creating -= 1
                for descriptor in (slave_fd, master_fd):
                    if descriptor is not None:
                        try:
                            os.close(descriptor)
                        except OSError:
                            pass

    def write(self, thread_id: str, terminal_id: str, data: str) -> int:
        encoded = data.encode("utf-8")
        if len(encoded) > 64 * 1024:
            raise InvalidArgumentError("terminal input exceeds 64 KiB")
        session = self._owned(thread_id, terminal_id)
        try:
            return os.write(session.master_fd, encoded)
        except OSError as exc:
            raise ConflictError("terminal is no longer writable") from exc

    def resize(
        self, thread_id: str, terminal_id: str, *, columns: int, rows: int
    ) -> TerminalInfo:
        self._validate_size(columns, rows)
        session = self._owned(thread_id, terminal_id)
        self._set_size(session.master_fd, columns, rows)
        with self._lock:
            session.info = TerminalInfo(
                id=session.info.id,
                thread_id=session.info.thread_id,
                pid=session.info.pid,
                columns=columns,
                rows=rows,
                workspace_path=session.info.workspace_path,
            )
            return session.info

    def close(self, thread_id: str, terminal_id: str) -> bool:
        session = self._owned(thread_id, terminal_id)
        with self._lock:
            if session.closing:
                return False
            session.closing = True
        self._terminate(session.process)
        try:
            os.close(session.master_fd)
        except OSError:
            pass
        return True

    def active_for_thread(self, thread_id: str) -> bool:
        with self._lock:
            return any(
                session.info.thread_id == thread_id and session.process.poll() is None
                for session in self._sessions.values()
            )

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            if session.process.poll() is None:
                session.closing = True
                self._terminate(session.process)
            try:
                os.close(session.master_fd)
            except OSError:
                pass

    def _owned(self, thread_id: str, terminal_id: str) -> _TerminalSession:
        with self._lock:
            session = self._sessions.get(terminal_id)
        if session is None or session.info.thread_id != thread_id:
            raise TerminalNotFoundError(f"terminal not found for Thread: {terminal_id}")
        return session

    def _read_output(self, session: _TerminalSession) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while True:
                try:
                    raw = os.read(session.master_fd, 16 * 1024)
                except OSError:
                    break
                if not raw:
                    break
                text = decoder.decode(raw)
                if text:
                    self._publish(
                        "terminal.output",
                        {
                            "terminalId": session.info.id,
                            "threadId": session.info.thread_id,
                            "data": text,
                        },
                    )
            trailing = decoder.decode(b"", final=True)
            if trailing:
                self._publish(
                    "terminal.output",
                    {
                        "terminalId": session.info.id,
                        "threadId": session.info.thread_id,
                        "data": trailing,
                    },
                )
        finally:
            try:
                exit_code = session.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._terminate(session.process)
                exit_code = session.process.returncode
            with self._lock:
                if self._sessions.get(session.info.id) is session:
                    self._sessions.pop(session.info.id, None)
            self._publish(
                "terminal.exit",
                {
                    "terminalId": session.info.id,
                    "threadId": session.info.thread_id,
                    "exitCode": exit_code,
                },
            )

    def _publish(self, method: str, payload: dict[str, Any]) -> None:
        with self._lock:
            listeners = tuple(self._listeners.values())
        for listener in listeners:
            try:
                listener(method, payload)
            except Exception:
                continue

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=0.75)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass

    @staticmethod
    def _validate_size(columns: int, rows: int) -> None:
        if not 20 <= columns <= 500 or not 5 <= rows <= 200:
            raise InvalidArgumentError("terminal size is outside supported bounds")

    @staticmethod
    def _set_size(file_descriptor: int, columns: int, rows: int) -> None:
        if os.name == "nt":
            return
        fcntl.ioctl(
            file_descriptor,
            termios.TIOCSWINSZ,
            struct.pack("HHHH", rows, columns, 0, 0),
        )


def _shell_path() -> str:
    configured = os.environ.get("SHELL")
    if configured and Path(configured).is_file():
        return configured
    return "/bin/sh"
