#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepCode Agent Thread Manager — Agent 线程派生系统
═══════════════════════════════════════════════════
移植自 CODEX.EXE (OpenAI Codex CLI v0.145.0) 的 Agent 线程架构:

  SQL 结构逆向 (来自 codex.exe PE scan):
    DELETE FROM threads WHERE id = ?
    DELETE FROM thread_dynamic_tools WHERE thread_id = ?
    DELETE FROM thread_spawn_edges WHERE parent_thread_id = ? OR child_thread_id = ?
    DELETE FROM logs WHERE thread_id = ?

核心能力:
  1. Agent 线程树 — 父 Agent 派生子 Agent，完整生命周期管理
  2. 动态工具注册 — 每个线程独立注册/卸载工具
  3. SQLite 持久化 — 线程、工具、边、日志全量落地
  4. MCP Server — 通过 MCP 协议对外暴露

用法:
  # CLI
  python agent_thread_manager.py spawn "analyze this binary" --type researcher
  python agent_thread_manager.py tree
  python agent_thread_manager.py stop <thread_id>

  # MCP Server
  python agent_thread_manager.py --mcp

  # Python 嵌入
  from agent_thread_manager import AgentThreadManager
  mgr = AgentThreadManager("F:/DEEPCODE/database.db")
  thread_id = mgr.spawn("analyze code", agent_type="researcher")
