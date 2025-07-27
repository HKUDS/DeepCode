#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
超简化LLM响应日志记录器
专注于记录LLM回复的核心内容，配置简单易用
"""

import json
import os
import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, Any


class SimpleLLMLogger:
    """超简化的LLM响应日志记录器"""

    def __init__(self, config_path: str = "mcp_agent.config.yaml"):
        """
        初始化日志记录器

        Args:
            config_path: 配置文件路径
        """
        self.config = self._load_config(config_path)
        self.llm_config = self.config.get("llm_logger", {})

        # 如果禁用则直接返回
        if not self.llm_config.get("enabled", True):
            self.enabled = False
            return

        self.enabled = True
        self._setup_logger()

    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """加载配置文件"""
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception as e:
            print(f"⚠️ 配置文件加载失败: {e}，使用默认配置")
            return self._get_default_config()

    def _get_default_config(self) -> Dict[str, Any]:
        """获取默认配置"""
        return {
            "llm_logger": {
                "enabled": True,
                "output_format": "json",
                "log_level": "basic",
                "log_directory": "logs/llm_responses",
                "filename_pattern": "llm_responses_{timestamp}.jsonl",
                "include_models": ["claude-sonnet-4", "gpt-4", "o3-mini"],
                "min_response_length": 50,
            }
        }

    def _setup_logger(self):
        """设置日志记录器"""
        log_dir = self.llm_config.get("log_directory", "logs/llm_responses")

        # 创建日志目录
        Path(log_dir).mkdir(parents=True, exist_ok=True)

        # 生成日志文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename_pattern = self.llm_config.get(
            "filename_pattern", "llm_responses_{timestamp}.jsonl"
        )
        self.log_file = os.path.join(
            log_dir, filename_pattern.format(timestamp=timestamp)
        )

        print(f"📝 LLM响应日志: {self.log_file}")

    def log_response(self, content: str, model: str = "", agent: str = "", **kwargs):
        """
        记录LLM响应 - 简化版本

        Args:
            content: LLM响应内容
            model: 模型名称
            agent: Agent名称
            **kwargs: 其他可选信息
        """
        if not self.enabled:
            return

        # 检查是否应该记录
        if not self._should_log(content, model):
            return

        # 构建日志记录
        log_entry = self._build_entry(content, model, agent, kwargs)

        # 写入日志
        self._write_log(log_entry)

        # 控制台显示
        self._console_log(content, model, agent)

    def _should_log(self, content: str, model: str) -> bool:
        """检查是否应该记录"""
        # 检查长度
        min_length = self.llm_config.get("min_response_length", 50)
        if len(content) < min_length:
            return False

        # 检查模型
        include_models = self.llm_config.get("include_models", [])
        if include_models and not any(m in model for m in include_models):
            return False

        return True

    def _build_entry(self, content: str, model: str, agent: str, extra: Dict) -> Dict:
        """构建日志条目"""
        log_level = self.llm_config.get("log_level", "basic")

        if log_level == "basic":
            # 基础级别：只记录核心内容
            return {
                "timestamp": datetime.now().isoformat(),
                "content": content,
                "model": model,
            }
        else:
            # 详细级别：包含更多信息
            entry = {
                "timestamp": datetime.now().isoformat(),
                "content": content,
                "model": model,
                "agent": agent,
            }
            # 添加额外信息
            if "token_usage" in extra:
                entry["tokens"] = extra["token_usage"]
            if "session_id" in extra:
                entry["session"] = extra["session_id"]
            return entry

    def _write_log(self, entry: Dict):
        """写入日志文件"""
        output_format = self.llm_config.get("output_format", "json")

        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                if output_format == "json":
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                elif output_format == "text":
                    timestamp = entry.get("timestamp", "")
                    model = entry.get("model", "")
                    content = entry.get("content", "")
                    f.write(f"[{timestamp}] {model}: {content}\n\n")
                elif output_format == "markdown":
                    timestamp = entry.get("timestamp", "")
                    model = entry.get("model", "")
                    content = entry.get("content", "")
                    f.write(f"**{timestamp}** | {model}\n\n{content}\n\n---\n\n")
        except Exception as e:
            print(f"⚠️ 写入日志失败: {e}")

    def _console_log(self, content: str, model: str, agent: str):
        """控制台简要显示"""
        preview = content[:80] + "..." if len(content) > 80 else content
        print(f"🤖 {model} ({agent}): {preview}")


# 全局实例
_global_logger = None


def get_llm_logger() -> SimpleLLMLogger:
    """获取全局LLM日志记录器实例"""
    global _global_logger
    if _global_logger is None:
        _global_logger = SimpleLLMLogger()
    return _global_logger


def log_llm_response(content: str, model: str = "", agent: str = "", **kwargs):
    """便捷函数：记录LLM响应"""
    logger = get_llm_logger()
    logger.log_response(content, model, agent, **kwargs)


# 示例使用
if __name__ == "__main__":
    # 测试日志记录
    log_llm_response(
        content="这是一个测试的LLM响应内容，用于验证简化日志记录器的功能是否正常工作。",
        model="claude-sonnet-4-20250514",
        agent="TestAgent",
    )

    print("✅ 简化LLM日志测试完成")
