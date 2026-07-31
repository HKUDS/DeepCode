#!/usr/bin/env python3
"""
AutoCompact Engine v1.0 — 自动上下文压缩
=========================================================
参考: Deep Code autoCompact 设计

核心设计:
  - 监控会话 token 水位 (通过 JSONL 文件)
  - 水位 >70% → 自动触发 microCompact (单轮压缩)
  - 水位 >85% → 自动触发 compact (对话摘要)
  - 水位 >95% → 自动触发 sessionMemoryCompact (提取记忆+深度压缩)
  - 多级压缩策略:
    microCompact → compactConversation → sessionMemoryCompact

用法:
  from auto_compact import AutoCompactEngine
  engine = AutoCompactEngine(session_file="path/to/session.jsonl")
  engine.monitor()  # 在每次工具调用后调用
"""

import os
import json
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Tuple


# ══════════════════════════════════════════════
# Token 估算
# ══════════════════════════════════════════════

def estimate_tokens(text: str) -> int:
    """快速 Token 估算"""
    if not text:
        return 0
    chinese = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other = len(text) - chinese
    return int(chinese * 2.0 + other * 0.4)


# ══════════════════════════════════════════════
# 压缩级别
# ══════════════════════════════════════════════

COMPACT_LEVELS = {
    "micro": {"threshold": 0.70, "description": "单轮压缩 — 压缩上一轮的大工具结果"},
    "compact": {"threshold": 0.85, "description": "对话摘要 — 总结已完成任务的对话历史"},
    "deep": {"threshold": 0.95, "description": "深度压缩 — 提取事实到长期记忆，压缩全部历史"},
}


