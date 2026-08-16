"""P0-2: session-end memory distillation bridging DeepCode to cerebellum.

Claude Code's autoDream keeps a persistent memory pipeline: logs → session
summary → consolidated memory files, reusing prompt cache to stay cheap.
DeepCode already has the *storage* side (``core.harness.memory`` notes +
``core.sessions`` JSONL) and an external bridge for DSH
(``core.mcp_servers.dsh_cerebellum_bridge``), but its own agent loop never
feeds completed sessions into the cerebellum memory system. This module closes
that gap: when a session ends, the dialogue is extracted from the in-memory
history and deposited into cerebellum via its unified scheduler
(``.dsh/cerebellum-scheduler/scheduler.py session_end``).

Design rules:

* **Non-blocking, fail-soft.** Memory distillation is observability-grade
  work: it must never stall or crash the turn it runs after. We fire it on a
  background thread and swallow every error (log only).
* **In-memory history, no file hunting.** The session already holds the
  transcript in memory (``session.history``), so we serialize it directly
  instead of re-reading JSONL files — no path-format coupling with
  cerebellum's ``~/.deepcode/projects`` layout.
* **stdin pipe, not JSON payload.** cerebellum's ``session_end`` treats raw
  (non-JSON) stdin as the conversation text; we prefix a marker exactly like
  ``dsh_cerebellum_bridge`` so a transcript that happens to be valid JSON is
  never mistaken for a hook payload.
* **Opt-out via env.** ``DEEPCODE_MEMORY_DISTILL=0`` disables; missing
  scheduler binary degrades to a log line.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from loguru import logger

# Cerebellum unified scheduler entry (same constant the DSH bridge uses).
CEREBELLUM_SCHEDULER = Path(r"F:/DEEPCODE/.dsh/cerebellum-scheduler/scheduler.py")

# Non-JSON prefix marker (mirrors dsh_cerebellum_bridge.PREFIX_MARKER).
PREFIX_MARKER = "# deepcode-session-transcript v1\n"

# Cerebellum conversation-context cap (its _read_session_context max_chars).
MAX_CONTEXT_CHARS = 4000

# Per-message truncation.
MAX_MSG_CHARS = 600

# ---------------------------------------------------------------------------
# P0-2 upgrade (Codex Phase-1 lesson): secrets redaction + optional structured
# extraction before deposition.
# ---------------------------------------------------------------------------

# Heuristic secret patterns redacted before memory deposition (Codex redacts
# secrets from generated memory fields). Never blocks; best-effort.
_SECRET_PATTERNS = (
    (r"\b(sk-[A-Za-z0-9_-]{16,})\b", r"sk-[REDACTED]"),  # OpenAI-style keys
    (r"\b(AKIA[0-9A-Z]{16})\b", r"AKIA[REDACTED]"),  # AWS access key id
    (r"\b(ghp_[A-Za-z0-9]{20,})\b", r"ghp_[REDACTED]"),  # GitHub PAT
    (r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b", r"xox[REDACTED]"),  # Slack token
    (r"(-----BEGIN [A-Z ]+ PRIVATE KEY-----)", r"[REDACTED-PRIVATE-KEY]"),
    (r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/-]{16,}\b", r"\1[REDACTED]"),
    (r"(?i)(api[_-]?key['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9_./+=-]{8,}", r"\1[REDACTED]"),
    (r"(?i)(password['\"]?\s*[:=]\s*['\"]?)[^\s'\"]{6,}", r"\1[REDACTED]"),
    (r"(?i)(token['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9._~+/=-]{8,}", r"\1[REDACTED]"),
    (
        r"\b(eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})\b",
        r"[JWT-REDACTED]",
    ),
)

# Structured-extraction prompt (Codex Phase-1 style: raw_memory + summary +
# slug). Used only when DEEPCODE_MEMORY_DISTILL_STRUCTURED=1.
_STRUCTURED_SYSTEM = (
    "You distill a coding-agent session into a compact memory record. "
    "Respond with ONLY JSON: "
    '{"raw_memory": "<durable facts worth remembering across sessions, '
    'one per line>", "rollout_summary": "<2-3 sentence summary>", '
    '"rollout_slug": "<4-8 word lowercase slug>"}. '
    "Keep raw_memory under 40 lines, each line a concrete fact."
)
_STRUCTURED_USER = (
    "Distill this session transcript into a memory record:\n\n{transcript}"
)


def redact_secrets(text: str) -> str:
    """Redact common secret patterns from memory-bound text (best-effort)."""
    import re

    if not text:
        return text
    for pattern, replacement in _SECRET_PATTERNS:
        try:
            text = re.sub(pattern, replacement, text)
        except re.error:  # pragma: no cover - patterns are static
            continue
    return text


def structured_extraction_enabled() -> bool:
    """Whether Phase-1 structured extraction is on (env:
    ``DEEPCODE_MEMORY_DISTILL_STRUCTURED``; default off — it costs an LLM
    call on the background thread)."""
    value = os.environ.get("DEEPCODE_MEMORY_DISTILL_STRUCTURED", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


async def extract_structured_memory(
    transcript: str,
    provider: Any,
    *,
    model: str | None = None,
    max_tokens: int = 1024,
    timeout_s: float = 60.0,
) -> dict[str, Any] | None:
    """Generate a structured memory record from a transcript (Codex Phase-1).

    Async: the caller owns the event loop (``distill_session`` runs on a
    daemon thread and awaits this via ``asyncio.run`` once). Returns
    ``{"raw_memory", "rollout_summary", "rollout_slug"}`` or None on any
    failure (never raises).
    """
    import asyncio
    import re

    try:
        response = await asyncio.wait_for(
            provider.chat(
                [
                    {"role": "system", "content": _STRUCTURED_SYSTEM},
                    {
                        "role": "user",
                        "content": _STRUCTURED_USER.format(
                            transcript=transcript[:8000]
                        ),
                    },
                ],
                model=model,
                max_tokens=max_tokens,
                temperature=0.0,
            ),
            timeout=timeout_s,
        )
        text = response.content or ""
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        payload = json.loads(match.group(0))
        if not isinstance(payload, dict):
            return None
        record = {
            "raw_memory": redact_secrets(str(payload.get("raw_memory", "")))[:4000],
            "rollout_summary": redact_secrets(str(payload.get("rollout_summary", "")))[
                :1000
            ],
            "rollout_slug": redact_secrets(str(payload.get("rollout_slug", "")))[:120],
        }
        return record if any(record.values()) else None
    except Exception:  # noqa: BLE001 - never break distillation
        logger.debug("memory_distill: structured extraction failed", exc_info=True)
        return None


def compose_deposit_text(
    transcript: str,
    structured: dict[str, Any] | None,
) -> str:
    """Compose the text handed to cerebellum: raw transcript + (optional)
    structured memory record. Transcript is always redacted first."""
    body = redact_secrets(transcript)
    if not structured:
        return body
    parts = [body]
    # Defense-in-depth: structured fields are redacted again here in case a
    # caller handed us an unredacted record.
    summary = redact_secrets(str(structured.get("rollout_summary", "")))[:1000]
    raw = redact_secrets(str(structured.get("raw_memory", "")))[:4000]
    if summary:
        parts.append(f"\n\n# structured summary\n{summary}")
    if raw:
        parts.append(f"\n# raw memory\n{raw}")
    return "\n".join(parts)[:MAX_CONTEXT_CHARS]


def memory_distill_enabled() -> bool:
    """Whether session-end distillation is on (env: ``DEEPCODE_MEMORY_DISTILL``;
    default on when unset)."""
    value = os.environ.get("DEEPCODE_MEMORY_DISTILL", "").strip().lower()
    if not value:
        return True
    return value not in {"0", "false", "off", "no"}


def _collect_text(value: Any, out: list[str]) -> None:
    """Recursively collect ``text`` string fields (user/assistant content)."""
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            out.append(value["text"])
        for v in value.values():
            _collect_text(v, out)
    elif isinstance(value, list):
        for v in value:
            _collect_text(v, out)


def _truncate(text: str, limit: int = MAX_MSG_CHARS) -> str:
    text = text.strip()
    if len(text) > limit:
        return text[: limit - 20] + "\n...[truncated]..."
    return text


def dialogue_from_history(history: list[dict[str, Any]]) -> str:
    """Serialize in-memory session history into ``[user]/[assistant]/[tool]``
    dialogue lines, truncated to cerebellum's context cap."""
    lines: list[str] = []
    for message in history:
        if not isinstance(message, dict):
            continue
        role = message.get("role", "")
        content = message.get("content", "")
        parts: list[str] = []
        if role == "user":
            if isinstance(content, str) and content.strip():
                parts.append(content)
            else:
                _collect_text(content, parts)
            if parts:
                lines.append(f"[user] {_truncate(' '.join(parts))}")
        elif role == "assistant":
            if isinstance(content, str) and content.strip():
                lines.append(f"[assistant] {_truncate(content)}")
            else:
                _collect_text(content, parts)
                if parts:
                    lines.append(f"[assistant] {_truncate(' '.join(parts))}")
            # Tool calls the assistant made.
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        name = tc.get("name") or tc.get("function", {}).get("name", "?")
                        args = tc.get("arguments", {})
                        if isinstance(args, str):
                            args = args[:200]
                        else:
                            args = json.dumps(args, ensure_ascii=False)[:200]
                        lines.append(f"[tool] call: {name}({args})")
        elif role == "tool":
            parts = []
            _collect_text(content, parts)
            if isinstance(content, str) and content.strip():
                parts.insert(0, content)
            if parts:
                lines.append(f"[tool] result: {_truncate(' '.join(parts))}")
    joined = "\n".join(lines).strip()
    return joined[:MAX_CONTEXT_CHARS]


