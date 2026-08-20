"""Runner helper utilities (subset of nanobot.utils.helpers).

Pulled in only the helpers actually used by :mod:`core.providers.base` and
:mod:`core.agent_runtime.runner`. Tool-result persistence to disk is kept
since the runner threads ``workspace`` / ``session_key`` through ``AgentRunSpec``.
"""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

try:  # tiktoken is optional; we degrade to a length-based estimate when missing.
    import tiktoken  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - import guarded
    tiktoken = None  # type: ignore[assignment]


def strip_think(text: str) -> str:
    """Remove thinking blocks and any unclosed trailing tag."""
    text = re.sub(r"<think>[\s\S]*?</think>", "", text)
    text = re.sub(r"^\s*<think>[\s\S]*$", "", text)
    text = re.sub(r"<thought>[\s\S]*?</thought>", "", text)
    text = re.sub(r"^\s*<thought>[\s\S]*$", "", text)
    return text.strip()


def image_placeholder_text(path: str | None, *, empty: str = "[image]") -> str:
    return f"[image: {path}]" if path else empty


def truncate_text(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... (truncated)"


_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*]')
_TOOL_RESULT_PREVIEW_CHARS = 1200
_TOOL_RESULTS_DIR = ".deepcode/tool-results"
# A spilled result is referenced by the session history that produced it, so
# its bucket has to outlive anything a user might resume. Deleting by RANK
# (a "keep the newest N sessions" cap) broke exactly that: in a workspace with
# more sessions than the cap, an older session's locators went dangling while
# its history still pointed at them. Age is the only honest signal available
# at this layer — the kernel cannot see which sessions still exist — so the
# horizon is long enough that a bucket only disappears once nobody has
# resumed that session for a full quarter.
_TOOL_RESULT_RETENTION_SECS = 90 * 24 * 60 * 60


def safe_filename(name: str) -> str:
    return _UNSAFE_CHARS.sub("_", name).strip()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def find_legal_message_start(messages: list[dict[str, Any]]) -> int:
    """Find the first index whose tool results have matching assistant calls."""
    declared: set[str] = set()
    start = 0
    for i, msg in enumerate(messages):
        role = msg.get("role")
        if role == "assistant":
            for tc in msg.get("tool_calls") or []:
                if isinstance(tc, dict) and tc.get("id"):
                    declared.add(str(tc["id"]))
        elif role == "tool":
            tid = msg.get("tool_call_id")
            if tid and str(tid) not in declared:
                start = i + 1
                declared.clear()
                for prev in messages[start : i + 1]:
                    if prev.get("role") == "assistant":
                        for tc in prev.get("tool_calls") or []:
                            if isinstance(tc, dict) and tc.get("id"):
                                declared.add(str(tc["id"]))
    return start


def stringify_text_blocks(content: list[dict[str, Any]]) -> str | None:
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            return None
        if block.get("type") != "text":
            return None
        text = block.get("text")
        if not isinstance(text, str):
            return None
        parts.append(text)
    return "\n".join(parts)


def _render_tool_result_reference(
    filepath: Path,
    *,
    original_size: int,
    preview: str,
    truncated_preview: bool,
) -> str:
    result = (
        f"[tool output persisted]\n"
        f"Full output saved to: {filepath}\n"
        f"Original size: {original_size} chars\n"
        f"Preview:\n{preview}"
    )
    if truncated_preview:
        result += "\n...\n(Read the saved file if you need the full output.)"
    return result


def _bucket_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


_SWEPT_TOOL_RESULT_ROOTS: set[str] = set()


def _cleanup_tool_result_buckets(root: Path, current_bucket: Path) -> None:
    """Drop buckets no session has touched inside the retention horizon.

    Swept once per root per process: this used to run on every oversized tool
    result, scanning and stat-ing the whole directory on a hot path.
    """
    key = str(root)
    if key in _SWEPT_TOOL_RESULT_ROOTS:
        return
    _SWEPT_TOOL_RESULT_ROOTS.add(key)
    cutoff = time.time() - _TOOL_RESULT_RETENTION_SECS
    for path in root.iterdir():
        if path.is_dir() and path != current_bucket and _bucket_mtime(path) < cutoff:
            shutil.rmtree(path, ignore_errors=True)


