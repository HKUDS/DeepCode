#!/usr/bin/env python3
"""
Permission Gate — Claude Code 风格权限分级
═══════════════════════════════════════════
五级权限模式, 控制工具调用的安全边界。

Mode 说明:
  default     → 每次危险操作都询问用户
  acceptEdits → 自动接受文件编辑, 其他询问
  plan        → 只读模式, 不执行任何写操作
  auto        → 自动批准白名单操作, 其他询问
  bypass      → 跳过所有权限检查 (仅限沙箱)

用法:
  from permission_gate import PermissionGate
  gate = PermissionGate("auto")
  if gate.allow("Bash", "git status"):
      ...
"""

import json
import fnmatch
from pathlib import Path
from typing import List, Optional

SETTINGS_PATH = Path(__file__).parent.parent / "settings.json"

DEFAULT_PERMISSION_CONFIG = {
    "mode": "default",
    "auto_accept": [
        "Read*",
        "Bash(git*)",
        "Bash(ls*)",
        "Bash(cat*)",
        "Bash(head*)",
        "Bash(tail*)",
        "Bash(wc*)",
        "Bash(date)",
        "Bash(node --version)",
        "Bash(python --version)",
        "Bash(python3 --version)",
        "Bash(npm --version)",
        "Bash(which*)",
        "Bash(rg*)",
        "Bash(jq*)",
        "Bash(find*)",
        "Edit*",
        "WebSearch*",
    ],
    "deny_tools": [],
    "deny_patterns": [
        "Bash(rm -rf*)",
        "Bash(sudo*)",
        "Bash(curl*| sh)",
        "Bash(wget*| sh)",
        "Bash(shutdown*)",
        "Bash(reboot*)",
        "Bash(format*)",
        "Bash(mkfs*)",
        "Bash(dd if=*)",
        "Write(/etc/*)",
        "Write(C:\\\\Windows\\*)",
        "Write(C:\\\\Program Files\\*)",
        "Bash(git push --force*)",
    ],
}


class PermissionGate:
    """权限门控 — 五级模式"""

    MODES = ("default", "acceptEdits", "plan", "auto", "bypass")

    def __init__(self, mode: Optional[str] = None):
        cfg = self._load_config()
        self.mode = mode or cfg.get("mode", DEFAULT_PERMISSION_CONFIG["mode"])
        if self.mode not in self.MODES:
            print(f"[permission_gate] Unknown mode '{self.mode}', falling back to default")
            self.mode = "default"
        self.auto_accept = cfg.get("auto_accept") or DEFAULT_PERMISSION_CONFIG["auto_accept"]
        self.deny_tools = cfg.get("deny_tools") or DEFAULT_PERMISSION_CONFIG["deny_tools"]
        self.deny_patterns = cfg.get("deny_patterns") or DEFAULT_PERMISSION_CONFIG["deny_patterns"]

    def _load_config(self) -> dict:
        if SETTINGS_PATH.exists():
            raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            return raw.get("permission", {})
        return {}

    def _match(self, tool_call: str, patterns: List[str]) -> bool:
        """通配符匹配 tool_call 是否在 patterns 中"""
        for pattern in patterns:
            if fnmatch.fnmatch(tool_call, pattern):
                return True
        return False

    def allow(self, tool: str, detail: str = "") -> bool:
        """
        检查是否允许执行某个工具调用。

        Args:
            tool: 工具名 (e.g. "Bash", "Edit", "Write")
            detail: 调用详情 (e.g. "git status", "rm -rf /")

        Returns:
            True = 允许, False = 需要询问用户
        """
        tool_call = f"{tool}({detail})" if detail else tool

        # bypass — 全部放行
        if self.mode == "bypass":
            return True

        # plan — 只读模式
        if self.mode == "plan":
            read_only = {"Read", "rg", "grep", "find", "WebSearch", "WebFetch"}
            if tool in read_only or tool.startswith("mcp__"):
                # 允许 MCP 只读操作
                return True
            return False

        # 检查黑名单 (最高优先级)
        if self._match(tool_call, self.deny_patterns):
            print(f"[permission_gate] DENIED: DENIED: {tool_call}")
            return False

        # acceptEdits — 自动接受 Edit
        if self.mode == "acceptEdits" and tool == "Edit":
            return True

        # 安全命令检查 (所有非 bypass/plan 模式都检查)
        if self._match(tool_call, self.auto_accept):
            return True

        # auto — 自动接受白名单扩展
        if self.mode == "auto" and tool in ("Read", "WebSearch"):
            return True

        # plan 和 bypass 已在前面处理, 这里处理剩下的模式
        # default/acceptEdits — 白名单以外的都询问
        return False

    def needs_prompt(self, tool: str, detail: str = "") -> bool:
        """是否需要向用户确认 (True = 需要询问)"""
        return not self.allow(tool, detail)

    @property
    def is_plan_mode(self) -> bool:
        return self.mode == "plan"

    @property
    def is_bypass(self) -> bool:
        return self.mode == "bypass"


# ── CLI 入口 ──
if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        action = sys.argv[1]
        if action == "check":
            tool = sys.argv[2] if len(sys.argv) > 2 else "Read"
            detail = sys.argv[3] if len(sys.argv) > 3 else ""
            gate = PermissionGate()
            result = gate.allow(tool, detail)
            print(json.dumps({
                "mode": gate.mode,
                "tool": tool,
                "detail": detail,
                "allowed": result,
                "needs_prompt": not result,
            }))
            sys.exit(0 if result else 1)
        elif action == "mode":
            gate = PermissionGate()
            mode = sys.argv[2] if len(sys.argv) > 2 else gate.mode
            print(f"Permission mode: {mode}")
            print(f"  acceptEdits: {mode == 'acceptEdits'}")
            print(f"  plan (read-only): {mode == 'plan'}")
            print(f"  auto: {mode == 'auto'}")
            print(f"  bypass: {mode == 'bypass'}")
    else:
        gate = PermissionGate()
        print(f"Current permission mode: {gate.mode}")
        print(f"Auto-accept patterns: {len(gate.auto_accept)}")
        print(f"Deny patterns: {len(gate.deny_patterns)}")
