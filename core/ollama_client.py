#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""共享 Ollama 接入层 — 统一 4 个模块的 Ollama HTTP 调用。

收敛对象 (此前各自重复实现):
  - cerebellum_core.py      小脑: /api/embed 批处理 + /api/generate + /api/tags (urllib)
  - router_cascade.py       路由: /api/embed + /api/generate + /api/tags (urllib)
  - token_saver_mcp_server  压缩: /api/chat + /api/tags (httpx)
  - mcp-servers/ollama-mcp  桥接: /api/generate|chat 流式 + 旧版 /api/embeddings (urllib)

设计原则:
  - 零第三方依赖: 仅标准库 urllib (统一原 httpx 路径)
  - 低层接入: 只做 HTTP 封装 + 数据提取; 异常向上抛, 由调用方保持各自的降级策略
  - 不动配置: 各模块保留自己的 env 前缀与默认模型, 本模块只收"怎么调"
  - 兼容双接口: /api/generate (补全式) 与 /api/chat (聊天式) 都支持; 流式/非流式都支持
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict, List, Optional


# ═══════════════════════════════════════════
# 底层 HTTP (urllib, 零第三方依赖)
# ═══════════════════════════════════════════


def _url(host: str, path: str) -> str:
    return f"{host.rstrip('/')}{path}"


def post_json(
    host: str, path: str, payload: Dict[str, Any], timeout: int = 120
) -> Dict[str, Any]:
    """POST JSON → JSON。网络/解析异常向上抛, 由调用方决定降级。"""
    req = urllib.request.Request(
        _url(host, path),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_json(host: str, path: str, timeout: int = 30) -> Dict[str, Any]:
    """GET → JSON。异常向上抛。"""
    req = urllib.request.Request(_url(host, path), method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _stream_lines(
    host: str, path: str, payload: Dict[str, Any], timeout: int = 120
) -> List[Dict[str, Any]]:
    """流式 POST, 逐行解析 JSON 对象 (Ollama NDJSON)。异常向上抛。"""
    req = urllib.request.Request(
        _url(host, path),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    chunks: List[Dict[str, Any]] = []
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for line in resp:
            line = line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                chunks.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return chunks


# ═══════════════════════════════════════════
# 健康检查
# ═══════════════════════════════════════════


def status(host: str) -> Dict[str, Any]:
    """Ollama 健康检查 (GET /api/tags)。失败自带降级: {"ok": False, "error": ...}"""
    try:
        tags = get_json(host, "/api/tags", timeout=5)
        models = [m["name"] for m in tags.get("models", [])]
        return {"ok": True, "host": host, "models": models}
    except Exception as e:
        return {"ok": False, "host": host, "error": str(e)}


# ═══════════════════════════════════════════
# 向量嵌入 (新版 /api/embed, 支持批处理)
# ═══════════════════════════════════════════


def embed(
    host: str, model: str, texts: List[str], timeout: int = 60
) -> List[List[float]]:
    """批量向量化, 返回 embeddings 列表 (与 texts 顺序一致)。异常向上抛。"""
    resp = post_json(
        host, "/api/embed", {"model": model, "input": texts}, timeout=timeout
    )
    return resp.get("embeddings", [])


# ═══════════════════════════════════════════
# 补全式生成 (/api/generate, 非流式)
# ═══════════════════════════════════════════


def generate(
    host: str,
    model: str,
    prompt: str,
    system: str = "",
    temperature: float = 0.3,
    max_tokens: int = 512,
    timeout: int = 120,
    think: Optional[bool] = None,
) -> str:
    """补全式生成, 返回正文文本。异常向上抛。

    think: qwen3 等思考模型的控制开关; None=不控制, False=关闭思考
           (摘要/分类任务建议 False, 避免思考过程吃掉 token 预算)。
    兜底: 某些模型 response 为空但 thinking 有内容时, 取 thinking 末尾 200 字符。
    """
    payload: Dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }
    if think is not None:
        # qwen3 顶层 think 参数 (options 内不生效)
        payload["think"] = think
    resp = post_json(host, "/api/generate", payload, timeout=timeout)
    out = (resp.get("response") or "").strip()
    if not out and resp.get("thinking"):
        out = (resp.get("thinking") or "").strip()[-200:]
    return out


def generate_stream(
    host: str,
    model: str,
    prompt: str,
    system: str = "",
    temperature: float = 0.7,
    max_tokens: int = 2048,
    timeout: int = 120,
) -> str:
    """流式补全生成 (逐行 NDJSON), 拼接 response 返回。异常向上抛。"""
    payload: Dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": True,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }
    parts: List[str] = []
    for chunk in _stream_lines(host, "/api/generate", payload, timeout=timeout):
        text = chunk.get("response") or ""
        if text:
            parts.append(text)
        if chunk.get("done"):
            break
    return "".join(parts)


# ═══════════════════════════════════════════
# 聊天式生成 (/api/chat)
# ═══════════════════════════════════════════


def chat(
    host: str,
    model: str,
    messages: List[Dict[str, Any]],
    temperature: float = 0.2,
    max_tokens: int = 512,
    timeout: int = 180,
    keep_alive: str = "30m",
) -> Dict[str, Any]:
    """聊天式生成 (非流式), 返回 Ollama 原始 JSON
    (含 message.content / prompt_eval_count / eval_count)。异常向上抛。
    """
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens},
        "keep_alive": keep_alive,
    }
    return post_json(host, "/api/chat", payload, timeout=timeout)


def chat_stream(
    host: str,
    model: str,
    messages: List[Dict[str, Any]],
    temperature: float = 0.7,
    max_tokens: int = 2048,
    timeout: int = 120,
) -> str:
    """流式聊天 (逐行 NDJSON), 拼接 message.content 返回。异常向上抛。"""
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }
    parts: List[str] = []
    for chunk in _stream_lines(host, "/api/chat", payload, timeout=timeout):
        msg = chunk.get("message") or {}
        text = msg.get("content") or ""
        if text:
            parts.append(text)
        if chunk.get("done"):
            break
    return "".join(parts)


# ═══════════════════════════════════════════
# 模型管理
# ═══════════════════════════════════════════


def list_models(host: str, timeout: int = 30) -> List[str]:
    """列出已安装模型名。异常向上抛。"""
    tags = get_json(host, "/api/tags", timeout=timeout)
    return [m["name"] for m in tags.get("models", [])]


def pull_model(host: str, model: str, timeout: int = 300) -> Dict[str, Any]:
    """拉取模型 (POST /api/pull)。异常向上抛。"""
    return post_json(
        host, "/api/pull", {"model": model, "stream": False}, timeout=timeout
    )


def delete_model(host: str, model: str, timeout: int = 60) -> Dict[str, Any]:
    """删除模型 (DELETE /api/delete)。异常向上抛。"""
    req = urllib.request.Request(
        _url(host, "/api/delete"),
        data=json.dumps({"model": model}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="DELETE",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ═══════════════════════════════════════════
# 自检 (smoke test)
# ═══════════════════════════════════════════

if __name__ == "__main__":
    import sys

    host = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:11434"
    print("status:", status(host))
    print("models:", list_models(host) if status(host).get("ok") else [])
