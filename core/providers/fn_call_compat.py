"""非函数调用模型的降级开关（E10 探索项 · AutoAgent 对标，additive）。

对标：AutoAgent ``constant.py:61-85``（按模型名白名单开关
``FN_CALL/ADD_USER/NON_FN_CALL``）+ ``fn_call_converter.py``（函数调用 ↔
非函数调用消息互转），适配 deepseek-reasoner / o1 等不支持函数调用的模型
（规格来源 ``docs/hkuds-study/05-agent-ecosystem-scan.md`` §1.2 / §7
AutoAgent "[P3] 按模型名 fn_call 兼容开关"）。

本模块提供**机制 + 开关**，不改变任何现有调用路径（additive）：

1. :func:`resolve_fn_call_policy` —— 纯函数：给定 ``(model, spec, 开关覆盖)``
   判定该模型本轮请求是否走函数调用通道；
2. :func:`to_non_function_call_messages` —— 把 tool_calls/tool 结果回合
   互转为纯文本消息（NON_FN_CALL 通道）；
3. :func:`build_fallback_system_reminder` —— 降级时把工具清单以纯文本
   提醒注入 user 消息，让模型仍知道有哪些工具可用。

开关来源（优先级从高到低）：
- ``enabled`` 显式参数（调用方/配置直接指定）；
- 环境变量 ``DEEPCODE_FN_CALL_COMPAT``：``on/1`` 强制开、``off/0`` 强制关、
  ``auto``（默认）按模型名判定。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

# 已知不支持（或官方不推荐）函数调用参数传递的模型名特征（保守清单）。
# 注意：这是降级名单，不是能力白名单 —— 名单命中即默认关闭 fn_call。
FN_CALL_DISABLED_MODEL_TOKENS: tuple[str, ...] = (
    "deepseek-reasoner",
    "o1-",  # o1 系列（部分端点不支持 tool_choice）
    "o3-",
    "o4-",
    "reasoner",
)

# 环境变量开关取值
_FN_CALL_COMPAT_ENV = "DEEPCODE_FN_CALL_COMPAT"


@dataclass(frozen=True)
class FnCallPolicy:
    """一次请求的函数调用通道判定结果。"""

    fn_call_supported: bool
    reason: str = ""


def _model_tokens(model: str) -> str:
    return (model or "").lower()


def _is_known_non_fn_call_model(model: str) -> bool:
    name = _model_tokens(model)
    return any(token in name for token in FN_CALL_DISABLED_MODEL_TOKENS)


def resolve_fn_call_policy(
    *,
    model: str,
    spec: Any | None = None,
    enabled: bool | None = None,
) -> FnCallPolicy:
    """判定模型是否走函数调用通道。

    纯函数，无 IO。判定顺序：

    1. ``enabled`` 显式覆盖（最高优先）；
    2. 环境变量 ``DEEPCODE_FN_CALL_COMPAT``（``on/1`` → True，``off/0`` → False）；
    3. 模型名特征命中降级名单 → False；
    4. 其余默认 True。

    ``spec``（ProviderSpec）预留：未来可把开关声明为 provider 级配置
    （如 ``spec.fn_call_compat``），此处仅透传不消费。
    """
    if enabled is not None:
        return FnCallPolicy(fn_call_supported=bool(enabled), reason="explicit override")

    env_value = os.environ.get(_FN_CALL_COMPAT_ENV, "").strip().lower()
    if env_value in ("on", "1", "true", "yes"):
        return FnCallPolicy(fn_call_supported=True, reason="env override on")
    if env_value in ("off", "0", "false", "no"):
        return FnCallPolicy(fn_call_supported=False, reason="env override off")

    if _is_known_non_fn_call_model(model):
        return FnCallPolicy(
            fn_call_supported=False,
            reason=f"model '{model}' is in the fn_call fallback list",
        )
    return FnCallPolicy(fn_call_supported=True, reason="default")


def to_non_function_call_messages(
    messages: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把「带 tool_calls 的 assistant 回合 + 后续 tool 结果」互转为纯文本。

    对标 AutoAgent ``fn_call_converter.py``：不支持函数调用的模型看不到
    ``tool_calls`` 字段，因此把工具调用意图（JSON）与工具结果拼成普通
    user 消息喂回模型。输入输出均为新列表，不修改入参（纯函数）。

    ``tool_calls`` 为 OpenAI 风格列表，如::

        [{"id": "call_1", "type": "function",
          "function": {"name": "read_file", "arguments": "{\"path\": \"a.txt\"}"}}]
    """
    result: list[dict[str, Any]] = []
    for message in messages:
        clean = dict(message)
        role = clean.get("role")
        if role == "assistant" and clean.get("tool_calls"):
            # 把工具调用意图折叠进 assistant 内容（纯文本描述）。
            # 注意：先取出 tool_calls 再 pop，避免 pop 后读回 None。
            calls = clean.get("tool_calls") or tool_calls or []
            clean.pop("tool_calls", None)
            calls_text = _tool_calls_to_text(calls)
            text = str(clean.get("content") or "")
            clean["content"] = (
                (text + "\n\n" + calls_text).strip() if text else calls_text
            )
            result.append(clean)
            continue
        if role == "tool":
            # 工具结果回合转成 user 消息：tool_call_id 保留在文本里
            text = _tool_result_to_text(clean)
            result.append({"role": "user", "content": text})
            continue
        result.append(clean)
    return result


def _tool_calls_to_text(tool_calls: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for index, call in enumerate(tool_calls):
        fn = call.get("function") or {}
        name = fn.get("name") or call.get("name") or f"tool_{index}"
        arguments = fn.get("arguments") or "{}"
        try:
            parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
            arguments_text = json.dumps(parsed, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            arguments_text = str(arguments)
        lines.append(
            f"[tool_call {index}] {name}({arguments_text}) "
            f"(call_id: {call.get('id', '')})"
        )
    return "\n".join(lines) if lines else "[no tool calls]"


def _tool_result_to_text(message: dict[str, Any]) -> str:
    call_id = str(message.get("tool_call_id") or "")
    content = message.get("content") or ""
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    prefix = f"[tool_result {call_id}] " if call_id else "[tool_result] "
    return prefix + content


def build_fallback_system_reminder(tools: list[dict[str, Any]]) -> str:
    """降级通道下注入 user 消息的工具清单提醒（纯文本）。

    ``tools`` 为 OpenAI 风格工具定义列表（``{"type": "function",
    "function": {"name", "description", "parameters"}}``）。模型无法走
    函数调用参数传递时，用这段文本告知可用工具及调用语法。
    """
    if not tools:
        return ""
    lines = [
        "注意：当前模型不支持函数调用参数传递，请用以下文本语法调用工具：",
        "",
    ]
    for tool in tools:
        fn = tool.get("function") if isinstance(tool, dict) else None
        if not isinstance(fn, dict):
            continue
        name = fn.get("name", "")
        description = str(fn.get("description") or "").strip()
        parameters = fn.get("parameters") or {}
        args = ", ".join(
            f"{key}: {value.get('type', 'any')}"
            for key, value in (parameters.get("properties") or {}).items()
        )
        line = f"- {name}({args})"
        if description:
            line += f" — {description}"
        lines.append(line)
    lines.append("")
    lines.append("调用格式：[tool_call] <name>(<json args>)")
    return "\n".join(lines)


__all__ = [
    "FN_CALL_DISABLED_MODEL_TOKENS",
    "FnCallPolicy",
    "build_fallback_system_reminder",
    "resolve_fn_call_policy",
    "to_non_function_call_messages",
]
