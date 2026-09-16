"""Ephemeral PTY sessions with explicit Thread ownership and process cleanup."""

from __future__ import annotations

import codecs
import os
import select
import signal
import struct
import subprocess
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.application.errors import (
    ConflictError,
    InvalidArgumentError,
    NotSupportedApplicationError,
    TerminalNotFoundError,
    ThreadNotFoundError,
)
from core.application.workspace_service import WorkspaceService
from core.application.views import terminal_info_view
from core.file_lock import FileLease
from core.sessions import SessionStore

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
    master_fd: int | None
    activity_lease: FileLease
    closing: bool = False
    output: bytearray = field(default_factory=bytearray)
    offset: int = 0
    exited: bool = False
    exit_code: int | None = None
    reader: threading.Thread | None = None
    io_lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def head(self) -> int:
        return self.offset + len(self.output)


class TerminalService:
    def __init__(
        self,
        workspaces: WorkspaceService,
        sessions: SessionStore,
        *,
        max_sessions: int = 8,
        output_capacity: int = 256 * 1024,
        retained_exits: int = 8,
    ) -> None:
        if max_sessions < 1 or output_capacity < 4 or retained_exits < 0:
            raise ValueError("invalid terminal retention limits")
        self.workspaces = workspaces
        self.sessions = sessions
        self.max_sessions = max_sessions
        self.output_capacity = output_capacity
        self.retained_exits = retained_exits
        self._lock = threading.RLock()
        self._sessions: dict[str, _TerminalSession] = {}
        self._finished: OrderedDict[str, _TerminalSession] = OrderedDict()
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
        activity_lease = self.sessions.acquire_activity_lease(thread_id)
        if activity_lease is None:
            raise ThreadNotFoundError(f"thread not found: {thread_id}")
        master_fd: int | None = None
        slave_fd: int | None = None
        registered = False
        counted = False
        try:
            context = self.workspaces.resolve(thread_id, require_trusted=True)
            with self._lock:
                if len(self._sessions) + self._creating >= self.max_sessions:
                    raise ConflictError("maximum terminal session count reached")
                self._creating += 1
                counted = True
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
            session = _TerminalSession(
                info=info,
                process=process,
                master_fd=master_fd,
                activity_lease=activity_lease,
            )
            with self._lock:
                self._sessions[terminal_id] = session
                self._creating -= 1
                registered = True
            session.reader = threading.Thread(
                target=self._read_output,
                args=(session,),
                name=f"deepcode-terminal-{terminal_id[-8:]}",
                daemon=True,
            )
            try:
                session.reader.start()
            except BaseException:
                with self._lock:
                    self._sessions.pop(terminal_id, None)
                self._terminate(process)
                os.close(master_fd)
                session.master_fd = None
                activity_lease.close()
                raise
            return info
        except OSError as exc:
            raise ConflictError(f"terminal could not start: {exc}") from exc
        finally:
            if not registered:
                activity_lease.close()
                if counted:
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
        with session.io_lock:
            if session.master_fd is None or session.closing:
                raise ConflictError("terminal is no longer writable")
            try:
                return os.write(session.master_fd, encoded)
            except OSError as exc:
                raise ConflictError("terminal is no longer writable") from exc

    def resize(
        self, thread_id: str, terminal_id: str, *, columns: int, rows: int
    ) -> TerminalInfo:
        self._validate_size(columns, rows)
        session = self._owned(thread_id, terminal_id)
        with session.io_lock:
            if session.master_fd is None or session.closing:
                raise ConflictError("terminal has closed")
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
        return True

    def list(self, thread_id: str) -> list[dict[str, Any]]:
        """Discover live terminals and a bounded set of completed output windows."""
        self._require_thread(thread_id)
        with self._lock:
            return [
                {
                    "terminal": terminal_info_view(session.info),
                    "exited": session.exited,
                    "exitCode": session.exit_code,
                }
                for session in (*self._finished.values(), *self._sessions.values())
                if session.info.thread_id == thread_id
            ]

    def _require_thread(self, thread_id: str) -> None:
        if self.sessions.get_session(thread_id) is None:
            raise ThreadNotFoundError(f"thread not found: {thread_id}")

    def read(
        self,
        thread_id: str,
        terminal_id: str,
        *,
        offset: int = 0,
        limit: int = 16 * 1024,
        through: int | None = None,
    ) -> dict[str, Any]:
        if (
            offset < 0
            or not 4 <= limit <= 64 * 1024
            or (through is not None and through < offset)
        ):
            raise InvalidArgumentError("invalid terminal output range")
        self._require_thread(thread_id)
        with self._lock:
            session = self._owned(thread_id, terminal_id, include_finished=True)
            head = session.head
            if offset > head:
                raise InvalidArgumentError("terminal cursor is ahead of its output")
            start = max(offset, session.offset)
            # If the original window was evicted during paging, report the new
            # lower bound even when it has moved beyond the old cutoff.
            end = max(start, min(head, through if through is not None else head))
            if end < head and session.output[end - session.offset] & 0xC0 == 0x80:
                raise InvalidArgumentError("terminal cutoff splits a UTF-8 character")
            relative = start - session.offset
            raw = bytes(session.output[relative : relative + min(limit, end - start)])
            if raw and raw[0] & 0xC0 == 0x80:
                raise InvalidArgumentError("terminal cursor splits a UTF-8 character")
            data = raw.decode("utf-8", errors="ignore")
            next_offset = start + len(data.encode("utf-8"))
            return {
                "terminalId": terminal_id,
                "threadId": thread_id,
                "data": data,
                "offset": start,
                "nextOffset": next_offset,
                "availableFrom": session.offset,
                "headOffset": head,
                "hasMore": next_offset < end,
                "truncated": offset < session.offset,
                "exited": session.exited,
                "exitCode": session.exit_code,
            }

    def active_for_thread(self, thread_id: str) -> bool:
        with self._lock:
            return any(
                session.info.thread_id == thread_id and session.process.poll() is None
                for session in self._sessions.values()
            )

    @property
    def active_count(self) -> int:
        """Number of terminals this application still owns."""
        with self._lock:
            return len(self._sessions)

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            session.closing = True
            self._terminate(session.process)
        for session in sessions:
            if (
                session.reader is not None
                and session.reader is not threading.current_thread()
            ):
                session.reader.join(timeout=3)
        with self._lock:
            self._finished.clear()

    def _owned(
        self, thread_id: str, terminal_id: str, *, include_finished: bool = False
    ) -> _TerminalSession:
        with self._lock:
            session = self._sessions.get(terminal_id)
            if session is None and include_finished:
                session = self._finished.get(terminal_id)
        if session is None or session.info.thread_id != thread_id:
            raise TerminalNotFoundError(f"terminal not found for Thread: {terminal_id}")
        return session

    def _read_output(self, session: _TerminalSession) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        close_deadline = None
        try:
            while True:
                if session.closing:
                    if close_deadline is None:
                        close_deadline = time.monotonic() + 0.75
                    if time.monotonic() >= close_deadline:
                        break
                try:
                    ready, _, _ = select.select([session.master_fd], [], [], 0.1)
                    if not ready:
                        if session.closing:
                            break
                        continue
                    raw = os.read(session.master_fd, 16 * 1024)
                except OSError:
                    break
                if not raw:
                    break
                text = decoder.decode(raw)
                if text:
                    self._record_output(session, text)
            trailing = decoder.decode(b"", final=True)
            if trailing:
                self._record_output(session, trailing)
        finally:
            # The reader exclusively closes the descriptor, including natural
            # exit. Producer operations use io_lock to avoid descriptor reuse.
            with session.io_lock:
                if session.master_fd is not None:
                    os.close(session.master_fd)
                    session.master_fd = None
            try:
                exit_code = session.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._terminate(session.process)
                exit_code = session.process.returncode
            with self._lock:
                session.exited = True
                session.exit_code = exit_code
                if self._sessions.get(session.info.id) is session:
                    self._sessions.pop(session.info.id, None)
                self._finished[session.info.id] = session
                while len(self._finished) > self.retained_exits:
                    self._finished.popitem(last=False)
            session.activity_lease.close()
            self._publish(
                "terminal.exit",
                {
                    "terminalId": session.info.id,
                    "threadId": session.info.thread_id,
                    "exitCode": exit_code,
                    "nextOffset": session.head,
                },
            )

    def _record_output(self, session: _TerminalSession, text: str) -> None:
        encoded = text.encode("utf-8")
        with self._lock:
            offset = session.head
            session.output.extend(encoded)
            trim = max(0, len(session.output) - self.output_capacity)
            while trim < len(session.output) and session.output[trim] & 0xC0 == 0x80:
                trim += 1
            if trim:
                del session.output[:trim]
                session.offset += trim
            next_offset = session.head
        self._publish(
            "terminal.output",
            {
                "terminalId": session.info.id,
                "threadId": session.info.thread_id,
                "data": text,
                "offset": offset,
                "nextOffset": next_offset,
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
