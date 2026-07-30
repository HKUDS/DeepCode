#!/usr/bin/env python3
"""
MCP Connection Manager v1.0 — Claude Code 风格 MCP 连接运行时管理
==================================================================
参考: Claude Code v2.1.88 MCPConnectionManager.tsx / useManageMCPConnections.ts

核心设计:
  - 运行时连接状态监控 (health check 每30秒)
  - 自动重连 (exponential backoff)
  - 工具审批工作流 (首次使用需确认)
  - 连接池 (复用连接，避免重复建立)
  - 开关控制 (运行时启用/禁用 MCP Server)

用法:
  from mcp_manager import MCPConnectionManager
  mgr = MCPConnectionManager()
  mgr.load_config(".mcp.json")
  mgr.start_health_checks()
"""

import os
import json
import time
import asyncio
import subprocess
import requests
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class MCPServerState:
    """MCP Server 运行时状态"""
    name: str
    command: str
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    url: Optional[str] = None

    # 运行时状态
    connected: bool = False
    healthy: bool = False
    process: Any = None
    last_health_check: Optional[datetime] = None
    last_error: Optional[str] = None
    reconnect_attempts: int = 0
    max_reconnects: int = 5
    base_delay: float = 1.0
    enabled: bool = True

    # 审批状态
    approved_tools: Dict[str, bool] = field(default_factory=dict)

    # 统计
    total_calls: int = 0
    total_errors: int = 0
    connected_since: Optional[datetime] = None

    def backoff_delay(self) -> float:
        """指数退避延迟"""
        return min(self.base_delay * (2 ** self.reconnect_attempts), 60.0)