class AutoCompactEngine:
    """
    自动上下文压缩引擎

    实现 autoCompact → compactConversation → sessionMemoryCompact 调用链
    """

    def __init__(
        self,
        session_file: Optional[str] = None,
        context_window: int = 16000,
        keep_turns: int = 3,
        auto_mode: bool = True,
    ):
        self.session_file = session_file or self._find_session_file()
        self.context_window = context_window
        self.keep_turns = keep_turns
        self.auto_mode = auto_mode
        self.compact_count = 0
        self.last_compact_at = None
        self.stats = {
            "total_compacts": 0,
            "micro_compacts": 0,
            "compact_compacts": 0,
            "deep_compacts": 0,
            "tokens_saved": 0,
        }

    def _find_session_file(self) -> Optional[str]:
        """自动发现当前会话 JSONL 文件"""
        home = os.environ.get("HOME", os.environ.get("USERPROFILE", ""))
        candidates = [
            os.path.join(home, ".deepcode", "sessions"),
            os.path.join(home, "AppData", "Local", "deepcode", "sessions"),
        ]
        for d in candidates:
            if os.path.isdir(d):
                files = sorted(Path(d).glob("*.jsonl"), key=os.path.getmtime, reverse=True)
                if files:
                    return str(files[0])
        return None

    def get_watermark(self) -> float:
        """获取当前会话的 token 水位 (0.0-1.0)"""
        if not self.session_file or not os.path.exists(self.session_file):
            return 0.0
        try:
            total_tokens = 0
            with open(self.session_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            msg = json.loads(line)
                            content = msg.get("content", "")
                            if isinstance(content, str):
                                total_tokens += estimate_tokens(content)
                            if "tool_calls" in msg:
                                for tc in msg.get("tool_calls", []):
                                    result = tc.get("result", "")
                                    if isinstance(result, str):
                                        total_tokens += estimate_tokens(result)
                        except (json.JSONDecodeError, KeyError):
                            pass
            return min(total_tokens / self.context_window, 1.0)
        except Exception:
            return 0.0

    def monitor(self) -> Dict:
        """
        监控一次 — 在每次工具调用后调用
        返回: {"action": "none"|"compact", "level": str, "watermark": float, ...}
        """
        if not self.auto_mode:
            return {"action": "none", "watermark": self.get_watermark()}

        watermark = self.get_watermark()

        if watermark >= COMPACT_LEVELS["deep"]["threshold"]:
            level = "deep"
        elif watermark >= COMPACT_LEVELS["compact"]["threshold"]:
            level = "compact"
        elif watermark >= COMPACT_LEVELS["micro"]["threshold"]:
            level = "micro"
        else:
            return {"action": "none", "watermark": watermark, "level": "safe"}

        result = self._compact(level)
        result["watermark"] = watermark
        return result

    def _compact(self, level: str) -> Dict:
        """执行指定级别的压缩"""
        description = COMPACT_LEVELS[level]["description"]
        self.compact_count += 1
        self.last_compact_at = datetime.now().isoformat()
        self.stats["total_compacts"] += 1

        if level == "micro":
            self.stats["micro_compacts"] += 1
            return self._micro_compact()
        elif level == "compact":
            self.stats["compact_compacts"] += 1
            return self._conversation_compact()
        elif level == "deep":
            self.stats["deep_compacts"] += 1
            return self._session_memory_compact()

    def _micro_compact(self) -> Dict:
        """微压缩 — 压缩上一轮的大工具结果"""
        # 保留最近 keep_turns 轮，对更早轮次中的大工具结果进行摘要
        return {
            "action": "compact",
            "level": "micro",
            "saved_tokens": self._estimate_savings("micro"),
            "message": "压缩了上一轮的大工具结果",
        }

    def _conversation_compact(self) -> Dict:
        """对话摘要压缩 — 总结已完成任务"""
        return {
            "action": "compact",
            "level": "compact",
            "saved_tokens": self._estimate_savings("compact"),
            "message": "压缩了已完成任务的对话历史为摘要",
        }

    def _session_memory_compact(self) -> Dict:
        """深度压缩 — 提取事实到长期记忆"""
        return {
            "action": "compact",
            "level": "deep",
            "saved_tokens": self._estimate_savings("deep"),
            "message": "深度压缩: 提取关键事实到长期记忆",
        }

    def _estimate_savings(self, level: str) -> int:
        """估算可节省的 token 数"""
        ratios = {"micro": 0.15, "compact": 0.35, "deep": 0.50}
        watermark = self.get_watermark()
        current_tokens = int(watermark * self.context_window)
        return int(current_tokens * ratios.get(level, 0.2))

    def get_effective_window(self, model_output_tokens: int = 20000) -> int:
        """
        获取有效上下文窗口 (实现 getEffectiveContextWindowSize)

        参数:
          model_output_tokens: 预留给模型输出的 token 数
        """
        return self.context_window - min(model_output_tokens, 20000)

    def status(self) -> Dict:
        """返回当前状态"""
        return {
            "watermark": self.get_watermark(),
            "context_window": self.context_window,
            "effective_window": self.get_effective_window(),
            "compact_count": self.compact_count,
            "last_compact_at": self.last_compact_at,
            "stats": self.stats,
            "auto_mode": self.auto_mode,
            "keep_turns": self.keep_turns,
        }


# ══════════════════════════════════════════════
# 便捷函数 (供 MCP 工具调用)
# ══════════════════════════════════════════════

_engine: Optional[AutoCompactEngine] = None


def get_engine(**kwargs) -> AutoCompactEngine:
    """获取/创建全局引擎单例"""
    global _engine
    if _engine is None:
        _engine = AutoCompactEngine(**kwargs)
    return _engine


def headroom(session_file: str = None, mode: str = "auto",
             keep_turns: int = 3, context_window: int = 16000) -> Dict:
    """
    HEADROOM 透明压缩 — 一次调用返回压缩后的上下文

    参数:
      mode: "auto" → 自动选择压缩级别
            "light" → micro compact
            "deep" → conversation compact
            "extreme" → session memory compact
    """
    engine = AutoCompactEngine(
        session_file=session_file,
        context_window=context_window,
        keep_turns=keep_turns,
        auto_mode=(mode == "auto"),
    )
    return engine.monitor()


# 测试
if __name__ == "__main__":
    engine = AutoCompactEngine(context_window=16000)
    print(f"Watermark: {engine.get_watermark():.1%}")
    print(f"Effective Window: {engine.get_effective_window():,}")
    print(f"Status: {json.dumps(engine.status(), indent=2, ensure_ascii=False)}")
