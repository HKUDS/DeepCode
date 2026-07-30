#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepCode Provider Router — 多 Provider 抽象层
══════════════════════════════════════════════════
对标 CODEX.EXE aws-smithy-runtime + provider.active:
  - 5 provider types: OpenAI / Anthropic / DeepSeek / Bedrock / Vertex
  - Hot-switch without restart
  - Config: settings.json provider.providers + env vars
  - 5 MCP tools: provider_switch / provider_list / provider_status / provider_chat / provider_recommend

整合状态 2026-07-29:
  - 作为独立 MCP server 运行 (provider-router)
  - 读取 settings.json `provider.active` 决定默认 provider
  - router-mcp 负责智能路由决策，provider-router 负责多 provider 执行
"""

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError


# ── 类型定义 ──────────────────────────────────────────────────

class ProviderType(str, Enum):
    OPENAI = "openai"          # OpenAI-compatible API
    ANTHROPIC = "anthropic"    # Anthropic Claude
    DEEPSEEK = "deepseek"      # DeepSeek (OpenAI-compatible)
    BEDROCK = "bedrock"        # AWS Bedrock
    VERTEX = "vertex"          # GCP Vertex AI


class AuthType(str, Enum):
    API_KEY = "api_key"
    BEARER = "bearer"
    OAUTH = "oauth"
    AWS_IAM = "aws_iam"


@dataclass
class ProviderConfig:
    """Provider 配置 — 对标 CODEX provider.providers.<name>"""
    name: str = ""
    type: ProviderType = ProviderType.OPENAI
    api_base: str = ""
    api_key: str = ""
    api_key_env: str = ""
    model: str = ""
    max_tokens: int = 8192
    temperature: float = 0.6
    headers: Dict[str, str] = field(default_factory=dict)
    # Bedrock / Vertex specific
    region: str = ""
    project: str = ""

    def get_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env, "")
        return ""

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["type"] = self.type.value
        # 隐藏 key
        d["api_key"] = "***" if d["api_key"] else ""
        return d


@dataclass
class ProviderResponse:
    """Provider 响应"""
    ok: bool = True
    content: str = ""
    model: str = ""
    usage: Dict[str, int] = field(default_factory=dict)
    error: str = ""
    duration_ms: int = 0
    provider: str = ""


# ── Provider 实现 ─────────────────────────────────────────────

class BaseProvider:
    """Provider 基类"""

    def __init__(self, config: ProviderConfig):
        self.config = config

    @property
    def name(self) -> str:
        return self.config.name

    async def chat(self, messages: List[Dict[str, str]],
                   model: str = "", max_tokens: int = 0,
                   temperature: float = -1.0,
                   stream: bool = False) -> ProviderResponse:
        raise NotImplementedError

    def health_check(self) -> bool:
        return False


class OpenAICompatibleProvider(BaseProvider):
    """
    OpenAI-compatible API Provider
    支持: DeepSeek / OpenAI / 任何 OpenAI-compatible API
    """

    async def chat(self, messages, model="", max_tokens=0,
                   temperature=-1.0, stream=False) -> ProviderResponse:
        start = time.time()
        model = model or self.config.model
        max_tokens = max_tokens or self.config.max_tokens
        temp = temperature if temperature >= 0 else self.config.temperature

        api_key = self.config.get_api_key()
        if not api_key:
            return ProviderResponse(ok=False, error="No API key configured",
                                    provider=self.name)

        body = json.dumps({
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temp,
            "stream": stream,
        }).encode()

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            **self.config.headers,
        }

        try:
            req = Request(
                f"{self.config.api_base}/chat/completions",
                data=body, headers=headers, method="POST",
            )
            resp = urlopen(req, timeout=120)
            data = json.loads(resp.read().decode())

            return ProviderResponse(
                ok=True,
                content=data["choices"][0]["message"]["content"],
                model=data.get("model", model),
                usage={
                    "prompt": data.get("usage", {}).get("prompt_tokens", 0),
                    "completion": data.get("usage", {}).get("completion_tokens", 0),
                    "total": data.get("usage", {}).get("total_tokens", 0),
                },
                duration_ms=int((time.time() - start) * 1000),
                provider=self.name,
            )
        except URLError as e:
            return ProviderResponse(ok=False, error=str(e), provider=self.name)
        except Exception as e:
            return ProviderResponse(ok=False, error=str(e), provider=self.name)

    def health_check(self) -> bool:
        resp = asyncio.run(self.chat(
            [{"role": "user", "content": "ping"}],
            max_tokens=5,
        ))
        return resp.ok


class AnthropicProvider(BaseProvider):
    """Anthropic Claude Provider"""

    async def chat(self, messages, model="", max_tokens=0,
                   temperature=-1.0, stream=False) -> ProviderResponse:
        start = time.time()
        model = model or self.config.model or "claude-sonnet-4-20250514"
        max_tokens = max_tokens or self.config.max_tokens
        temp = temperature if temperature >= 0 else self.config.temperature

        api_key = self.config.get_api_key()
        if not api_key:
            return ProviderResponse(ok=False, error="No API key", provider=self.name)

        # 转换 messages 格式: OpenAI → Anthropic
        system = ""
        anthropic_msgs = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                anthropic_msgs.append({"role": m["role"], "content": m["content"]})

        body = json.dumps({
            "model": model,
            "messages": anthropic_msgs,
            "system": system,
            "max_tokens": max_tokens,
            "temperature": temp,
        }).encode()

        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }

        try:
            req = Request(
                f"{self.config.api_base}/messages",
                data=body, headers=headers, method="POST",
            )
            resp = urlopen(req, timeout=120)
            data = json.loads(resp.read().decode())

            return ProviderResponse(
                ok=True,
                content=data["content"][0]["text"],
                model=data.get("model", model),
                usage={
                    "prompt": data.get("usage", {}).get("input_tokens", 0),
                    "completion": data.get("usage", {}).get("output_tokens", 0),
                    "total": data.get("usage", {}).get("input_tokens", 0) +
                             data.get("usage", {}).get("output_tokens", 0),
                },
                duration_ms=int((time.time() - start) * 1000),
                provider=self.name,
            )
        except URLError as e:
            return ProviderResponse(ok=False, error=str(e), provider=self.name)
        except Exception as e:
            return ProviderResponse(ok=False, error=str(e), provider=self.name)


# ── Provider 路由器 ────────────────────────────────────────────

# Provider 工厂
PROVIDER_CLASSES = {
    ProviderType.OPENAI: OpenAICompatibleProvider,
    ProviderType.DEEPSEEK: OpenAICompatibleProvider,
    ProviderType.ANTHROPIC: AnthropicProvider,
}


class ProviderRouter:
    """
    Provider 路由器 — 对标 CODEX.EXE 的多 Provider 切换系统

    配置格式 (对标 CODEX settings.json):
      {
        "provider": {
          "active": "deepseek",
          "providers": {
            "deepseek": {
              "type": "openai",
              "api_base": "https://api.deepseek.com",
              "api_key_env": "DEEPSEEK_API_KEY",
              "model": "deepseek-chat"
            },
            "anthropic": {
              "type": "anthropic",
              "api_base": "https://api.anthropic.com",
              "api_key_env": "ANTHROPIC_API_KEY",
              "model": "claude-sonnet-4-20250514"
            }
          }
        }
      }
    """

    def __init__(self, config: Dict[str, Any] = None):
        self._providers: Dict[str, BaseProvider] = {}
        self._active: str = ""
        self._config = config or self._load_default_config()

        self._init_providers()

    @property
    def active(self) -> Optional[BaseProvider]:
        return self._providers.get(self._active)

    @property
    def active_name(self) -> str:
        return self._active

    @property
    def providers(self) -> List[str]:
        return list(self._providers.keys())

    def _init_providers(self):
        pc = self._config.get("provider", {})
        self._active = pc.get("active", "deepseek")

        for name, cfg in pc.get("providers", {}).items():
            try:
                ptype = ProviderType(cfg.get("type", "openai"))
                pconfig = ProviderConfig(
                    name=name,
                    type=ptype,
                    api_base=cfg.get("api_base", ""),
                    api_key=cfg.get("api_key", ""),
                    api_key_env=cfg.get("api_key_env", ""),
                    model=cfg.get("model", ""),
                    max_tokens=cfg.get("max_tokens", 8192),
                    temperature=cfg.get("temperature", 0.6),
                    headers=cfg.get("headers", {}),
                    region=cfg.get("region", ""),
                    project=cfg.get("project", ""),
                )

                cls = PROVIDER_CLASSES.get(ptype, OpenAICompatibleProvider)
                self._providers[name] = cls(pconfig)
            except Exception as e:
                print(f"[provider] Failed to init {name}: {e}", file=sys.stderr)

        # 默认 provider
        if not self._providers:
            self._add_builtin_providers()

    def _add_builtin_providers(self):
        """添加内置 Provider — DeepSeek 默认"""
        deepseek_config = ProviderConfig(
            name="deepseek", type=ProviderType.DEEPSEEK,
            api_base="https://api.deepseek.com",
            api_key_env="DEEPSEEK_API_KEY",
            model="deepseek-chat",
            max_tokens=8192,
        )
        self._providers["deepseek"] = OpenAICompatibleProvider(deepseek_config)
        self._active = "deepseek"

    def _load_default_config(self) -> Dict:
        """加载默认配置"""
        return {
            "provider": {
                "active": "deepseek",
                "providers": {
                    "deepseek": {
                        "type": "openai",
                        "api_base": "https://api.deepseek.com",
                        "api_key_env": "DEEPSEEK_API_KEY",
                        "model": "deepseek-chat",
                        "max_tokens": 8192,
                    },
                },
            },
        }

    def switch(self, name: str) -> bool:
        """切换活跃 Provider"""
        if name not in self._providers:
            return False
        self._active = name
        return True

    def register(self, name: str, config: ProviderConfig) -> bool:
        """注册新 Provider"""
        cls = PROVIDER_CLASSES.get(config.type, OpenAICompatibleProvider)
        self._providers[name] = cls(config)
        if not self._active:
            self._active = name
        return True

    async def chat(self, messages: List[Dict], **kwargs) -> ProviderResponse:
        """使用当前活跃 Provider 发送请求"""
        provider = self.active
        if not provider:
            return ProviderResponse(ok=False, error="No active provider")

        try:
            return await provider.chat(messages, **kwargs)
        except Exception as e:
            return ProviderResponse(ok=False, error=str(e),
                                    provider=self._active)

    def list_providers(self) -> List[Dict]:
        """列出所有 Provider"""
        result = []
        for name, p in self._providers.items():
            result.append({
                "name": name,
                "type": p.config.type.value,
                "active": name == self._active,
                "model": p.config.model,
                "health": p.health_check() if name == self._active else "unknown",
            })
        return result

    def status(self) -> Dict:
        """获取状态"""
        return {
            "active": self._active,
            "providers": self.list_providers(),
            "config": {
                k: v.to_dict() for k, v in {
                    n: p.config for n, p in self._providers.items()
                }.items()
            },
        }

    def recommend_model(self, effort: str = "high",
                        reasoning: str = "") -> Dict[str, str]:
        """
        根据 effort 级别推荐模型 — 对标 CODEX 的 reasoning_effort

        effort: low | medium | high | xhigh | max
        """
        models = {
            "low":    {"model": "deepseek-v4-flash", "max_tokens": 2048},
            "medium": {"model": "deepseek-v4-flash", "max_tokens": 4096},
            "high":   {"model": "deepseek-v4-pro",   "max_tokens": 8192},
            "xhigh":  {"model": "deepseek-r1",       "max_tokens": 16384},
            "max":    {"model": "deepseek-r1",       "max_tokens": 32768},
        }
        return models.get(effort, models["high"])


# ── 全局单例 ──────────────────────────────────────────────────

_router: Optional[ProviderRouter] = None


def get_router() -> ProviderRouter:
    global _router
    if _router is None:
        _router = ProviderRouter()
    return _router


# ── MCP Server ─────────────────────────────────────────────────

async def run_mcp():
    router = get_router()

    TOOLS = {
        "provider_switch": {
            "description": "切换到指定 Provider",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                },
                "required": ["name"],
            },
        },
        "provider_list": {
            "description": "列出所有 Provider",
            "inputSchema": {"type": "object", "properties": {}},
        },
        "provider_status": {
            "description": "Provider 状态",
            "inputSchema": {"type": "object", "properties": {}},
        },
        "provider_chat": {
            "description": "使用活跃 Provider 发送 LLM 请求",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "messages": {"type": "array"},
                    "model": {"type": "string"},
                    "max_tokens": {"type": "integer"},
                },
                "required": ["messages"],
            },
        },
        "provider_recommend": {
            "description": "根据 effort 推荐模型",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "effort": {"type": "string"},
                },
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
                    "serverInfo": {"name": "provider-router", "version": "1.0.0"},
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
                if name == "provider_switch":
                    ok = router.switch(args["name"])
                    result = {"ok": ok, "active": router.active_name}

                elif name == "provider_list":
                    result = {"providers": router.list_providers()}

                elif name == "provider_status":
                    result = router.status()

                elif name == "provider_chat":
                    resp = await router.chat(
                        args["messages"],
                        model=args.get("model", ""),
                        max_tokens=args.get("max_tokens", 0),
                    )
                    result = {
                        "ok": resp.ok, "content": resp.content[:500],
                        "usage": resp.usage, "duration_ms": resp.duration_ms,
                        "provider": resp.provider, "error": resp.error,
                    }

                elif name == "provider_recommend":
                    result = router.recommend_model(args.get("effort", "high"))

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
    parser = argparse.ArgumentParser(description="DeepCode Provider Router")
    parser.add_argument("--mcp", action="store_true", help="MCP Server 模式")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="列出 Provider")
    sub.add_parser("status", help="状态")
    p = sub.add_parser("switch", help="切换 Provider")
    p.add_argument("name", help="Provider 名称")
    p = sub.add_parser("chat", help="发送聊天请求")
    p.add_argument("prompt", help="提示词")

    args = parser.parse_args()

    if args.mcp:
        asyncio.run(run_mcp())
        return

    router = get_router()

    if args.command == "list":
        print(json.dumps(router.list_providers(), indent=2))

    elif args.command == "status":
        print(json.dumps(router.status(), indent=2, default=str))

    elif args.command == "switch":
        ok = router.switch(args.name)
        print(f"Switched to {args.name}: {ok}")

    elif args.command == "chat":
        resp = asyncio.run(router.chat([{"role": "user", "content": args.prompt}]))
        print(f"[{resp.provider}] {resp.model} ({resp.duration_ms}ms)")
        print(f"Tokens: {resp.usage}")
        print(f"\n{resp.content[:500]}")

    else:
        # Demo
        print("=== Provider Router Demo ===\n")
        print("Active:", router.active_name)
        print()
        print("Providers:")
        for p in router.list_providers():
            print(f"  {'*' if p['active'] else ' '} {p['name']} [{p['type']}] "
                  f"model={p['model']}")
        print()
        for effort in ["low", "medium", "high", "xhigh", "max"]:
            rec = router.recommend_model(effort)
            print(f"  effort={effort:6s} → {rec['model']:25s} max_tokens={rec['max_tokens']}")


if __name__ == "__main__":
    main()