def _run_cerebellum_session_end(session_key: str, dialogue: str) -> int:
    """Invoke the scheduler; any failure returns non-zero (caller ignores)."""
    cmd = [
        sys.executable,
        str(CEREBELLUM_SCHEDULER),
        "session_end",
        "--session-id",
        session_key,
    ]
    result = subprocess.run(
        cmd,
        input=PREFIX_MARKER + dialogue,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=180,
        check=False,
    )
    if result.stdout.strip():
        logger.debug("memory_distill stdout: {}", result.stdout.strip()[:300])
    if result.stderr.strip():
        logger.debug("memory_distill stderr: {}", result.stderr.strip()[:300])
    return result.returncode


def distill_session(session_key: str, history: list[dict[str, Any]]) -> None:
    """Deposit a finished session's dialogue into cerebellum, best-effort.

    Runs synchronously but is *never* awaited on the hot path by callers
    directly; session.py wraps it in a daemon thread. Every failure is logged
    and swallowed — memory work must not break the turn.
    """
    if not memory_distill_enabled():
        return
    if not CEREBELLUM_SCHEDULER.exists():
        logger.warning(
            "memory_distill: cerebellum scheduler missing at {}; skipping",
            CEREBELLUM_SCHEDULER,
        )
        return
    if not history:
        return

    try:
        dialogue = dialogue_from_history(history)
    except Exception:  # noqa: BLE001
        logger.exception("memory_distill: dialogue extraction failed")
        return
    if not dialogue:
        logger.debug("memory_distill: no dialogue to distill for {}", session_key)
        return

    # P0-2: optional structured extraction (Codex Phase-1) before deposition.
    structured: dict[str, Any] | None = None
    if structured_extraction_enabled():
        try:
            provider = _resolve_provider()
            if provider is not None:
                import asyncio

                structured = asyncio.run(extract_structured_memory(dialogue, provider))
        except Exception:  # noqa: BLE001 - never break distillation
            logger.debug("memory_distill: structured provider resolve failed")

    # Redact secrets, then compose (transcript + optional structured record).
    deposit_text = compose_deposit_text(dialogue, structured)
    if not deposit_text:
        logger.debug("memory_distill: nothing to deposit for {}", session_key)
        return

    try:
        rc = _run_cerebellum_session_end(session_key, deposit_text)
        if rc == 0:
            logger.info(
                "memory_distill: session {} deposited ({} chars, structured={})",
                session_key,
                len(deposit_text),
                bool(structured),
            )
            _emit_distill_event(
                "memory.distill.ok",
                session_key,
                len(deposit_text),
                structured=bool(structured),
            )
        else:
            logger.warning(
                "memory_distill: cerebellum session_end rc={} for {}", rc, session_key
            )
            _emit_distill_event(
                "memory.distill.error",
                session_key,
                len(deposit_text),
                rc=rc,
                structured=bool(structured),
            )
    except Exception:  # noqa: BLE001 - never crash the caller
        logger.exception("memory_distill: cerebellum call failed for {}", session_key)
        _emit_distill_event("memory.distill.error", session_key, len(deposit_text))


