#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepCode Internal App Server — 移植自 CODEX.EXE 远程控制架构
══════════════════════════════════════════════════════════════
对标 CODEX.EXE v0.145.0:
  - CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED 环境变量
  - CreateThread + Named Pipe IPC
  - WaitForSingleObject / CloseHandle 生命周期
  - 远程命令: stop / status / ping / exec / reload

架构:
  Main Thread                    Worker Thread
  ┌──────────────┐              ┌──────────────────┐
  │ codex-main   │───CreateThread──→│ FUN_14d90f890   │
  │              │              │                  │
  │ WaitFor      │              │ Named Pipe       │
  │ SingleObject │              │ \\.\\pipe\\dc-{pid}│
  │              │              │                  │
  │ CloseHandle  │              │ Command Handler  │
  └──────────────┘              └──────────────────┘

用法:
  # 启动服务器 (阻塞当前线程)
  python app_server.py start

  # 发送远程命令
  python app_server.py ping
  python app_server.py stop
  python app_server.py status
  python app_server.py exec "ls -la"

  # 作为 MCP Server
  python app_server.py --mcp
"""

import asyncio
import ctypes
import json
import os
import signal
import socket
import struct
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# ── 常量 ──────────────────────────────────────────────────────

ENV_DISABLE_KEY = "CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED"
PIPE_NAME_TEMPLATE = r"\\.\pipe\deepcode-internal-{pid}"
DEFAULT_TIMEOUT = 30.0
MAX_MESSAGE_SIZE = 65536  # 64KB

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    try:
        import win32pipe
        import win32file
        import win32event
        import pywintypes
        HAS_PYWIN32 = True
    except ImportError:
        HAS_PYWIN32 = False
else:
    HAS_PYWIN32 = False


# ── 枚举 ──────────────────────────────────────────────────────

class ServerCommandType(str, Enum):
    PING = "ping"
    STOP = "stop"
    STATUS = "status"
    EXEC = "exec"
    RELOAD = "reload"
    SHUTDOWN = "shutdown"


class ServerStatus(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"


# ── 数据类 ────────────────────────────────────────────────────

@dataclass
class ServerState:
    """服务器状态"""
    pid: int = field(default_factory=os.getpid)
    status: ServerStatus = ServerStatus.STARTING
    started_at: str = ""
    command_count: int = 0
    last_command: str = ""
    last_command_at: str = ""

    def to_dict(self) -> Dict:
        return {
            "pid": self.pid,
            "status": self.status.value,
            "started_at": self.started_at,
            "command_count": self.command_count,
            "last_command": self.last_command,
            "last_command_at": self.last_command_at,
        }


@dataclass
class ServerCommand:
    """服务器命令"""
    id: str = ""
    command: ServerCommandType = ServerCommandType.PING
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = f"cmd_{uuid.uuid4().hex[:8]}"
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


@dataclass
class ServerResponse:
    """服务器响应"""
    command_id: str = ""
    ok: bool = True
    data: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    timestamp: str = ""
    server_pid: int = 0
    server_status: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


# ── 内部应用服务器 ────────────────────────────────────────────

class InternalAppServer:
    """
    内部应用服务器 — 对标 CODEX.EXE 的 CreateThread + Named Pipe 架构

    对标:
      - CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED → ENV_DISABLE_KEY
      - CreateThread(..., FUN_14d90f890, ...)              → _server_thread
      - WaitForSingleObject + CloseHandle                 → stop_event
      - Named Pipe: \\\\.\\pipe\\codex-internal-{pid}      → PIPE_NAME_TEMPLATE

    生命周期:
      1. 检查 ENV_DISABLE_KEY — 若设置则跳过
      2. 创建 Named Pipe 或 Unix Socket
      3. 后台线程 listen
      4. 主线程 WaitForSingleObject(stop_event)
      5. 收到 stop → CloseHandle → 退出
    """

    def __init__(self, pipe_name: str = "", command_handler: Optional[Callable] = None):
        self._pipe_name = pipe_name or PIPE_NAME_TEMPLATE.format(pid=os.getpid())
        self._command_handler = command_handler or self._default_handler
        self._state = ServerState()
        self._stop_event = threading.Event()
        self._server_thread: Optional[threading.Thread] = None
        self._running = False

    @property
    def is_disabled(self) -> bool:
        """对标 CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED"""
        return os.environ.get(ENV_DISABLE_KEY, "").strip().lower() in ("1", "true", "yes")

    @property
    def state(self) -> ServerState:
        return self._state

    # ── 服务器生命周期 ─────────────────────────────────────

    def start(self, blocking: bool = True) -> Optional[threading.Thread]:
        """
        启动内部应用服务器

        Args:
            blocking: True=阻塞当前线程, False=后台线程运行

        Returns:
            后台线程 (blocking=False 时)
        """
        if self.is_disabled:
            return None

        self._state.started_at = datetime.now().isoformat()
        self._state.status = ServerStatus.RUNNING
        self._stop_event.clear()

        if blocking:
            self._run_server()
        else:
            self._server_thread = threading.Thread(
                target=self._run_server, daemon=True, name="DeepCode-AppServer"
            )
            self._server_thread.start()
            return self._server_thread

    def stop(self):
        """停止服务器"""
        self._state.status = ServerStatus.STOPPING
        self._stop_event.set()
        # 发送一个空连接来唤醒 pipe listen
        try:
            self._send_local_command(ServerCommandType.SHUTDOWN)
        except Exception:
            pass
        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=5)
        self._state.status = ServerStatus.STOPPED

    # ── Named Pipe 实现 ────────────────────────────────────

    def _run_server(self):
        """服务器主循环 — 对标 FUN_14d90f890"""
        self._running = True

        if IS_WINDOWS:
            self._run_named_pipe_server()
        else:
            self._run_unix_socket_server()

    def _run_named_pipe_server(self):
        """Windows Named Pipe 服务器 — 对标 CODEX.EXE Named Pipe IPC"""
        if not HAS_PYWIN32:
            # Fallback: 使用 socket (比 Named Pipe 慢但跨平台)
            self._run_socket_fallback()
            return

        pipe_name = self._pipe_name
        while not self._stop_event.is_set():
            try:
                # 创建 Named Pipe
                pipe = win32pipe.CreateNamedPipe(
                    pipe_name,
                    win32pipe.PIPE_ACCESS_DUPLEX,
                    win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
                    1,  # max instances
                    MAX_MESSAGE_SIZE,
                    MAX_MESSAGE_SIZE,
                    1000,  # default timeout ms
                    None,  # security attributes
                )

                # 等待客户端连接
                result = win32pipe.ConnectNamedPipe(pipe, None)
                if result == 0 and ctypes.GetLastError() == 535:  # ERROR_PIPE_CONNECTED
                    pass

                # 读取命令
                if not self._stop_event.is_set():
                    data = self._read_pipe_message(pipe)
                    if data:
                        self._handle_message(data)

                win32pipe.DisconnectNamedPipe(pipe)
                win32file.CloseHandle(pipe)

            except Exception as e:
                if not self._stop_event.is_set():
                    time.sleep(0.1)

    def _read_pipe_message(self, pipe) -> Optional[bytes]:
        """从 Named Pipe 读取消息"""
        try:
            hr, data = win32file.ReadFile(pipe, MAX_MESSAGE_SIZE)
            return data
        except pywintypes.error as e:
            if e.winerror != 109:  # ERROR_BROKEN_PIPE
                raise
            return None

    def _run_unix_socket_server(self):
        """Unix Domain Socket 服务器"""
        sock_path = f"/tmp/deepcode-internal-{os.getpid()}.sock"
        try:
            os.unlink(sock_path)
        except OSError:
            pass

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(sock_path)
        sock.listen(1)
        sock.settimeout(1.0)  # 1秒超时用于检查 stop_event

        while not self._stop_event.is_set():
            try:
                conn, _ = sock.accept()
                data = conn.recv(MAX_MESSAGE_SIZE)
                if data:
                    self._handle_message(data)
                    response = self._state.to_dict()
                    conn.send(json.dumps(response).encode())
                conn.close()
            except socket.timeout:
                continue
            except Exception:
                if not self._stop_event.is_set():
                    time.sleep(0.1)

        sock.close()
        try:
            os.unlink(sock_path)
        except OSError:
            pass

    def _run_socket_fallback(self):
        """TCP Socket fallback (无 pywin32 时)"""
        port = 17000 + (os.getpid() % 10000)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
        sock.settimeout(1.0)

        while not self._stop_event.is_set():
            try:
                conn, _ = sock.accept()
                data = conn.recv(MAX_MESSAGE_SIZE)
                if data:
                    self._handle_message(data)
                    response = json.dumps(self._state.to_dict()).encode()
                    conn.send(response)
                conn.close()
            except socket.timeout:
                continue
            except Exception:
                if not self._stop_event.is_set():
                    time.sleep(0.1)

        sock.close()

    # ── 命令处理 ──────────────────────────────────────────

    def _handle_message(self, data: bytes):
        """处理收到的命令"""
        try:
            msg = json.loads(data.decode("utf-8"))
            cmd = ServerCommandType(
                id=msg.get("id", ""),
                command=ServerCommand(msg.get("command", "ping")),
                payload=msg.get("payload", {}),
            )

            self._state.command_count += 1
            self._state.last_command = cmd.command.value
            self._state.last_command_at = datetime.now().isoformat()

            response = self._command_handler(cmd, self._state)

        except (json.JSONDecodeError, ValueError) as e:
            response = ServerResponse(
                command_id="unknown", ok=False,
                error=f"Invalid command: {e}",
                server_pid=os.getpid(),
                server_status=self._state.status.value,
            )

    def _default_handler(self, cmd: ServerCommandType, state: ServerState) -> ServerResponse:
        """默认命令处理器"""
        if cmd.command == ServerCommandType.PING:
            return ServerResponse(
                command_id=cmd.id, ok=True,
                data={"pong": True, "uptime_sec": state.started_at},
                server_pid=state.pid, server_status=state.status.value,
            )
        elif cmd.command == ServerCommandType.STATUS:
            return ServerResponse(
                command_id=cmd.id, ok=True,
                data=state.to_dict(),
                server_pid=state.pid, server_status=state.status.value,
            )
        elif cmd.command in (ServerCommandType.STOP, ServerCommandType.SHUTDOWN):
            self._stop_event.set()
            return ServerResponse(
                command_id=cmd.id, ok=True,
                data={"stopping": True},
                server_pid=state.pid, server_status=ServerStatus.STOPPING.value,
            )
        elif cmd.command == ServerCommandType.EXEC:
            return ServerResponse(
                command_id=cmd.id, ok=False,
                error="EXEC handler not configured",
                server_pid=state.pid, server_status=state.status.value,
            )
        else:
            return ServerResponse(
                command_id=cmd.id, ok=False,
                error=f"Unknown command: {cmd.command.value}",
                server_pid=state.pid, server_status=state.status.value,
            )

    def _send_local_command(self, command: ServerCommandType):
        """向本地服务器发送命令 (客户端侧)"""
        payload = json.dumps({
            "id": command.id, "command": command.command.value,
            "payload": command.payload,
        }).encode()

        if IS_WINDOWS and HAS_PYWIN32:
            # Named Pipe 客户端
            pipe = win32file.CreateFile(
                self._pipe_name,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0, None, win32file.OPEN_EXISTING, 0, None,
            )
            win32file.WriteFile(pipe, payload)
            win32file.CloseHandle(pipe)
        elif IS_WINDOWS:
            # TCP fallback
            port = 17000 + (os.getpid() % 10000)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(("127.0.0.1", port))
            sock.send(payload)
            sock.close()
        else:
            # Unix socket
            sock_path = f"/tmp/deepcode-internal-{os.getpid()}.sock"
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(sock_path)
            sock.send(payload)
            sock.close()


# ── 客户端 ────────────────────────────────────────────────────

class AppServerClient:
    """内部应用服务器客户端 — 对标 CODEX 的远程控制客户端"""

    def __init__(self, server_pid: Optional[int] = None):
        self._server_pid = server_pid or os.getpid()

    def _send(self, command: ServerCommand) -> ServerResponse:
        pipe_name = PIPE_NAME_TEMPLATE.format(pid=self._server_pid)
        payload = json.dumps({
            "id": command.id, "command": command.command.value,
            "payload": command.payload,
        }).encode()

        data = None
        if IS_WINDOWS and HAS_PYWIN32:
            try:
                pipe = win32file.CreateFile(
                    pipe_name,
                    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    0, None, win32file.OPEN_EXISTING, 0, None,
                )
                win32file.WriteFile(pipe, payload)
                hr, data = win32file.ReadFile(pipe, MAX_MESSAGE_SIZE)
                win32file.CloseHandle(pipe)
            except pywintypes.error as e:
                return ServerResponse(
                    command_id=command.id, ok=False,
                    error=f"Pipe error: {e.strerror} (code={e.winerror})",
                )
        else:
            try:
                if IS_WINDOWS:
                    port = 17000 + (self._server_pid % 10000)
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.connect(("127.0.0.1", port))
                else:
                    sock_path = f"/tmp/deepcode-internal-{self._server_pid}.sock"
                    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    sock.connect(sock_path)
                sock.send(payload)
                data = sock.recv(MAX_MESSAGE_SIZE)
                sock.close()
            except Exception as e:
                return ServerResponse(
                    command_id=command.id, ok=False,
                    error=f"Connection error: {e}",
                )

        if data:
            try:
                resp = json.loads(data.decode("utf-8"))
                return ServerResponse(**resp)
            except Exception:
                return ServerResponse(command_id=command.id, ok=False, error="Invalid response")

        return ServerResponse(command_id=command.id, ok=False, error="No response")

    def ping(self) -> ServerResponse:
        return self._send(ServerCommand(command=ServerCommandType.PING))

    def status(self) -> ServerResponse:
        return self._send(ServerCommand(command=ServerCommandType.STATUS))

    def stop(self) -> ServerResponse:
        return self._send(ServerCommand(command=ServerCommandType.STOP))

    def exec(self, payload: Dict[str, Any]) -> ServerResponse:
        return self._send(ServerCommand(command=ServerCommandType.EXEC, payload=payload))


# ── 全局单例 ──────────────────────────────────────────────────

_server_instance: Optional[InternalAppServer] = None


def get_server() -> InternalAppServer:
    global _server_instance
    if _server_instance is None:
        _server_instance = InternalAppServer()
    return _server_instance


# ── MCP Server ─────────────────────────────────────────────────

async def run_mcp():
    """MCP Server 模式"""
    server = get_server()

    # 启动后台服务器
    if not server.is_disabled:
        server.start(blocking=False)

    TOOLS = {
        "app_server_ping": {
            "description": "Ping 内部应用服务器 — 对标 CODEX 远程控制 ping",
            "inputSchema": {"type": "object", "properties": {}},
        },
        "app_server_status": {
            "description": "获取内部应用服务器状态",
            "inputSchema": {"type": "object", "properties": {}},
        },
        "app_server_stop": {
            "description": "停止内部应用服务器",
            "inputSchema": {"type": "object", "properties": {}},
        },
        "app_server_exec": {
            "description": "通过内部应用服务器执行命令",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "args": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["command"],
            },
        },
    }

    # ── 标准 MCP JSON-RPC 2.0 stdio ──
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            err = {
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": f"Parse error: {e}"},
                "id": None,
            }
            sys.stdout.write(json.dumps(err, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue

        method = req.get("method", "")
        params = req.get("params", {})
        rid = req.get("id", "")

        # ── initialize ──
        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "deepcode-app-server", "version": "1.0.0"},
                },
            }
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue

        # ── notifications/initialized ──
        if method == "notifications/initialized":
            continue

        if method == "tools/list":
            print(json.dumps({
                "jsonrpc": "2.0", "id": rid,
                "result": {"tools": [
                    {"name": k, "description": v["description"],
                     "inputSchema": v["inputSchema"]}
                    for k, v in TOOLS.items()
                ]},
            }), flush=True)

        elif method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments", {})
            result = {}

            try:
                if name == "app_server_ping":
                    result = {"is_disabled": server.is_disabled,
                              "state": server.state.to_dict()}
                elif name == "app_server_status":
                    result = server.state.to_dict()
                elif name == "app_server_stop":
                    server.stop()
                    result = {"stopped": True}
                elif name == "app_server_exec":
                    cmd = ServerCommandType(
                        command=ServerCommandType.EXEC,
                        payload={"command": args["command"],
                                 "args": args.get("args", [])},
                    )
                    server._send_local_command(cmd)
                    result = {"sent": True, "command": args["command"]}
                else:
                    result = {"error": f"Unknown: {name}"}
            except Exception as e:
                result = {"error": str(e)}

            print(json.dumps({
                "jsonrpc": "2.0", "id": rid,
                "result": {
                    "content": [{"type": "text",
                                 "text": json.dumps(result, ensure_ascii=False)}],
                },
            }), flush=True)


# ── CLI ────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="DeepCode Internal App Server — CODEX 远程控制移植")
    parser.add_argument("--mcp", action="store_true", help="MCP Server 模式")
    parser.add_argument("--pid", type=int, default=0, help="目标服务器 PID")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("start", help="启动内部应用服务器 (阻塞)")
    sub.add_parser("ping", help="Ping 服务器")
    sub.add_parser("status", help="获取服务器状态")
    sub.add_parser("stop", help="停止服务器")
    p = sub.add_parser("exec", help="发送执行命令")
    p.add_argument("payload", nargs="+", help="命令 + 参数")

    args = parser.parse_args()

    if args.mcp:
        asyncio.run(run_mcp())
        return

    if args.command == "start":
        server = get_server()
        if server.is_disabled:
            print(f"Disabled by {ENV_DISABLE_KEY}=1")
            return
        print(f"Starting DeepCode App Server on {PIPE_NAME_TEMPLATE.format(pid=os.getpid())}")
        server.start(blocking=True)

    else:
        client = AppServerClient(server_pid=args.pid or os.getpid())

        if args.command == "ping":
            resp = client.ping()
            print(json.dumps(resp.__dict__, indent=2, default=str))
        elif args.command == "status":
            resp = client.status()
            print(json.dumps(resp.__dict__, indent=2, default=str))
        elif args.command == "stop":
            resp = client.stop()
            print(json.dumps(resp.__dict__, indent=2, default=str))
        elif args.command == "exec":
            resp = client.exec({"command": " ".join(args.payload)})
            print(json.dumps(resp.__dict__, indent=2, default=str))
        else:
            parser.print_help()


if __name__ == "__main__":
    main()