class MCPConnectionManager:
    """
    MCP 连接运行时管理器
    模拟 Claude Code 的 MCPConnectionManager + useManageMCPConnections
    """

    def __init__(self, config_path: str = None):
        self.servers: Dict[str, MCPServerState] = {}
        self.config_path = config_path or ""
        self._health_task = None
        self._running = False

    def load_config(self, config_path: str):
        """从 .mcp.json 加载 MCP 配置"""
        self.config_path = config_path
        if not os.path.exists(config_path):
            return

        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        for name, cfg in config.get("mcpServers", {}).items():
            if "url" in cfg:
                self.servers[name] = MCPServerState(
                    name=name,
                    command="",
                    url=cfg["url"],
                )
            elif "command" in cfg:
                self.servers[name] = MCPServerState(
                    name=name,
                    command=cfg["command"],
                    args=cfg.get("args", []),
                    env=cfg.get("env", {}),
                )

    def get_server(self, name: str) -> Optional[MCPServerState]:
        return self.servers.get(name)

    def list_servers(self) -> List[Dict]:
        """列出所有 MCP Server 状态"""
        return [
            {
                "name": s.name,
                "connected": s.connected,
                "healthy": s.healthy,
                "enabled": s.enabled,
                "total_calls": s.total_calls,
                "total_errors": s.total_errors,
                "reconnect_attempts": s.reconnect_attempts,
                "last_error": s.last_error,
                "last_health_check": s.last_health_check.isoformat() if s.last_health_check else None,
            }
            for s in self.servers.values()
        ]

    async def reconnect_server(self, name: str) -> Dict:
        """重连指定 MCP Server"""
        server = self.servers.get(name)
        if not server:
            return {"error": f"Server '{name}' not found"}

        server.reconnect_attempts += 1
        delay = server.backoff_delay()

        if server.reconnect_attempts > server.max_reconnects:
            return {"error": f"Max reconnect attempts ({server.max_reconnects}) exceeded"}

        await asyncio.sleep(delay)

        try:
            # 尝试健康检查
            healthy = await self._check_health(server)
            server.connected = healthy
            server.healthy = healthy
            server.last_error = None if healthy else "Health check failed"
            server.reconnect_attempts = 0 if healthy else server.reconnect_attempts

            return {
                "server": name,
                "connected": healthy,
                "attempt": server.reconnect_attempts,
                "delay_used": delay,
            }
        except Exception as e:
            server.last_error = str(e)
            return {
                "server": name,
                "connected": False,
                "error": str(e),
                "next_retry_delay": server.backoff_delay(),
            }

    def toggle_server(self, name: str) -> Dict:
        """开关 MCP Server"""
        server = self.servers.get(name)
        if not server:
            return {"error": f"Server '{name}' not found"}

        server.enabled = not server.enabled
        return {
            "server": name,
            "enabled": server.enabled,
            "action": "enabled" if server.enabled else "disabled",
        }

    def approve_tool(self, server_name: str, tool_name: str, approved: bool = True):
        """审批工具调用"""
        server = self.servers.get(server_name)
        if server:
            server.approved_tools[tool_name] = approved

    def is_tool_approved(self, server_name: str, tool_name: str) -> bool:
        """检查工具是否已审批"""
        server = self.servers.get(server_name)
        if not server:
            return False
        return server.approved_tools.get(tool_name, False)

    def needs_approval(self, server_name: str, tool_name: str) -> bool:
        """检查工具是否需要审批"""
        server = self.servers.get(server_name)
        if not server:
            return True
        return tool_name not in server.approved_tools

    async def start_health_checks(self, interval: float = 30.0):
        """启动定期健康检查"""
        self._running = True
        while self._running:
            for name, server in self.servers.items():
                if server.enabled:
                    healthy = await self._check_health(server)
                    server.healthy = healthy
                    server.last_health_check = datetime.now()
                    if not healthy and server.reconnect_attempts < server.max_reconnects:
                        await self.reconnect_server(name)
            await asyncio.sleep(interval)

    def stop_health_checks(self):
        """停止健康检查"""
        self._running = False

    async def _check_health(self, server: MCPServerState) -> bool:
        """检查单个 Server 健康状态"""
        if server.url:
            try:
                health_url = server.url.rstrip("/") + "/health"
                resp = requests.get(health_url, timeout=5)
                return resp.status_code == 200
            except Exception:
                pass

            try:
                resp = requests.get(server.url, timeout=5)
                return resp.status_code < 500
            except Exception:
                return False

        # 对于命令行类型的 MCP Server，通过检查进程状态
        if server.process:
            return server.process.poll() is None

        return False

    def record_call(self, name: str, success: bool = True):
        """记录工具调用"""
        server = self.servers.get(name)
        if server:
            server.total_calls += 1
            if not success:
                server.total_errors += 1

    def get_health_report(self) -> Dict:
        """获取健康报告"""
        total = len(self.servers)
        healthy = sum(1 for s in self.servers.values() if s.healthy)
        connected = sum(1 for s in self.servers.values() if s.connected)
        enabled = sum(1 for s in self.servers.values() if s.enabled)

        return {
            "total_servers": total,
            "enabled": enabled,
            "connected": connected,
            "healthy": healthy,
            "health_ratio": f"{healthy}/{total}",
            "servers": self.list_servers(),
        }


# ══════════════════════════════════════════════
# 便捷函数
# ══════════════════════════════════════════════

_mcp_manager: Optional[MCPConnectionManager] = None


def get_mcp_manager(config_path: str = None) -> MCPConnectionManager:
    global _mcp_manager
    if _mcp_manager is None:
        _mcp_manager = MCPConnectionManager()
        if config_path:
            _mcp_manager.load_config(config_path)
    return _mcp_manager


# 测试
if __name__ == "__main__":
    mgr = MCPConnectionManager()
    # 模拟加载配置
    mgr.servers["test_mcp"] = MCPServerState(
        name="test_mcp",
        command="python3",
        args=["-c", "print('ok')"],
    )
    mgr.servers["test_mcp"].connected = True
    mgr.servers["test_mcp"].healthy = True

    print(json.dumps(mgr.get_health_report(), indent=2, ensure_ascii=False))
