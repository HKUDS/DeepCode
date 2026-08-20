#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepCode Hook System — 移植自 Claude Code 的 Hook 架构
══════════════════════════════════════════════════════════
通用 Hook 管理器，支持 Pre/Post 工具钩子、会话钩子、错误钩子。

对标 Claude Code hook-handler.cjs:
  - pre-bash / post-edit / pre-task / post-task
  - session-restore / session-end
  - route / stats
  - toolFailed 检测 / intelligence 反馈

用法:
  # 注册 Hook
  python hooks.py register --event beforeWrite --cmd "python validate.py {filePath}"

  # 触发 Hook
  python hooks.py trigger --event beforeWrite --ctx '{"filePath":"/path/to/file"}'

  # 列出 Hook
  python hooks.py list

  # MCP Server
  python hooks.py --mcp
"""

import asyncio
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from enum import Enum

SYSTEM = platform.system()

# ── Codex 决策模型 ──────────────────────────────────────

class HookDecision(str, Enum):
    """
    Hook 决策类型 — 移植自 codex.exe Hook 系统

    参考 codex.exe 的 DecisionWire 类型:
      - PreToolUseDecisionWire: approve / block / allow / deny
      - PermissionRequestBehaviorWire: behavior / updatedInput / updatedPermissions / interrupt
      - PreToolUsePermissionDecisionWire: additionalContext / permissionDecision
    """
    APPROVE = "approve"          # 允许继续 (PreToolUse)
    BLOCK = "block"              # 阻止执行 (PreToolUse)
    ALLOW = "allow"              # 允许 (PermissionRequest)
    DENY = "deny"                # 拒绝 (PermissionRequest)
    INTERRUPT = "interrupt"      # 中断 (PermissionRequest)
    CONTINUE = "continue"        # 继续 (PostToolUse/通用)
    STOP = "stop"                # 停止 (Stop hook)
    SUPPRESS = "suppress"        # 抑制输出

class HookBehavior(str, Enum):
    """Hook 行为 — 移植自 codex.exe PermissionRequestBehaviorWire"""
    DEFAULT = "default"
    UPDATED_INPUT = "updatedInput"
    UPDATED_PERMISSIONS = "updatedPermissions"
    INTERRUPT = "interrupt"
    ASK = "ask"

class HookWire:
    """
    Hook Wire 类型 — 移植自 codex.exe 的 XXXHookSpecificOutputWire 类型

    codex.exe 中每个事件有专属的 Wire 类型，例如:
      - PreToolUseHookSpecificOutputWire
      - PostToolUseHookSpecificOutputWire
      - PermissionRequestHookSpecificOutputWire
      - SessionStartHookSpecificOutputWire
    """

    @staticmethod
    def pre_tool_use(decision: HookDecision = HookDecision.APPROVE,
                     reason: str = "",
                     additional_context: str = "",
                     permission_decision: str = "") -> Dict:
        """PreToolUseDecisionWire — 工具执行前决策"""
        return {
            "decision": decision.value,
            "reason": reason,
            "hookSpecificOutput": {
                "additionalContext": additional_context,
                "permissionDecision": permission_decision,
            },
        }

    @staticmethod
    def permission_request(behavior: HookBehavior = HookBehavior.DEFAULT,
                           decision: HookDecision = HookDecision.ALLOW,
                           reason: str = "") -> Dict:
        """PermissionRequestBehaviorWire — 权限请求"""
        return {
            "behavior": behavior.value,
            "decision": decision.value,
            "reason": reason,
        }

    @staticmethod
    def post_tool_use(continue_flag: bool = True,
                      system_message: str = "",
                      additional_context: str = "") -> Dict:
        """PostToolUseHookSpecificOutputWire — 工具执行后"""
        return {
            "continue": continue_flag,
            "systemMessage": system_message,
            "hookSpecificOutput": {
                "additionalContext": additional_context,
            },
        }

    @staticmethod
    def session_start(system_message: str = "",
                      additional_context: str = "") -> Dict:
        """SessionStartHookSpecificOutputWire — 会话启动"""
        return {
            "systemMessage": system_message,
            "hookSpecificOutput": {
                "additionalContext": additional_context,
            },
        }

    @staticmethod
    def stop(continue_flag: bool = False,
             reason: str = "",
             suppress_output: bool = False) -> Dict:
        """StopCommandOutputWire — 停止"""
        return {
            "continue": continue_flag,
            "reason": reason,
            "suppressOutput": suppress_output,
        }


# ── 事件定义 (扩展) ──────────────────────────────────────

class HookEvent:
    """Hook 事件类型 — 对标 Claude Code 全部 hook 点"""

    # 工具执行前 (Pre-Tool)
    BEFORE_WRITE = "beforeWrite"         # 文件写入前
    BEFORE_EDIT = "beforeEdit"           # 文件编辑前
    BEFORE_COMMAND = "beforeCommand"     # 命令执行前
    BEFORE_READ = "beforeRead"           # 文件读取前
    BEFORE_SEARCH = "beforeSearch"       # 搜索前

    # 工具执行后 (Post-Tool)
    AFTER_WRITE = "afterWrite"           # 文件写入后
    AFTER_EDIT = "afterEdit"             # 文件编辑后 — 对标 post-edit
    AFTER_COMMAND = "afterCommand"       # 命令执行后
    AFTER_READ = "afterRead"             # 文件读取后
    AFTER_SEARCH = "afterSearch"         # 搜索后

    # 任务 (Task)
    PRE_TASK = "preTask"                 # 任务开始 — 对标 pre-task
    POST_TASK = "postTask"               # 任务结束 — 对标 post-task

    # 会话 (Session)
    SESSION_START = "sessionStart"       # 会话开始 — 对标 session-restore
    SESSION_END = "sessionEnd"           # 会话结束 — 对标 session-end

    # 路由 (Route)
    ROUTE = "route"                      # 路由决策前 — 对标 route

    # 错误
    ON_ERROR = "onError"                 # 发生错误

    # 系统
    STARTUP = "startup"                  # DeepCode 启动
    SHUTDOWN = "shutdown"                # DeepCode 关闭

    # 高级别抽象事件（settings.json 兼容）— 运行时由 matcher 分发到具体工具
    PRE_TOOL_USE = "PreToolUse"          # 任意工具执行前
    POST_TOOL_USE = "PostToolUse"        # 任意工具执行后
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"  # 工具执行失败
    PERMISSION_REQUEST = "PermissionRequest"      # 权限请求
    NOTIFICATION = "Notification"                # 通知
    STOP_EVENT = "Stop"                          # 中止请求
    USER_PROMPT_SUBMIT = "UserPromptSubmit"      # 用户提交提示
    USER_PROMPT_EXPANSION = "UserPromptExpansion" # 提示展开
    SUBAGENT_STOP = "SubagentStop"               # 子Agent停止
    WORKTREE_CREATE = "WorktreeCreate"           # Git Worktree 创建
    WORKTREE_REMOVE = "WorktreeRemove"           # Git Worktree 删除
    PRE_COMPACT = "PreCompact"                   # 压缩前

    @classmethod
    def all(cls) -> List[str]:
        return [v for k, v in vars(cls).items()
                if not k.startswith("_") and isinstance(v, str)]

    @classmethod
    def pre_events(cls) -> List[str]:
        """所有 Pre 事件"""
        return [e for e in cls.all() if e.startswith("before")]

    @classmethod
    def post_events(cls) -> List[str]:
        """所有 Post 事件"""
        return [e for e in cls.all() if e.startswith("after")]


# ── Hook 上下文 ───────────────────────────────────────────

@dataclass
class HookContext:
    """Hook 上下文 — 移植自 codex.exe Hook 系统

    codex.exe Wire 类型字段:
      - decision: approve/block/allow/deny (PreToolUseDecisionWire)
      - permission_decision: 权限决策 (PreToolUsePermissionDecisionWire)
      - behavior: default/updatedInput/updatedPermissions/interrupt
      - additional_context: 额外上下文 (XXXHookSpecificOutputWire)
      - suppress_output: 抑制输出 (StopCommandOutputWire)
    """
    event: str
    tool_name: str = ""
    file_path: str = ""
    file_content: str = ""
    old_string: str = ""
    new_string: str = ""
    command: str = ""
    command_stdout: str = ""
    command_stderr: str = ""
    exit_code: int = 0
    error_message: str = ""

    # Codex 决策模型字段
    decision: str = ""                # approve/block/allow/deny
    reason: str = ""                  # 决策理由
    additional_context: str = ""      # 额外上下文 (hookSpecificOutput)
    permission_decision: str = ""     # 权限决策
    behavior: str = ""                # 行为模式
    suppress_output: bool = False     # 抑制输出

    session_id: str = ""
    task_id: str = ""
    project_root: str = ""
    workspace: str = ""
    timestamp: str = ""
    agent_id: str = ""
    tool_use_id: str = ""
    hook_event_name: str = ""
    model: str = ""
    permission_mode: str = ""
    transcript_path: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_wire(self) -> Dict:
        """转换为 Wire 格式 — 移植自 codex.exe HookSpecificOutputWire"""
        wire = {
            "session_id": self.session_id,
            "turn_id": self.task_id,
            "agent_id": self.agent_id,
            "tool_name": self.tool_name,
            "tool_input": {},
            "tool_use_id": self.tool_use_id,
            "hook_event_name": self.hook_event_name or self.event,
            "model": self.model,
            "permission_mode": self.permission_mode,
            "trigger": "hook",
            "transcript_path": self.transcript_path,
        }
        if self.command:
            wire["tool_input"]["command"] = self.command
        if self.file_path:
            wire["tool_input"]["path"] = self.file_path
        return wire

    def to_env(self) -> Dict[str, str]:
        """转换为环境变量 (子进程可用)"""
        env = {
            "DEEPCODE_HOOK_EVENT": self.event,
            "DEEPCODE_TOOL_NAME": self.tool_name,
            "DEEPCODE_FILE_PATH": self.file_path,
            "DEEPCODE_COMMAND": self.command,
            "DEEPCODE_EXIT_CODE": str(self.exit_code),
            "DEEPCODE_ERROR": self.error_message,
            "DEEPCODE_SESSION_ID": self.session_id,
            "DEEPCODE_TASK_ID": self.task_id,
            "DEEPCODE_PROJECT_ROOT": self.project_root or os.getcwd(),
            "DEEPCODE_WORKSPACE": self.workspace or os.getcwd(),
            "DEEPCODE_TIMESTAMP": self.timestamp or datetime.now().isoformat(),
        }
        # 非空字段才设置 (避免污染)
        return {k: v for k, v in env.items() if v}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event": self.event,
            "tool_name": self.tool_name,
            "file_path": self.file_path,
            "command": self.command,
            "exit_code": self.exit_code,
            "error": self.error_message,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "project_root": self.project_root,
            "timestamp": self.timestamp or datetime.now().isoformat(),
            **self.extra,
        }


# ── Hook 定义 ─────────────────────────────────────────────

@dataclass
class Hook:
    """单个 Hook 定义"""
    name: str
    event: str
    handler: str                    # 脚本路径或内联命令
    type: str = "shell"             # shell / python / node
    priority: int = 0               # 越大越先执行
    timeout: int = 30               # 超时秒数
    enabled: bool = True
    description: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def expand_vars(self, text: str, ctx: HookContext) -> str:
        """展开模板变量"""
        import string
        try:
            return string.Formatter().vformat(text, (), {
                "event": ctx.event,
                "toolName": ctx.tool_name,
                "filePath": ctx.file_path,
                "command": ctx.command,
                "exitCode": str(ctx.exit_code),
                "error": ctx.error_message,
                "sessionId": ctx.session_id,
                "taskId": ctx.task_id,
                "projectRoot": ctx.project_root or os.getcwd(),
                "workspace": ctx.workspace or os.getcwd(),
                "timestamp": ctx.timestamp or datetime.now().isoformat(),
            })
        except Exception:
            return text


# ── Hook 管理器 ───────────────────────────────────────────

class HookManager:
    """
    Hook 管理器 — 对标 Claude Code hook-handler.cjs

    - 支持 Pre/Post/Error/Session 事件
    - 多脚本链式执行
    - 超时 + 异常保护
    - 变量模板展开
    - 配置持久化
    """

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or os.path.expanduser(
            "~/.deepcode/hooks_config.json"
        )
        self._hooks: List[Hook] = []
        self._load()

    def _load(self):
        """从配置加载 hooks"""
        p = Path(self.config_path)
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                self._hooks = [Hook(**h) for h in data.get("hooks", [])]
            except Exception:
                self._hooks = []

    def _save(self):
        """持久化 hooks"""
        Path(self.config_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.config_path).write_text(
            json.dumps({
                "hooks": [h.__dict__ for h in self._hooks],
                "updated_at": datetime.now().isoformat(),
            }, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def register(self, hook: Hook):
        """注册一个 Hook"""
        # 同名替换
        self._hooks = [h for h in self._hooks
                       if not (h.name == hook.name and h.event == hook.event)]
        self._hooks.append(hook)
        self._hooks.sort(key=lambda h: (-h.priority, h.name))
        self._save()

    def unregister(self, name: str, event: str = None):
        """注销一个 Hook"""
        if event:
            self._hooks = [h for h in self._hooks
                           if not (h.name == name and h.event == event)]
        else:
            self._hooks = [h for h in self._hooks if h.name != name]
        self._save()

    def get_hooks(self, event: str) -> List[Hook]:
        """获取某事件的所有 Hook (按优先级排序)"""
        return [h for h in self._hooks
                if h.event == event and h.enabled]

    def list_hooks(self, event: str = None) -> List[Dict]:
        """列出 Hook"""
        hooks = self._hooks
        if event:
            hooks = [h for h in hooks if h.event == event]
        return [
            {
                "name": h.name,
                "event": h.event,
                "type": h.type,
                "handler": h.handler,
                "priority": h.priority,
                "enabled": h.enabled,
                "timeout": h.timeout,
                "description": h.description,
            }
            for h in hooks
        ]

    def get_hooks_by_group(self) -> Dict[str, List[Dict]]:
        """按事件分组列出"""
        groups = {}
        for h in self._hooks:
            groups.setdefault(h.event, []).append({
                "name": h.name,
                "type": h.type,
                "handler": h.handler,
                "priority": h.priority,
                "enabled": h.enabled,
            })
        return groups

    def enable(self, name: str, event: str = None):
        """启用 Hook"""
        for h in self._hooks:
            if h.name == name and (not event or h.event == event):
                h.enabled = True
        self._save()

    def disable(self, name: str, event: str = None):
        """禁用 Hook"""
        for h in self._hooks:
            if h.name == name and (not event or h.event == event):
                h.enabled = False
        self._save()

    # ── Hook 执行 ──────────────────────────────────────

    async def trigger(
        self,
        event: str,
        ctx: Optional[HookContext] = None,
        timeout: int = 30,
    ) -> List[Dict]:
        """
        触发事件的所有 Hook — 对标 Claude Code hook dispatch

        Args:
            event: 事件名
            ctx: Hook 上下文
            timeout: 每个 hook 的超时

        Returns:
            [{"hook": name, "status": "ok"|"error"|"timeout", "output": str, ...}]
        """
        hooks = self.get_hooks(event)
        if not hooks:
            return []

        ctx = ctx or HookContext(event=event)
        ctx.timestamp = datetime.now().isoformat()
        results = []

        for hook in hooks:
            result = await self._run_hook(hook, ctx, timeout)
            results.append(result)

        return results

    async def _run_hook(
        self, hook: Hook, ctx: HookContext, default_timeout: int
    ) -> Dict:
        """执行单个 Hook"""
        start = time.time()
        try:
            # 展开模板变量
            handler = hook.expand_vars(hook.handler, ctx)
            actual_timeout = hook.timeout or default_timeout

            if hook.type == "shell":
                output = await self._run_shell(handler, ctx, actual_timeout)
            elif hook.type == "python":
                output = await self._run_python(handler, ctx, actual_timeout)
            elif hook.type == "node":
                output = await self._run_node(handler, ctx, actual_timeout)
            else:
                output = f"Unknown hook type: {hook.type}"

            return {
                "hook": hook.name,
                "event": hook.event,
                "status": "ok",
                "output": output[:2000] if output else "",
                "duration": round(time.time() - start, 3),
            }
        except asyncio.TimeoutError:
            return {
                "hook": hook.name,
                "event": hook.event,
                "status": "timeout",
                "output": f"Timed out after {hook.timeout or default_timeout}s",
                "duration": hook.timeout or default_timeout,
            }
        except Exception as e:
            return {
                "hook": hook.name,
                "event": hook.event,
                "status": "error",
                "output": str(e)[:500],
                "duration": round(time.time() - start, 3),
            }

    async def _run_shell(
        self, cmd: str, ctx: HookContext, timeout: int
    ) -> str:
        """执行 Shell 命令 hook"""
        env = {**os.environ, **ctx.to_env()}
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=ctx.project_root or os.getcwd(),
            ),
            timeout=timeout,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        output = (stdout or b"").decode("utf-8", errors="replace")
        if stderr:
            output += "\n[stderr] " + stderr.decode("utf-8", errors="replace")
        return output.strip()

    async def _run_python(
        self, script: str, ctx: HookContext, timeout: int
    ) -> str:
        """执行 Python 脚本 hook"""
        if os.path.isfile(script):
            cmd = [sys.executable, script]
        else:
            # 内联 Python 代码
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, encoding="utf-8"
            )
            tmp.write(
                "import os, json\n"
                f"# Hook: {ctx.event}\n"
                f"print('executing hook...')\n"
            )
            if "print" not in script:
                tmp.write(f"print({repr(script)})\n")
            else:
                tmp.write(script + "\n")
            tmp.close()
            cmd = [sys.executable, tmp.name]
            # 清理
            try:
                os.unlink(tmp.name)
            except Exception:
                pass

        env = {**os.environ, **ctx.to_env()}
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=ctx.project_root or os.getcwd(),
            ),
            timeout=timeout,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        output = (stdout or b"").decode("utf-8", errors="replace")
        if stderr:
            output += "\n[stderr] " + stderr.decode("utf-8", errors="replace")
        return output.strip()

    async def _run_node(
        self, script: str, ctx: HookContext, timeout: int
    ) -> str:
        """执行 Node.js 脚本 hook"""
        cmd = ["node", script] if os.path.isfile(script) else ["node", "-e", script]
        env = {**os.environ, **ctx.to_env()}
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            ),
            timeout=timeout,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        output = (stdout or b"").decode("utf-8", errors="replace")
        if stderr:
            output += "\n[stderr] " + stderr.decode("utf-8", errors="replace")
        return output.strip()

    # ── 与 settings.json hooks 兼容 ─────────────────────

    @classmethod
    def from_settings_dict(cls, hooks_dict: Dict[str, str]) -> "HookManager":
        """从 settings.json 的 hooks 配置创建"""
        mgr = cls()
        event_map = {
            "beforeWrite": HookEvent.BEFORE_WRITE,
            "afterWrite": HookEvent.AFTER_WRITE,
            "beforeEdit": HookEvent.BEFORE_EDIT,
            "afterEdit": HookEvent.AFTER_EDIT,
            "beforeCommand": HookEvent.BEFORE_COMMAND,
            "afterCommand": HookEvent.AFTER_COMMAND,
            "onError": HookEvent.ON_ERROR,
        }
        for key, handler in hooks_dict.items():
            event = event_map.get(key, key)
            mgr.register(Hook(
                name=f"settings_{key}",
                event=event,
                handler=handler,
                type="shell",
                priority=100,  # settings hooks 优先
            ))
        return mgr


# ── MCP Server 模式 ──────────────────────────────────────

def _mcp_send(obj):
    """JSON-RPC 单行输出（MCP 标准 stdio 传输协议要求单行 JSON）"""
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def _load_hooks_from_settings(mgr: HookManager) -> int:
    """从 settings.json 自动加载 hooks 配置。返回加载数量。"""
    candidates = [
        os.path.join(os.getcwd(), ".deepcode", "settings.json"),
        os.path.join(os.getcwd(), ".deepcode", "settings.local.json"),
        os.path.expanduser("~/.deepcode/settings.json"),
    ]
    loaded = 0
    for cfg_path in candidates:
        if not os.path.isfile(cfg_path):
            continue
        try:
            with open(cfg_path, "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
            hooks_cfg = cfg.get("hooks", {})
            if not isinstance(hooks_cfg, dict):
                continue
            for event_name, entries in hooks_cfg.items():
                if not isinstance(entries, list):
                    continue
                # 验证事件名合法
                if event_name not in HookEvent.all():
                    continue
                for i, entry in enumerate(entries):
                    if not isinstance(entry, dict):
                        continue
                    cmd = entry.get("command", entry.get("handler", ""))
                    if not cmd:
                        continue
                    hook_name = entry.get("name") or f"auto:{event_name}:{i}"
                    kind = entry.get("type", "shell")
                    if kind == "command":
                        kind = "shell"
                    mgr.register(Hook(
                        name=hook_name,
                        event=event_name,
                        handler=cmd,
                        type=kind,
                        priority=entry.get("priority", 0),
                        timeout=entry.get("timeout", 30),
                        description=entry.get("description", entry.get("matcher", "")),
                    ))
                    loaded += 1
        except Exception:
            pass
    return loaded


async def run_mcp():
    """作为 MCP Server 运行（标准 MCP 协议）"""
    mgr = HookManager()
    n_loaded = _load_hooks_from_settings(mgr)
    if n_loaded:
        # 写 stderr 避免污染 MCP stdio
        sys.stderr.write(f"[deepcode-hooks] auto-loaded {n_loaded} hooks from settings\n")
        sys.stderr.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            method = req.get("method", "")
            params = req.get("params", {})
            rid = req.get("id", "")

            # ── 标准 MCP 握手：initialize ──────────────────
            if method == "initialize":
                _mcp_send({
                    "jsonrpc": "2.0", "id": rid,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {"listChanged": True}},
                        "serverInfo": {
                            "name": "deepcode-hooks",
                            "version": "1.0.0",
                        },
                    },
                })

            # ── notifications/initialized：静默忽略 ──────
            elif method == "notifications/initialized":
                pass

            # ── tools/list ──────────────────────────────
            elif method == "tools/list":
                _mcp_send({
                    "jsonrpc": "2.0", "id": rid,
                    "result": {
                        "tools": [
                            {
                                "name": "hook_list",
                                "description": "列出所有 Hook",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "event": {"type": "string"},
                                    },
                                },
                            },
                            {
                                "name": "hook_register",
                                "description": "注册新 Hook",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "event": {"type": "string", "enum": HookEvent.all()},
                                        "handler": {"type": "string"},
                                        "type": {"type": "string", "enum": ["shell", "python", "node"]},
                                        "priority": {"type": "integer"},
                                        "timeout": {"type": "integer"},
                                        "description": {"type": "string"},
                                    },
                                    "required": ["name", "event", "handler"],
                                },
                            },
                            {
                                "name": "hook_trigger",
                                "description": "触发事件 Hook",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "event": {"type": "string"},
                                        "ctx": {"type": "object"},
                                    },
                                    "required": ["event"],
                                },
                            },
                            {
                                "name": "hook_unregister",
                                "description": "注销 Hook",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "event": {"type": "string"},
                                    },
                                    "required": ["name"],
                                },
                            },
                            {
                                "name": "hook_enable",
                                "description": "启用 Hook",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "event": {"type": "string"},
                                    },
                                    "required": ["name"],
                                },
                            },
                            {
                                "name": "hook_disable",
                                "description": "禁用 Hook",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "event": {"type": "string"},
                                    },
                                    "required": ["name"],
                                },
                            },
                        ],
                    },
                })

            # ── tools/call ─────────────────────────────
            elif method == "tools/call":
                name = params.get("name", "")
                args = params.get("arguments", {})
                result = {}

                if name == "hook_list":
                    result["hooks"] = mgr.list_hooks(args.get("event"))
                elif name == "hook_register":
                    mgr.register(Hook(
                        name=args["name"],
                        event=args["event"],
                        handler=args["handler"],
                        type=args.get("type", "shell"),
                        priority=args.get("priority", 0),
                        timeout=args.get("timeout", 30),
                        description=args.get("description", ""),
                    ))
                    result["status"] = "registered"
                elif name == "hook_trigger":
                    ctx = HookContext(
                        event=args["event"],
                        **(args.get("ctx", {}))
                    )
                    results = await mgr.trigger(args["event"], ctx)
                    result["results"] = results
                elif name == "hook_unregister":
                    mgr.unregister(args["name"], args.get("event"))
                    result["status"] = "unregistered"
                elif name == "hook_enable":
                    mgr.enable(args["name"], args.get("event"))
                    result["status"] = "enabled"
                elif name == "hook_disable":
                    mgr.disable(args["name"], args.get("event"))
                    result["status"] = "disabled"

                _mcp_send({
                    "jsonrpc": "2.0", "id": rid,
                    "result": {
                        "content": [
                            {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
                        ],
                    },
                })

        except json.JSONDecodeError:
            pass


# ── CLI 入口 ──────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="DeepCode Hook System")
    parser.add_argument("--mcp", action="store_true", help="MCP Server 模式")
    sub = parser.add_subparsers(dest="mode")

    # list
    list_p = sub.add_parser("list", help="列出 Hook")
    list_p.add_argument("--event", help="事件类型")

    # register
    reg_p = sub.add_parser("register", help="注册 Hook")
    reg_p.add_argument("--name", required=True)
    reg_p.add_argument("--event", required=True, choices=HookEvent.all())
    reg_p.add_argument("--handler", required=True)
    reg_p.add_argument("--type", default="shell", choices=["shell", "python", "node"])
    reg_p.add_argument("--priority", type=int, default=0)
    reg_p.add_argument("--timeout", type=int, default=30)
    reg_p.add_argument("--desc", default="")

    # unregister
    unreg_p = sub.add_parser("unregister", help="注销 Hook")
    unreg_p.add_argument("--name", required=True)
    unreg_p.add_argument("--event")

    # trigger
    trig_p = sub.add_parser("trigger", help="触发 Hook")
    trig_p.add_argument("--event", required=True)
    trig_p.add_argument("--ctx", default="{}",
                        help="JSON 上下文")

    # enable/disable
    en_p = sub.add_parser("enable", help="启用 Hook")
    en_p.add_argument("--name", required=True)
    en_p.add_argument("--event")

    dis_p = sub.add_parser("disable", help="禁用 Hook")
    dis_p.add_argument("--name", required=True)
    dis_p.add_argument("--event")

    args = parser.parse_args()

    if args.mcp:
        asyncio.run(run_mcp())
        return

    mgr = HookManager()

    if args.mode == "list":
        hooks = mgr.list_hooks(args.event)
        print(json.dumps(hooks, indent=2, ensure_ascii=False))
        print(f"\nTotal: {len(hooks)} hooks")

    elif args.mode == "register":
        mgr.register(Hook(
            name=args.name, event=args.event,
            handler=args.handler, type=args.type,
            priority=args.priority, timeout=args.timeout,
            description=args.desc,
        ))
        print(f"[OK] Hook '{args.name}' registered on event '{args.event}'")

    elif args.mode == "unregister":
        mgr.unregister(args.name, args.event)
        print(f"[OK] Hook '{args.name}' unregistered")

    elif args.mode == "trigger":
        ctx = HookContext(event=args.event, **json.loads(args.ctx))
        results = asyncio.run(mgr.trigger(args.event, ctx))
        print(json.dumps(results, indent=2, ensure_ascii=False))

    elif args.mode == "enable":
        mgr.enable(args.name, args.event)
        print(f"[OK] Hook '{args.name}' enabled")

    elif args.mode == "disable":
        mgr.disable(args.name, args.event)
        print(f"[OK] Hook '{args.name}' disabled")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