def _write_text_atomic(path: Path, content: str) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def maybe_persist_tool_result(
    workspace: Path | None,
    session_key: str | None,
    tool_call_id: str,
    content: Any,
    *,
    max_chars: int,
) -> Any:
    """Persist oversized tool output and replace it with a stable reference string."""
    if workspace is None or max_chars <= 0:
        return content

    text_payload: str | None = None
    suffix = "txt"
    if isinstance(content, str):
        text_payload = content
    elif isinstance(content, list):
        text_payload = stringify_text_blocks(content)
        if text_payload is None:
            return content
        suffix = "json"
    else:
        return content

    if len(text_payload) <= max_chars:
        return content

    root = ensure_dir(workspace / _TOOL_RESULTS_DIR)
    bucket = ensure_dir(root / safe_filename(session_key or "default"))
    try:
        _cleanup_tool_result_buckets(root, bucket)
    except Exception as exc:
        logger.warning("Failed to clean stale tool result buckets in {}: {}", root, exc)
    path = bucket / f"{safe_filename(tool_call_id)}.{suffix}"
    if not path.exists():
        if suffix == "json" and isinstance(content, list):
            _write_text_atomic(path, json.dumps(content, ensure_ascii=False, indent=2))
        else:
            _write_text_atomic(path, text_payload)

    preview = text_payload[:_TOOL_RESULT_PREVIEW_CHARS]
    # P3-B (GenAI lesson 19): when the SLM subtask router classifies tool-result
    # cleanup as an SLM-grade task, shape the preview as a dense noise-stripped
    # digest instead of the raw head (still zero network; decision-only).
    try:
        from core.loop.slm_tasks import choose_tool_result_preview

        preview = choose_tool_result_preview(
            text_payload, default_preview=preview
        )
    except Exception:
        pass
    return _render_tool_result_reference(
        path,
        original_size=len(text_payload),
        preview=preview,
        truncated_preview=len(text_payload) > _TOOL_RESULT_PREVIEW_CHARS,
    )


def history_signature(
    messages: list[dict[str, Any]],
) -> tuple[tuple[str, int], ...]:
    """A cheap positional signature of the model-visible conversation.

    Role plus content size per non-system message: enough to tell "the same
    history with more appended" from "a history that was rewritten", without
    serializing every message on a hot path. Shared by the token meter's
    anchor and the compaction memo so the two agree on what "unchanged"
    means.
    """
    signature: list[tuple[str, int]] = []
    for message in messages:
        if message.get("role") == "system":
            continue
        content = message.get("content")
        length = len(content) if isinstance(content, str) else 0
        calls = message.get("tool_calls") or ()
        signature.append((str(message.get("role")), length + 16 * len(calls)))
    return tuple(signature)


def build_assistant_message(
    content: str | None,
    tool_calls: list[dict[str, Any]] | None = None,
    reasoning_content: str | None = None,
    reasoning_summary: str | None = None,
    provider_state: dict[str, Any] | None = None,
    thinking_blocks: list[dict] | None = None,
) -> dict[str, Any]:
    """Build a provider-safe assistant message with optional reasoning fields."""
    msg: dict[str, Any] = {"role": "assistant", "content": content or ""}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if reasoning_content is not None or thinking_blocks:
        msg["reasoning_content"] = (
            reasoning_content if reasoning_content is not None else ""
        )
    if thinking_blocks:
        msg["thinking_blocks"] = thinking_blocks
    if reasoning_summary:
        msg["reasoning_summary"] = reasoning_summary
    if provider_state:
        msg["provider_state"] = provider_state
    return msg


def estimate_prompt_tokens(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> int:
    parts: list[str] = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    txt = part.get("text", "")
                    if txt:
                        parts.append(txt)

        tc = msg.get("tool_calls")
        if tc:
            parts.append(json.dumps(tc, ensure_ascii=False))

        rc = msg.get("reasoning_content")
        if isinstance(rc, str) and rc:
            parts.append(rc)

        for key in ("name", "tool_call_id"):
            value = msg.get(key)
            if isinstance(value, str) and value:
                parts.append(value)

    if tools:
        parts.append(json.dumps(tools, ensure_ascii=False))

    payload = "\n".join(parts)
    per_message_overhead = len(messages) * 4
    try:
        if tiktoken is None:
            raise RuntimeError("tiktoken unavailable")
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(payload)) + per_message_overhead
    except Exception:
        # Context governance must remain active in minimal/offline installs
        # where the optional tokenizer is unavailable. Four characters per
        # token is deliberately conservative for typical source-code prompts.
        return max(per_message_overhead, len(payload) // 4 + per_message_overhead)


def estimate_message_tokens(message: dict[str, Any]) -> int:
    content = message.get("content")
    parts: list[str] = []
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text", "")
                if text:
                    parts.append(text)
            else:
                parts.append(json.dumps(part, ensure_ascii=False))
    elif content is not None:
        parts.append(json.dumps(content, ensure_ascii=False))

    for key in ("name", "tool_call_id"):
        value = message.get(key)
        if isinstance(value, str) and value:
            parts.append(value)
    if message.get("tool_calls"):
        parts.append(json.dumps(message["tool_calls"], ensure_ascii=False))

    rc = message.get("reasoning_content")
    if isinstance(rc, str) and rc:
        parts.append(rc)

    payload = "\n".join(parts)
    if not payload:
        return 4
    try:
        if tiktoken is None:
            raise RuntimeError("tiktoken unavailable")
        enc = tiktoken.get_encoding("cl100k_base")
        return max(4, len(enc.encode(payload)) + 4)
    except Exception:
        return max(4, len(payload) // 4 + 4)


def estimate_prompt_tokens_chain(
    provider: Any,
    model: str | None,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> tuple[int, str]:
    provider_counter = getattr(provider, "estimate_prompt_tokens", None)
    if callable(provider_counter):
        try:
            tokens, source = provider_counter(messages, tools, model)
            if isinstance(tokens, (int, float)) and tokens > 0:
                return int(tokens), str(source or "provider_counter")
        except Exception:
            pass

    estimated = estimate_prompt_tokens(messages, tools)
    if estimated > 0:
        return int(estimated), "estimated"
    return 0, "none"