def _resolve_provider() -> Any | None:
    """Best-effort resolve of the LLM provider for structured extraction.

    Uses the same workflow provider the session used. Returns None when
    resolution fails (structured extraction then simply doesn't run).
    """
    try:
        from core.llm_runtime import get_workflow_provider

        provider, _profile = get_workflow_provider(phase="implementation")
        return provider
    except Exception:  # noqa: BLE001
        return None


def _emit_distill_event(name: str, session_key: str, chars: int, **extra: Any) -> None:
    """Emit a P1-3 canonical event for memory distillation (never raises)."""
    try:
        from core.observability.events import emit_event

        emit_event(name, session=session_key, chars=chars, **extra)
    except Exception:  # noqa: BLE001, S110
        pass


def distill_session_async(session_key: str, history: list[dict[str, Any]]) -> None:
    """Fire :func:`distill_session` on a daemon thread (non-blocking)."""
    import threading

    try:
        thread = threading.Thread(
            target=distill_session,
            args=(session_key, list(history)),
            name="memory-distill",
            daemon=True,
        )
        thread.start()
    except Exception:  # noqa: BLE001
        logger.exception("memory_distill: thread spawn failed")


__all__ = [
    "compose_deposit_text",
    "dialogue_from_history",
    "distill_session",
    "distill_session_async",
    "extract_structured_memory",
    "memory_distill_enabled",
    "redact_secrets",
    "structured_extraction_enabled",
]
