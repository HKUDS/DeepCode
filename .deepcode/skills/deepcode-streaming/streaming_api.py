#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepCode Streaming API — Claude Code v2.1.216 Streaming API 移植
══════════════════════════════════════════════════════════════════
Streaming (流式) API 模块，支持 SSE / Chunked / WebSocket 流。

移植自 Claude Code streaming 支持:
  - _calculateNonstreamingTimeout / calculateNonstreamingTimeout
  - buildRequest / buildHeaders / buildBody (流式版本)
  - shouldRetry / retryRequest (流式重试)
  - SSE 事件解析 + Chunked 传输

用法:
  # CLI 流式调用 DeepSeek
  python streaming_api.py chat --prompt "你好" --stream

  # MCP Server 模式
  python streaming_api.py --mcp

  # Python 嵌入
  from streaming_api import StreamingClient, StreamEvent
  client = StreamingClient(api_key="sk-xxx")
  async for chunk in client.chat_stream([{"role":"user","content":"hello"}]):
      print(chunk.content, end="")
"""

import asyncio
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import AsyncIterator, Dict, List, Optional, Callable, Any, Union

# ── 流事件类型 ────────────────────────────────────────────

class StreamEventType(Enum):
    """流事件类型"""
    CHUNK = "chunk"            # 普通内容块
    DONE = "done"              # 完成
    ERROR = "error"            # 错误
    THROTTLE = "throttle"      # 限流 (背压)
    METADATA = "metadata"      # 元数据 (token用量等)


@dataclass
class StreamEvent:
    """流事件 — 对标 Claude Code stream chunk"""
    type: StreamEventType
    content: Optional[str] = None
    delta: Optional[str] = None        # 增量内容 (SSE)
    finish_reason: Optional[str] = None
    usage: Optional[Dict] = None       # token 用量
    error: Optional[str] = None
    metadata: Optional[Dict] = None
    timestamp: float = field(default_factory=time.time)

    @classmethod
    def chunk(cls, content: str, delta: str = None):
        return cls(type=StreamEventType.CHUNK, content=content, delta=delta)

    @classmethod
    def done(cls, reason: str = "stop", usage: Dict = None):
        return cls(type=StreamEventType.DONE, finish_reason=reason, usage=usage)

    @classmethod
    def error(cls, msg: str):
        return cls(type=StreamEventType.ERROR, error=msg)


# ── 流式客户端 —────────────────────────────────────────────

class StreamingClient:
    """
    流式 API 客户端 — 对标 Claude Code streaming request 栈

    特性:
      - SSE (Server-Sent Events) 解析
      - Chunked transfer 支持
      - 超时管理 (streaming vs non-streaming)
      - 自动重试 (shouldRetry)
      - 背压控制 (throttle)
      - Token 用量跟踪
    """

    def __init__(
        self,
        api_key: str = None,
        base_url: str = "https://api.deepseek.com",
        timeout: int = 60,
        stream_timeout: int = 300,
        max_retries: int = 3,
    ):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        # 超时: 对标 Claude Code calculateNonstreamingTimeout / _calculateNonstreamingTimeout
        self.timeout = timeout              # 非流式超时 (short)
        self.stream_timeout = stream_timeout  # 流式超时 (long)
        self.max_retries = max_retries
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0

    def _build_headers(self, stream: bool = True) -> Dict[str, str]:
        """构建请求头 — 对标 Claude Code buildHeaders"""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "User-Agent": "DeepCode-Streaming/1.0",
        }

    def _build_body(
        self,
        messages: List[Dict],
        model: str = "deepseek-v4-flash",
        stream: bool = True,
        temperature: float = 0.6,
        max_tokens: int = 4096,
        **kwargs,
    ) -> bytes:
        """构建请求体 — 对标 Claude Code buildBody"""
        body = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        body.update(kwargs)
        return json.dumps(body).encode("utf-8")

    @staticmethod
    def _calculate_timeout(streaming: bool, non_streaming_val: int, streaming_val: int) -> int:
        """计算超时 — 对标 Claude Code _calculateNonstreamingTimeout"""
        return streaming_val if streaming else non_streaming_val

    # ── SSE 解析 ───────────────────────────────────────

    @staticmethod
    def _parse_sse_line(line: str) -> Optional[Dict]:
        """解析 SSE 行 — 对标 Claude Code SSE parser"""
        line = line.strip()
        if not line or line.startswith(":"):
            return None  # 注释 / 空行
        if line.startswith("data: "):
            data = line[6:]
            if data == "[DONE]":
                return {"type": "done"}
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                return {"type": "data", "raw": data}
        return None

    @staticmethod
    def _extract_delta(sse_data: Dict) -> Optional[str]:
        """从 SSE 数据中提取增量内容 — 对标 Claude Code delta extraction"""
        choices = sse_data.get("choices", [])
        if not choices:
            return None
        delta = choices[0].get("delta", {})
        return delta.get("content", "")

    @staticmethod
    def _extract_finish_reason(sse_data: Dict) -> Optional[str]:
        choices = sse_data.get("choices", [])
        if choices:
            return choices[0].get("finish_reason")
        return None

    @staticmethod
    def _extract_usage(sse_data: Dict) -> Optional[Dict]:
        return sse_data.get("usage")

    # ── 核心流式请求 ────────────────────────────────────

    async def chat_stream(
        self,
        messages: List[Dict],
        model: str = "deepseek-v4-flash",
        temperature: float = 0.6,
        max_tokens: int = 4096,
        **kwargs,
    ) -> AsyncIterator[StreamEvent]:
        """
        流式 Chat 补全 — 对标 Claude Code streaming makeRequest

        Args:
            messages: [{"role":"user","content":"..."}]
            model: 模型名
            temperature: 温度
            max_tokens: 最大输出 token

        Yields:
            StreamEvent: 流事件 (chunk / done / error)
        """
        url = f"{self.base_url}/v1/chat/completions"
        body = self._build_body(messages, model, True, temperature, max_tokens, **kwargs)
        headers = self._build_headers(stream=True)
        actual_timeout = self._calculate_timeout(
            True, self.timeout, self.stream_timeout
        )

        retries = 0
        last_error = None

        while retries <= self.max_retries:
            try:
                reader, writer = await asyncio.wait_for(
                    self._connect(url, headers, body),
                    timeout=self.timeout,
                )
                buffer = ""
                async for line in self._read_lines(reader, writer, actual_timeout):
                    buffer += line
                    if buffer.endswith("\n\n"):
                        for sse_line in buffer.strip().split("\n"):
                            parsed = self._parse_sse_line(sse_line)
                            if parsed is None:
                                continue
                            if parsed.get("type") == "done":
                                yield StreamEvent.done()
                                writer.close()
                                return
                            delta = self._extract_delta(parsed)
                            finish = self._extract_finish_reason(parsed)
                            usage = self._extract_usage(parsed)
                            if delta:
                                yield StreamEvent.chunk(content=delta, delta=delta)
                            if finish:
                                yield StreamEvent.done(reason=finish, usage=usage)
                                writer.close()
                                return
                            if usage:
                                self._total_prompt_tokens += usage.get("prompt_tokens", 0)
                                self._total_completion_tokens += usage.get("completion_tokens", 0)
                        buffer = ""

                # 读完缓冲区
                if buffer.strip():
                    for sse_line in buffer.strip().split("\n"):
                        parsed = self._parse_sse_line(sse_line)
                        if parsed and parsed.get("type") == "done":
                            yield StreamEvent.done()
                writer.close()
                return

            except asyncio.TimeoutError:
                last_error = "timeout"
                retries += 1
                if retries > self.max_retries:
                    yield StreamEvent.error(f"Stream timed out after {actual_timeout}s")
                    return
                # 指数退避 — 对标 Claude Code shouldRetry
                wait = min(2 ** retries, 30)
                yield StreamEvent(
                    type=StreamEventType.THROTTLE,
                    content=f"retry in {wait}s (attempt {retries}/{self.max_retries})",
                )
                await asyncio.sleep(wait)

            except Exception as e:
                last_error = str(e)
                retries += 1
                if retries > self.max_retries:
                    yield StreamEvent.error(f"Stream error after {retries} retries: {e}")
                    return
                wait = min(2 ** retries, 30)
                await asyncio.sleep(wait)

    async def _connect(self, url: str, headers: Dict, body: bytes):
        """建立 HTTP 连接"""
        import http.client
        parsed = url.replace("https://", "").replace("http://", "")
        use_ssl = url.startswith("https")
        host = parsed.split("/")[0]
        path = "/" + "/".join(parsed.split("/")[1:])

        conn = http.client.HTTPSConnection(host, timeout=self.timeout) if use_ssl \
            else http.client.HTTPConnection(host, timeout=self.timeout)
        conn.request("POST", path, body=body, headers=headers)
        resp = conn.getresponse()
        return resp, conn

    async def _read_lines(self, reader, writer, timeout):
        """逐行读取流式响应"""
        import select
        import socket

        async def read_one_line():
            loop = asyncio.get_event_loop()
            return await asyncio.wait_for(
                loop.run_in_executor(None, reader.readline),
                timeout=timeout,
            )

        while True:
            try:
                line = await read_one_line()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace")
                yield decoded
            except asyncio.TimeoutError:
                raise
            except Exception:
                break

    # ── 非流式请求 ──────────────────────────────────────

    async def chat(
        self,
        messages: List[Dict],
        model: str = "deepseek-v4-flash",
        temperature: float = 0.6,
        max_tokens: int = 4096,
        **kwargs,
    ) -> Dict:
        """
        非流式 Chat 补全 — 对标 Claude Code non-streaming makeRequest

        使用较短的超时 (calculateNonstreamingTimeout).
        """
        url = f"{self.base_url}/v1/chat/completions"
        body = self._build_body(messages, model, False, temperature, max_tokens, **kwargs)
        headers = self._build_headers(stream=False)
        actual_timeout = self._calculate_timeout(False, self.timeout, self.stream_timeout)

        import urllib.request
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=actual_timeout) as resp:
                data = json.loads(resp.read().decode())
                self._total_prompt_tokens += data.get("usage", {}).get("prompt_tokens", 0)
                self._total_completion_tokens += data.get("usage", {}).get("completion_tokens", 0)
                return data
        except Exception as e:
            return {"error": str(e)}

    # ── 工具函数 ────────────────────────────────────────

    def get_usage_stats(self) -> Dict:
        """获取累计 token 用量统计"""
        return {
            "total_prompt_tokens": self._total_prompt_tokens,
            "total_completion_tokens": self._total_completion_tokens,
            "total_tokens": self._total_prompt_tokens + self._total_completion_tokens,
        }

    def reset_usage(self):
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0


# ── 辅助: 组装流为完整响应 ──────────────────────────────

async def stream_to_completion(stream: AsyncIterator[StreamEvent]) -> str:
    """将流式事件组装为完整字符串"""
    result = []
    async for event in stream:
        if event.type == StreamEventType.CHUNK and event.content:
            result.append(event.content)
        elif event.type == StreamEventType.ERROR:
            return f"Error: {event.error}"
    return "".join(result)


# ── CLI 入口 ──────────────────────────────────────────────

async def main_cli():
    import argparse
    parser = argparse.ArgumentParser(description="DeepCode Streaming API")
    parser.add_argument("--mcp", action="store_true", help="MCP Server 模式")
    sub = parser.add_subparsers(dest="mode")

    chat_parser = sub.add_parser("chat", help="Chat 补全")
    chat_parser.add_argument("--prompt", required=True, help="用户输入")
    chat_parser.add_argument("--model", default="deepseek-v4-flash")
    chat_parser.add_argument("--stream", action="store_true", help="启用流式输出")
    chat_parser.add_argument("--temperature", type=float, default=0.6)
    chat_parser.add_argument("--max-tokens", type=int, default=4096)

    args = parser.parse_args()

    client = StreamingClient()

    if args.mode == "chat":
        messages = [{"role": "user", "content": args.prompt}]
        if args.stream:
            print(f"[streaming] 模型: {args.model}", file=sys.stderr)
            async for event in client.chat_stream(
                messages, model=args.model,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
            ):
                if event.type == StreamEventType.CHUNK:
                    print(event.content or "", end="", flush=True)
                elif event.type == StreamEventType.DONE:
                    print()
                    if event.usage:
                        print(f"\n[usage] {event.usage}", file=sys.stderr)
                elif event.type == StreamEventType.ERROR:
                    print(f"\n[error] {event.error}", file=sys.stderr)
                elif event.type == StreamEventType.THROTTLE:
                    print(f"\n[throttle] {event.content}", file=sys.stderr)
            print(f"\n[stats] {client.get_usage_stats()}", file=sys.stderr)
        else:
            result = await client.chat(messages, model=args.model)
            print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.mcp:
        await run_mcp()
    else:
        parser.print_help()


def _mcp_send(obj):
    print(json.dumps(obj, ensure_ascii=False), flush=True)


async def run_mcp():
    """MCP Server 模式（标准 MCP 协议）"""
    client = StreamingClient()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            method = req.get("method", "")
            params = req.get("params", {})
            req_id = req.get("id", "")

            # 标准 MCP 握手
            if method == "initialize":
                _mcp_send({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {"listChanged": True}},
                        "serverInfo": {"name": "deepcode-streaming", "version": "1.0.0"},
                    },
                })
            elif method == "notifications/initialized":
                pass
            elif method == "tools/list":
                _mcp_send({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {
                        "tools": [
                            {
                                "name": "streaming_chat",
                                "description": "流式 Chat 补全",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "prompt": {"type": "string"},
                                        "system": {"type": "string"},
                                        "model": {"type": "string"},
                                        "temperature": {"type": "number"},
                                        "max_tokens": {"type": "integer"},
                                        "stream": {"type": "boolean"},
                                    },
                                    "required": ["prompt"],
                                },
                            },
                        ],
                    },
                })
            elif method == "tools/call":
                name = params.get("name", "")
                args_dict = params.get("arguments", {})
                if name == "streaming_chat":
                    messages = []
                    if args_dict.get("system"):
                        messages.append({"role": "system", "content": args_dict["system"]})
                    messages.append({"role": "user", "content": args_dict["prompt"]})
                    stream = args_dict.get("stream", True)
                    if stream:
                        full_text = ""
                        async for event in client.chat_stream(
                            messages,
                            model=args_dict.get("model", "deepseek-v4-flash"),
                            temperature=args_dict.get("temperature", 0.6),
                            max_tokens=args_dict.get("max_tokens", 4096),
                        ):
                            if event.type == StreamEventType.CHUNK:
                                full_text += event.content or ""
                            elif event.type == StreamEventType.ERROR:
                                full_text += f"\n[error] {event.error}"
                        result = {"response": full_text}
                    else:
                        chat_kw = dict(args_dict)
                        chat_kw.pop('stream', None)
                        chat_kw.pop('prompt', None)
                        chat_kw.pop('system', None)
                        resp = await client.chat(messages, **chat_kw)
                        result = {"response": resp.get("choices", [{}])[0].get("message", {}).get("content", "")}
                else:
                    result = {"error": f"Unknown: {name}"}
                _mcp_send({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]},
                })
        except json.JSONDecodeError:
            pass


if __name__ == "__main__":
    asyncio.run(main_cli())
