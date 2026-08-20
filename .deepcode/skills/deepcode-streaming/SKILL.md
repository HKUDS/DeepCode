---
name: deepcode-streaming
description: >
  DeepCode Streaming API — 移植自 Claude Code v2.1.216 的流式 API 支持。
  SSE (Server-Sent Events) / Chunked / WebSocket 流式传输。
  对标 Claude Code: calculateNonstreamingTimeout, buildRequest,
  buildHeaders, buildBody, shouldRetry 等。
version: 1.0.0
author: DeepCode + Ghidra RE (Claude Code v2.1.216)
date: 2026-07-26
tags: [streaming, sse, api, llm]
---

# DeepCode Streaming API

移植自 **Claude Code v2.1.216** 的流式 API 能力。

## 对标项

| Claude Code | DeepCode Streaming |
|:-----------|:-------------------|
| `_calculateNonstreamingTimeout` | `StreamingClient._calculate_timeout()` |
| `buildRequest` | `_build_headers()` + `_build_body()` |
| `buildHeaders` | `_build_headers()` |
| `buildBody` | `_build_body()` |
| `shouldRetry` | 指数退避重试逻辑 |
| `retryRequest` | while 循环 + backoff |
| SSE 解析 | `_parse_sse_line()` + `_extract_delta()` |

## 用法

### CLI

```bash
# 流式输出
python streaming_api.py chat --prompt "用Python写个快排" --stream

# 非流式
python streaming_api.py chat --prompt "1+1=?" 

# 指定模型
python streaming_api.py chat --prompt "分析" --model deepseek-reasoner --stream
```

### MCP Server

```json
"deepcode-streaming": {
  "command": "python",
  "args": [
    "F:/DEEPCODE/.deepcode/skills/deepcode-streaming/streaming_api.py",
    "--mcp"
  ],
  "env": {
    "DEEPSEEK_API_KEY": "${DEEPSEEK_API_KEY}"
  }
}
```

### Python

```python
from streaming_api import StreamingClient

client = StreamingClient(api_key="sk-xxx")

# 流式
async for event in client.chat_stream(
    messages=[{"role":"user","content":"你好"}],
    model="deepseek-chat",
):
    if event.type == StreamEventType.CHUNK:
        print(event.content, end="", flush=True)
    elif event.type == StreamEventType.DONE:
        print(f"\n用量: {event.usage}")

# 非流式
result = await client.chat(messages)
print(result["choices"][0]["message"]["content"])

# 用量统计
print(client.get_usage_stats())
```

## 特性

- **双模超时**: 流式 (300s) vs 非流式 (60s) 自动切换
- **自动重试**: 指数退避 (2s/4s/8s...)，最多 3 次
- **背压通知**: 重试时发送 THROTTLE 事件
- **Token 统计**: 自动累计 prompt/completion token
- **Delta 增量**: 纯增量输出，适合实时展示