"""

import asyncio
import json
import os
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ── 数据库路径 ──────────────────────────────────────────────

DEFAULT_DB = Path(__file__).resolve().parent.parent.parent.parent / "database.db"


# ── 枚举定义 ─────────────────────────────────────────────────

class ThreadStatus(str, Enum):
    """Agent 线程状态 — 对标 codex.exe Thread 生命周期"""
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    AWAITING_APPROVAL = "awaiting_approval"
    WAITING_CHILD = "waiting_child"    # 等待子 Agent 完成
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"
    EXPIRED = "expired"


class AgentType(str, Enum):
    """Agent 类型 — 对标 codex.exe Agent 角色"""
    CODER = "coder"
    REVIEWER = "reviewer"
    TESTER = "tester"
    PLANNER = "planner"
    RESEARCHER = "researcher"
    COORDINATOR = "coordinator"
    SHELL_EXECUTOR = "shell_executor"
    FILE_EDITOR = "file_editor"
    GENERAL = "general"


class LogLevel(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


# ── 数据类 ───────────────────────────────────────────────────

@dataclass
class AgentThread:
    """Agent 线程 — 对标 codex.exe threads 表"""
    id: str = ""
    goal: str = ""
    agent_type: AgentType = AgentType.GENERAL
    status: ThreadStatus = ThreadStatus.CREATED
    context_json: str = "{}"
    parent_thread_id: Optional[str] = None
    parent_turn_id: Optional[str] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    completed_at: Optional[str] = None
    duration_ms: Optional[int] = None

    def __post_init__(self):
        if not self.id:
            self.id = f"at_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["agent_type"] = self.agent_type.value if isinstance(self.agent_type, AgentType) else self.agent_type
        d["status"] = self.status.value if isinstance(self.status, ThreadStatus) else self.status
        return d


@dataclass
class DynamicTool:
    """动态工具 — 对标 codex.exe thread_dynamic_tools 表"""
    id: str = ""
    thread_id: str = ""
    tool_name: str = ""
    tool_schema_json: str = "{}"
    registered_at: str = ""
    unregistered_at: Optional[str] = None

    def __post_init__(self):
        if not self.id:
            self.id = f"dt_{uuid.uuid4().hex[:8]}"
        if not self.registered_at:
            self.registered_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class SpawnEdge:
    """派生边 — 对标 codex.exe thread_spawn_edges 表"""
    id: str = ""
    parent_thread_id: str = ""
    child_thread_id: str = ""
    spawn_reason: str = ""
    spawned_at: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = f"se_{uuid.uuid4().hex[:8]}"
        if not self.spawned_at:
            self.spawned_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class ThreadLog:
    """线程日志 — 对标 codex.exe logs 表"""
    id: str = ""
    thread_id: str = ""
    level: LogLevel = LogLevel.INFO
    message: str = ""
    metadata_json: str = "{}"
    timestamp: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = f"tl_{uuid.uuid4().hex[:8]}"
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["level"] = self.level.value if isinstance(self.level, LogLevel) else self.level
        return d


# ── Agent Thread Manager ───────────────────────────────────

class AgentThreadManager:
    """
    Agent 线程管理器 — 对标 codex.exe 的完整线程系统

    架构:
      SQLite DB
      ├── threads              — Agent 线程主表
      ├── thread_dynamic_tools — 每线程独立工具注册
      ├── thread_spawn_edges   — 父→子派生关系
      └── thread_logs          — 线程级日志

    对标:
      codex.exe SQL:
        DELETE FROM threads WHERE id = ?
        DELETE FROM thread_dynamic_tools WHERE thread_id = ?
        DELETE FROM thread_spawn_edges WHERE parent_thread_id = ? OR child_thread_id = ?
        DELETE FROM logs WHERE thread_id = ?
    """

    SCHEMA_VERSION = 1

    def __init__(self, db_path: str = ""):
        self._db_path = str(db_path or DEFAULT_DB)
        self._conn: Optional[sqlite3.Connection] = None
        self._ensure_schema()

    # ── 数据库初始化 ──────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def _ensure_schema(self) -> None:
        db = self._connect()
        db.executescript("""
            CREATE TABLE IF NOT EXISTS agent_threads (
                id              TEXT PRIMARY KEY,
                goal            TEXT NOT NULL DEFAULT '',
                agent_type      TEXT NOT NULL DEFAULT 'general',
                status          TEXT NOT NULL DEFAULT 'created',
                context_json    TEXT NOT NULL DEFAULT '{}',
                parent_thread_id TEXT,
                parent_turn_id  TEXT,
                error           TEXT,
                metadata_json   TEXT NOT NULL DEFAULT '{}',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                completed_at    TEXT,
                duration_ms     INTEGER
            );

            CREATE TABLE IF NOT EXISTS agent_dynamic_tools (
                id              TEXT PRIMARY KEY,
                thread_id       TEXT NOT NULL REFERENCES agent_threads(id) ON DELETE CASCADE,
                tool_name       TEXT NOT NULL,
                tool_schema_json TEXT NOT NULL DEFAULT '{}',
                registered_at   TEXT NOT NULL,
                unregistered_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_dynamic_tools_thread
                ON agent_dynamic_tools(thread_id);

            CREATE TABLE IF NOT EXISTS agent_spawn_edges (
                id              TEXT PRIMARY KEY,
                parent_thread_id TEXT NOT NULL REFERENCES agent_threads(id) ON DELETE CASCADE,
                child_thread_id  TEXT NOT NULL REFERENCES agent_threads(id) ON DELETE CASCADE,
                spawn_reason    TEXT NOT NULL DEFAULT '',
                spawned_at      TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_spawn_edges_parent
                ON agent_spawn_edges(parent_thread_id);
            CREATE INDEX IF NOT EXISTS idx_spawn_edges_child
                ON agent_spawn_edges(child_thread_id);

            CREATE TABLE IF NOT EXISTS agent_thread_logs (
                id              TEXT PRIMARY KEY,
                thread_id       TEXT NOT NULL REFERENCES agent_threads(id) ON DELETE CASCADE,
                level           TEXT NOT NULL DEFAULT 'info',
                message         TEXT NOT NULL DEFAULT '',
                metadata_json   TEXT NOT NULL DEFAULT '{}',
                timestamp       TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_thread_logs_thread
                ON agent_thread_logs(thread_id);
            CREATE INDEX IF NOT EXISTS idx_thread_logs_time
                ON agent_thread_logs(thread_id, timestamp);

            CREATE TABLE IF NOT EXISTS agent_schema_version (
                version INTEGER PRIMARY KEY
            );
        """)
        # 记录 schema 版本
        db.execute(
            "INSERT OR IGNORE INTO agent_schema_version (version) VALUES (?)",
            (self.SCHEMA_VERSION,)
        )
        db.commit()

    # ── 线程 CRUD ─────────────────────────────────────────

    def spawn(
        self,
        goal: str,
        agent_type: AgentType = AgentType.GENERAL,
        parent_thread_id: Optional[str] = None,
        parent_turn_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentThread:
        """派生新 Agent 线程 — 对标 codex.exe SubagentStart"""
        thread = AgentThread(
            goal=goal,
            agent_type=agent_type,
            context_json=json.dumps(context or {}, ensure_ascii=False),
            parent_thread_id=parent_thread_id,
            parent_turn_id=parent_turn_id,
            metadata=metadata or {},
        )
        db = self._connect()
        db.execute(
            """INSERT INTO agent_threads
               (id, goal, agent_type, status, context_json,
                parent_thread_id, parent_turn_id, metadata_json,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                thread.id, thread.goal, thread.agent_type.value,
                thread.status.value, thread.context_json,
                thread.parent_thread_id, thread.parent_turn_id,
                json.dumps(thread.metadata, ensure_ascii=False),
                thread.created_at, thread.updated_at,
            ),
        )

        # 记录派生边
        if parent_thread_id:
            edge = SpawnEdge(
                parent_thread_id=parent_thread_id,
                child_thread_id=thread.id,
                spawn_reason=goal,
            )
            db.execute(
                """INSERT INTO agent_spawn_edges
                   (id, parent_thread_id, child_thread_id, spawn_reason, spawned_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (edge.id, edge.parent_thread_id, edge.child_thread_id,
                 edge.spawn_reason, edge.spawned_at),
            )

        db.commit()
        self._log(thread.id, LogLevel.INFO, f"Agent spawned: {goal[:100]}",
                  {"agent_type": agent_type.value, "parent": parent_thread_id})
        return thread

    def get(self, thread_id: str) -> Optional[AgentThread]:
        """获取线程"""
        db = self._connect()
        row = db.execute(
            "SELECT * FROM agent_threads WHERE id = ?", (thread_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_thread(row)

    def list_threads(
        self,
        status: Optional[ThreadStatus] = None,
        agent_type: Optional[AgentType] = None,
        parent_thread_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[AgentThread]:
        """列出线程"""
        db = self._connect()
        conds = []
        params: List[Any] = []
        if status:
            conds.append("status = ?")
            params.append(status.value)
        if agent_type:
            conds.append("agent_type = ?")
            params.append(agent_type.value)
        if parent_thread_id is not None:
            conds.append("parent_thread_id = ?")
            params.append(parent_thread_id)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        rows = db.execute(
            f"SELECT * FROM agent_threads {where} "
            f"ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [self._row_to_thread(r) for r in rows]

    def update_status(
        self,
        thread_id: str,
        status: ThreadStatus,
        error: Optional[str] = None,
    ) -> bool:
        """更新线程状态 — 对标 codex.exe SubagentStop / SessionEnd"""
        db = self._connect()
        now = datetime.now(timezone.utc).isoformat()
        is_terminal = status in (
            ThreadStatus.COMPLETED, ThreadStatus.FAILED,
            ThreadStatus.STOPPED, ThreadStatus.EXPIRED,
        )

        thread = self.get(thread_id)
        if thread is None:
            return False

        duration_ms = None
        if is_terminal and thread.created_at:
            try:
                created = datetime.fromisoformat(thread.created_at)
                ended = datetime.fromisoformat(now)
                duration_ms = int((ended - created).total_seconds() * 1000)
            except Exception:
                pass

        db.execute(
            """UPDATE agent_threads
               SET status = ?, updated_at = ?,
                   completed_at = CASE WHEN ? THEN ? ELSE completed_at END,
                   error = ?, duration_ms = ?
               WHERE id = ?""",
            (
                status.value, now,
                is_terminal, now if is_terminal else None,
                error, duration_ms,
                thread_id,
            ),
        )
        db.commit()
        self._log(thread_id, LogLevel.INFO if not error else LogLevel.ERROR,
                  f"Status → {status.value}" + (f": {error}" if error else ""),
                  {"new_status": status.value})
        return True

    def stop_thread_cascade(self, thread_id: str) -> int:
        """级联停止线程及其所有子线程"""
        db = self._connect()
        children = self.get_child_threads(thread_id)
        stopped = 0
        for child in children:
            stopped += self.stop_thread_cascade(child.id)

        self.update_status(thread_id, ThreadStatus.STOPPED)
        stopped += 1
        return stopped

    def delete_thread_cascade(self, thread_id: str) -> int:
        """级联删除线程及其所有子线程、动态工具、日志 — 对标 codex.exe DELETE CASCADE"""
        db = self._connect()
        children = self.get_child_threads(thread_id)
        deleted = 0
        for child in children:
            deleted += self.delete_thread_cascade(child.id)

        # 删除相关数据 (FK CASCADE 会自动清理, 这里显式做)
        db.execute("DELETE FROM agent_dynamic_tools WHERE thread_id = ?", (thread_id,))
        db.execute("DELETE FROM agent_spawn_edges WHERE child_thread_id = ?", (thread_id,))
        db.execute("DELETE FROM agent_spawn_edges WHERE parent_thread_id = ?", (thread_id,))
        db.execute("DELETE FROM agent_thread_logs WHERE thread_id = ?", (thread_id,))
        db.execute("DELETE FROM agent_threads WHERE id = ?", (thread_id,))
        db.commit()
        deleted += 1
        return deleted

    # ── 派生树 ────────────────────────────────────────────

    def get_child_threads(self, thread_id: str) -> List[AgentThread]:
        """获取直接子线程"""
        db = self._connect()
        rows = db.execute(
            """SELECT t.* FROM agent_threads t
               INNER JOIN agent_spawn_edges e ON t.id = e.child_thread_id
               WHERE e.parent_thread_id = ?
               ORDER BY e.spawned_at ASC""",
            (thread_id,),
        ).fetchall()
        return [self._row_to_thread(r) for r in rows]

    def get_parent_thread(self, thread_id: str) -> Optional[AgentThread]:
        """获取父线程"""
        db = self._connect()
        row = db.execute(
            """SELECT t.* FROM agent_threads t
               INNER JOIN agent_spawn_edges e ON t.id = e.parent_thread_id
               WHERE e.child_thread_id = ?""",
            (thread_id,),
        ).fetchone()
        return self._row_to_thread(row) if row else None

    def get_full_tree(self, root_thread_id: str) -> Dict[str, Any]:
        """获取完整派生树"""
        root = self.get(root_thread_id)
        if root is None:
            return {}

        def _build_node(tid: str) -> Dict[str, Any]:
            t = self.get(tid)
            if t is None:
                return {"id": tid, "error": "not found"}
            children = self.get_child_threads(tid)
            return {
                **t.to_dict(),
                "children": [_build_node(c.id) for c in children],
            }

        return _build_node(root_thread_id)

    def get_all_roots(self) -> List[AgentThread]:
        """获取所有根线程 (没有父线程的)"""
        db = self._connect()
        rows = db.execute(
            """SELECT * FROM agent_threads
               WHERE parent_thread_id IS NULL
               ORDER BY created_at DESC""",
        ).fetchall()
        return [self._row_to_thread(r) for r in rows]

    def get_full_forest(self) -> List[Dict[str, Any]]:
        """获取所有根线程的完整树"""
        return [self.get_full_tree(r.id) for r in self.get_all_roots()]

    # ── 动态工具管理 ──────────────────────────────────────

    def register_tool(
        self, thread_id: str, tool_name: str, tool_schema: Dict[str, Any]
    ) -> Optional[DynamicTool]:
        """注册动态工具 — 对标 codex.exe thread_dynamic_tools INSERT"""
        thread = self.get(thread_id)
        if thread is None:
            return None

        dt = DynamicTool(
            thread_id=thread_id,
            tool_name=tool_name,
            tool_schema_json=json.dumps(tool_schema, ensure_ascii=False),
        )
        db = self._connect()
        db.execute(
            """INSERT INTO agent_dynamic_tools
               (id, thread_id, tool_name, tool_schema_json, registered_at)
               VALUES (?, ?, ?, ?, ?)""",
            (dt.id, dt.thread_id, dt.tool_name, dt.tool_schema_json, dt.registered_at),
        )
        db.commit()
        self._log(thread_id, LogLevel.INFO,
                  f"Dynamic tool registered: {tool_name}",
                  {"tool_name": tool_name})
        return dt

    def unregister_tool(self, thread_id: str, tool_name: str) -> bool:
        """卸载动态工具"""
        db = self._connect()
        db.execute(
            """UPDATE agent_dynamic_tools
               SET unregistered_at = ?
               WHERE thread_id = ? AND tool_name = ? AND unregistered_at IS NULL""",
            (datetime.now(timezone.utc).isoformat(), thread_id, tool_name),
        )
        db.commit()
        self._log(thread_id, LogLevel.INFO, f"Dynamic tool unregistered: {tool_name}")
        return db.total_changes > 0

    def list_tools(self, thread_id: str) -> List[DynamicTool]:
        """列出线程的所有动态工具"""
        db = self._connect()
        rows = db.execute(
            """SELECT * FROM agent_dynamic_tools
               WHERE thread_id = ? ORDER BY registered_at ASC""",
            (thread_id,),
        ).fetchall()
        return [DynamicTool(
            id=r["id"], thread_id=r["thread_id"], tool_name=r["tool_name"],
            tool_schema_json=r["tool_schema_json"],
            registered_at=r["registered_at"],
            unregistered_at=r["unregistered_at"],
        ) for r in rows]

    # ── 日志 ──────────────────────────────────────────────

    def _log(self, thread_id: str, level: LogLevel, message: str,
             metadata: Optional[Dict] = None) -> ThreadLog:
        entry = ThreadLog(
            thread_id=thread_id, level=level, message=message,
            metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
        )
        self._connect().execute(
            """INSERT INTO agent_thread_logs
               (id, thread_id, level, message, metadata_json, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (entry.id, entry.thread_id, entry.level.value,
             entry.message, entry.metadata_json, entry.timestamp),
        )
        self._connect().commit()
        return entry

    def get_logs(self, thread_id: str, limit: int = 100,
                 level: Optional[LogLevel] = None) -> List[ThreadLog]:
        """获取线程日志"""
        db = self._connect()
        if level:
            rows = db.execute(
                """SELECT * FROM agent_thread_logs
                   WHERE thread_id = ? AND level = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (thread_id, level.value, limit),
            ).fetchall()
        else:
            rows = db.execute(
                """SELECT * FROM agent_thread_logs
                   WHERE thread_id = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (thread_id, limit),
            ).fetchall()
        return [ThreadLog(
            id=r["id"], thread_id=r["thread_id"],
            level=LogLevel(r["level"]), message=r["message"],
            metadata_json=r["metadata_json"], timestamp=r["timestamp"],
        ) for r in rows]

    # ── 统计 ──────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        """获取系统统计"""
        db = self._connect()
        total = db.execute("SELECT COUNT(*) FROM agent_threads").fetchone()[0]
        by_status = {}
        for row in db.execute(
            "SELECT status, COUNT(*) FROM agent_threads GROUP BY status"
        ).fetchall():
            by_status[row[0]] = row[1]
        by_type = {}
        for row in db.execute(
            "SELECT agent_type, COUNT(*) FROM agent_threads GROUP BY agent_type"
        ).fetchall():
            by_type[row[0]] = row[1]
        total_tools = db.execute("SELECT COUNT(*) FROM agent_dynamic_tools").fetchone()[0]
        total_edges = db.execute("SELECT COUNT(*) FROM agent_spawn_edges").fetchone()[0]
        total_logs = db.execute("SELECT COUNT(*) FROM agent_thread_logs").fetchone()[0]

        return {
            "total_threads": total,
            "by_status": by_status,
            "by_type": by_type,
            "total_dynamic_tools": total_tools,
            "total_spawn_edges": total_edges,
            "total_logs": total_logs,
            "db_path": self._db_path,
        }

    # ── 辅助 ──────────────────────────────────────────────

    def _row_to_thread(self, row: sqlite3.Row) -> AgentThread:
        # Row 可能来自 JOIN 或直接查询，安全地提取字段
        def _get(key: str, default=""):
            try:
                return row[key]
            except (KeyError, IndexError):
                return default

        return AgentThread(
            id=_get("id"),
            goal=_get("goal", ""),
            agent_type=AgentType(_get("agent_type", "general")),
            status=ThreadStatus(_get("status", "created")),
            context_json=_get("context_json", "{}"),
            parent_thread_id=_get("parent_thread_id") or None,
            parent_turn_id=_get("parent_turn_id") or None,
            error=_get("error") or None,
            metadata=json.loads(_get("metadata_json", "{}")),
            created_at=_get("created_at", ""),
            updated_at=_get("updated_at", ""),
            completed_at=_get("completed_at") or None,
            duration_ms=_get("duration_ms") or None,
        )

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None


# ── MCP Server ──────────────────────────────────────────────

async def run_mcp(db_path: str = ""):
    """MCP Server 模式 — stdio JSON-RPC 2.0"""
    mgr = AgentThreadManager(db_path)

    TOOLS = {
        "agent_thread_spawn": {
            "description": "派生新 Agent 线程 — 对标 codex.exe SubagentStart。创建子 Agent 执行指定目标，自动记录派生关系。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "Agent 目标描述"},
                    "agent_type": {"type": "string", "description": "Agent 类型: coder/reviewer/tester/planner/researcher/coordinator/shell_executor/file_editor/general"},
                    "parent_thread_id": {"type": "string", "description": "父线程 ID (可选)"},
                    "context": {"type": "object", "description": "上下文 JSON"},
                },
                "required": ["goal"],
            },
        },
        "agent_thread_status": {
            "description": "查询线程状态",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string"},
                },
                "required": ["thread_id"],
            },
        },
        "agent_thread_update": {
            "description": "更新线程状态 — 对标 codex.exe SubagentStop",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string"},
                    "status": {"type": "string", "description": "created/running/paused/awaiting_approval/waiting_child/completed/failed/stopped/expired"},
                    "error": {"type": "string"},
                },
                "required": ["thread_id", "status"],
            },
        },
        "agent_thread_tree": {
            "description": "获取线程的完整派生树",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string", "description": "根线程 ID (可选，不传则返回完整森林)"},
                },
            },
        },
        "agent_thread_list": {
            "description": "列出线程 (支持按状态/类型/父线程过滤)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "agent_type": {"type": "string"},
                    "parent_thread_id": {"type": "string"},
                    "limit": {"type": "integer", "default": 50},
                },
            },
        },
        "agent_thread_stop": {
            "description": "级联停止线程及其所有子线程",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string"},
                },
                "required": ["thread_id"],
            },
        },
        "agent_thread_delete": {
            "description": "级联删除线程及其子线程、动态工具、日志",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string"},
                },
                "required": ["thread_id"],
            },
        },
        "agent_tool_register": {
            "description": "为线程注册动态工具 — 对标 codex.exe thread_dynamic_tools",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string"},
                    "tool_name": {"type": "string"},
                    "tool_schema": {"type": "object", "description": "JSON Schema 工具定义"},
                },
                "required": ["thread_id", "tool_name", "tool_schema"],
            },
        },
        "agent_tool_unregister": {
            "description": "卸载线程的动态工具",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string"},
                    "tool_name": {"type": "string"},
                },
                "required": ["thread_id", "tool_name"],
            },
        },
        "agent_tool_list": {
            "description": "列出线程的所有动态工具",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string"},
                },
                "required": ["thread_id"],
            },
        },
        "agent_thread_logs": {
            "description": "获取线程日志",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string"},
                    "level": {"type": "string", "description": "debug/info/warn/error"},
                    "limit": {"type": "integer", "default": 50},
                },
                "required": ["thread_id"],
            },
        },
        "agent_thread_stats": {
            "description": "获取 Agent 线程系统统计",
            "inputSchema": {"type": "object", "properties": {}},
        },
        "agent_thread_forest": {
            "description": "获取所有根线程及其派生树 (完整森林)",
            "inputSchema": {"type": "object", "properties": {}},
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
                    "serverInfo": {"name": "deepcode-agent-threads", "version": "1.0.0"},
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
                if name == "agent_thread_spawn":
                    at = agent_type_str = args.get("agent_type", "general")
                    try:
                        agent_type = AgentType(agent_type_str)
                    except ValueError:
                        agent_type = AgentType.GENERAL
                    thread = mgr.spawn(
                        goal=args["goal"],
                        agent_type=agent_type,
                        parent_thread_id=args.get("parent_thread_id"),
                        context=args.get("context"),
                    )
                    result = thread.to_dict()

                elif name == "agent_thread_status":
                    thread = mgr.get(args["thread_id"])
                    result = thread.to_dict() if thread else {"error": "not found"}

                elif name == "agent_thread_update":
                    status_str = args["status"]
                    try:
                        status = ThreadStatus(status_str)
                    except ValueError:
                        result = {"error": f"Invalid status: {status_str}"}
                        break
                    ok = mgr.update_status(
                        args["thread_id"], status,
                        error=args.get("error"),
                    )
                    result = {"ok": ok, "thread_id": args["thread_id"],
                              "status": status_str}

                elif name == "agent_thread_tree":
                    tid = args.get("thread_id", "")
                    if tid:
                        result = mgr.get_full_tree(tid)
                    else:
                        result = {"forest": mgr.get_full_forest()}

                elif name == "agent_thread_list":
                    status = ThreadStatus(args["status"]) if args.get("status") else None
                    at = AgentType(args["agent_type"]) if args.get("agent_type") else None
                    threads = mgr.list_threads(
                        status=status, agent_type=at,
                        parent_thread_id=args.get("parent_thread_id"),
                        limit=args.get("limit", 50),
                    )
                    result = {"threads": [t.to_dict() for t in threads]}

                elif name == "agent_thread_stop":
                    count = mgr.stop_thread_cascade(args["thread_id"])
                    result = {"stopped": count, "thread_id": args["thread_id"]}

                elif name == "agent_thread_delete":
                    count = mgr.delete_thread_cascade(args["thread_id"])
                    result = {"deleted": count, "thread_id": args["thread_id"]}

                elif name == "agent_tool_register":
                    dt = mgr.register_tool(
                        args["thread_id"], args["tool_name"], args["tool_schema"],
                    )
                    result = dt.to_dict() if dt else {"error": "thread not found"}

                elif name == "agent_tool_unregister":
                    ok = mgr.unregister_tool(args["thread_id"], args["tool_name"])
                    result = {"ok": ok}

                elif name == "agent_tool_list":
                    tools = mgr.list_tools(args["thread_id"])
                    result = {"tools": [t.to_dict() for t in tools]}

                elif name == "agent_thread_logs":
                    level = LogLevel(args["level"]) if args.get("level") else None
                    logs = mgr.get_logs(
                        args["thread_id"],
                        limit=args.get("limit", 50),
                        level=level,
                    )
                    result = {"logs": [l.to_dict() for l in logs]}

                elif name == "agent_thread_stats":
                    result = mgr.stats()

                elif name == "agent_thread_forest":
                    result = {"forest": mgr.get_full_forest()}

                else:
                    result = {"error": f"Unknown tool: {name}"}

            except Exception as e:
                result = {"error": str(e), "ok": False}

            print(json.dumps({
                "jsonrpc": "2.0", "id": rid,
                "result": {
                    "content": [{"type": "text",
                                 "text": json.dumps(result, ensure_ascii=False)}],
                },
            }), flush=True)

        # 忽略其他方法 (notifications 等)


# ── CLI 入口 ─────────────────────────────────────────────────

def _print_json(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="DeepCode Agent Thread Manager — Agent 线程派生系统")
    parser.add_argument("--mcp", action="store_true", help="MCP Server 模式")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite 数据库路径")
    sub = parser.add_subparsers(dest="command")

    # spawn
    p = sub.add_parser("spawn", help="派生新 Agent 线程")
    p.add_argument("goal", help="目标")
    p.add_argument("--type", dest="agent_type", default="general",
                   choices=[e.value for e in AgentType])
    p.add_argument("--parent", default=None, help="父线程 ID")

    # tree
    sub.add_parser("tree", help="显示完整派生树")
    sub.add_parser("forest", help="显示所有根线程")
    sub.add_parser("stats", help="显示统计")

    # status
    p = sub.add_parser("status", help="查看线程状态")
    p.add_argument("thread_id")

    # stop
    p = sub.add_parser("stop", help="级联停止线程")
    p.add_argument("thread_id")

    # update
    p = sub.add_parser("update", help="更新线程状态")
    p.add_argument("thread_id")
    p.add_argument("status", choices=[e.value for e in ThreadStatus])

    # delete
    p = sub.add_parser("delete", help="级联删除线程")
    p.add_argument("thread_id")

    # tools
    p = sub.add_parser("tools", help="列出线程的动态工具")
    p.add_argument("thread_id")

    # logs
    p = sub.add_parser("logs", help="查看线程日志")
    p.add_argument("thread_id")
    p.add_argument("--level", default=None)

    args = parser.parse_args()

    if args.mcp:
        asyncio.run(run_mcp(args.db))
        return

    mgr = AgentThreadManager(args.db)

    if args.command == "spawn":
        t = mgr.spawn(args.goal, AgentType(args.agent_type),
                      parent_thread_id=args.parent)
        _print_json(t.to_dict())

    elif args.command == "tree":
        _print_json({"forest": mgr.get_full_forest()})

    elif args.command == "forest":
        for root in mgr.get_all_roots():
            print(f"[ROOT] {root.id[:16]} [{root.status.value}] {root.goal[:80]}")
            _print_children(mgr, root.id, "  ")

    elif args.command == "stats":
        _print_json(mgr.stats())

    elif args.command == "status":
        t = mgr.get(args.thread_id)
        _print_json(t.to_dict() if t else {"error": "not found"})

    elif args.command == "stop":
        n = mgr.stop_thread_cascade(args.thread_id)
        print(f"Stopped {n} thread(s)")

    elif args.command == "update":
        mgr.update_status(args.thread_id, ThreadStatus(args.status))
        print(f"Updated {args.thread_id} → {args.status}")

    elif args.command == "delete":
        n = mgr.delete_thread_cascade(args.thread_id)
        print(f"Deleted {n} thread(s)")

    elif args.command == "tools":
        for t in mgr.list_tools(args.thread_id):
            print(f"  🔧 {t.tool_name} (registered: {t.registered_at})")

    elif args.command == "logs":
        level = LogLevel(args.level) if args.level else None
        for l in mgr.get_logs(args.thread_id, level=level):
            print(f"  [{l.level.value}] {l.timestamp} {l.message}")

    else:
        parser.print_help()

    mgr.close()


def _print_children(mgr: AgentThreadManager, tid: str, indent: str):
    for child in mgr.get_child_threads(tid):
        print(f"{indent}|- {child.id[:16]} [{child.status.value}] {child.goal[:60]}")
        _print_children(mgr, child.id, indent + "  ")


if __name__ == "__main__":
    main()
