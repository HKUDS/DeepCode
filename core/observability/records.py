"""Schema for the three structured log streams DeepCode emits.

These are kept as :class:`dataclasses.dataclass` (not Pydantic) so the
hot logging path has zero validation overhead. They serialise to plain
JSON via :meth:`to_jsonl` for ``*.jsonl`` sinks.

``LLMLogRecord`` additionally carries the fields of the append-only
transparency log (P1-1 in ``docs/ROUTER_SUPPLY_CHAIN_HARDENING.md``): a
malicious model "router" between the harness and the provider can rewrite
the tool calls we receive and can read every credential we send. No
client-side check can prove such a relay is honest, but a hash chain over
the persisted entries answers the forensic question afterwards — *which
credentials flowed through which endpoint in which session* — because
rewriting or dropping a past entry breaks the chain. The chain is computed
at write time (:mod:`core.observability.bus`), not here: this module stays
a pure schema so existing construction sites keep working untouched.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def canonical_json(payload: Any) -> str:
    """Deterministic JSON used as the byte source for entry hashing.

    Stability matters more than readability here: the verifier recomputes
    these bytes from a line it read back (possibly on another machine), so
    the encoding must not depend on dict insertion order, locale, or
    ``repr`` drift. ``default=str`` keeps an exotic value (``Path``,
    ``Decimal``, ``datetime``) from raising inside the logging path; the
    string it produces is what ``json.dumps`` would have persisted, so the
    round-trip through the file is idempotent for the verifier.
    """
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )


def sha256_hex(value: Any) -> str | None:
    """Lowercase hex sha256 over *value*'s canonical bytes.

    ``None`` passes through as ``None`` (nothing was observed is different
    from "observed empty"). Strings are hashed as raw UTF-8 because a
    response body *is* text — re-encoding it as JSON would make the digest
    depend on quote/escape choices. Everything else (tool-call lists,
    dicts) goes through :func:`canonical_json` first so key order cannot
    change the digest. ``bytes`` are hashed verbatim.
    """
    if value is None:
        return None
    if isinstance(value, str):
        raw = value.encode("utf-8")
    elif isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
    else:
        raw = canonical_json(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def compute_entry_hash(payload: Mapping[str, Any], prev_hash: str | None) -> str:
    """Hash one transparency-log entry into its chain.

    ``entry_hash = sha256(canonical_json(payload) + prev_hash)`` where
    *payload* is exactly the object that gets persisted minus the
    ``entry_hash`` field itself. ``prev_hash`` is appended *outside* the
    JSON — it is also a field inside it, so it is covered twice, on purpose:
    an attacker who rewrites the stored ``prev_hash`` to re-point the chain
    changes the digest as well. The first entry of a file chains to the
    empty string, which is what makes line 1 verifiable with no prior file.
    """
    body = canonical_json(payload) + (prev_hash or "")
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass
class SystemLogRecord:
    """A generic loguru-derived record.

    System records are emitted automatically by the loguru patch
    installed by :func:`core.observability.bus.setup_logging`. Business
    code does not construct these directly.
    """

    timestamp: str
    level: str
    message: str
    logger: str = ""
    task_id: str | None = None
    session_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    exception: str | None = None

    def to_jsonl(self) -> str:
        payload = asdict(self)
        if not payload["extra"]:
            payload.pop("extra")
        if payload["exception"] is None:
            payload.pop("exception")
        return json.dumps(payload, ensure_ascii=False, default=str)


@dataclass
class LLMLogRecord:
    """One LLM call (request + response or error).

    The last seven fields are the transparency-log additions. They all
    default to ``None`` — i.e. "not recorded" — so every pre-existing
    construction site and every record written before this feature existed
    keeps serialising exactly as before (``to_jsonl`` drops ``None``).
    """

    timestamp: str
    task_id: str | None
    session_id: str | None
    provider: str
    model: str
    phase: str | None
    duration_ms: int
    status: str  # "ok" | "error" | "retry"
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    request_preview: str | None = None
    response_preview: str | None = None
    reasoning_preview: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    error: str | None = None
    # --- transparency log (P1-1) ---
    # Which endpoint actually served the call, so an exposure report can say
    # "these sessions went through that relay" rather than "some relay".
    endpoint_host: str | None = None
    endpoint_class: str | None = None
    # Nonce echoed to the provider once signed response envelopes land (P2-1).
    # Not generated here: a locally invented nonce would not be the one the
    # provider signed, which would make the field actively misleading.
    request_nonce: str | None = None
    # Digests of the FULL response / tool calls, taken before truncation, so a
    # rewrite past the 2000-char preview cap still changes the fingerprint.
    response_sha256: str | None = None
    tool_calls_sha256: str | None = None
    # Chain link: ``prev_hash`` is the previous entry's ``entry_hash`` in the
    # same file ("" for the first entry); ``entry_hash`` is this entry's digest.
    prev_hash: str | None = None
    entry_hash: str | None = None

    @classmethod
    def make(
        cls,
        *,
        task_id: str | None,
        session_id: str | None,
        provider: str,
        model: str,
        phase: str | None,
        duration_ms: int,
        status: str,
        finish_reason: str | None = None,
        usage: dict[str, int] | None = None,
        request_preview: str | None = None,
        response_preview: str | None = None,
        reasoning_preview: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        error: str | None = None,
        endpoint_host: str | None = None,
        endpoint_class: str | None = None,
        request_nonce: str | None = None,
        response_sha256: str | None = None,
        tool_calls_sha256: str | None = None,
        prev_hash: str | None = None,
        entry_hash: str | None = None,
    ) -> LLMLogRecord:
        usage = usage or {}
        return cls(
            timestamp=_utcnow_iso(),
            task_id=task_id,
            session_id=session_id,
            provider=provider,
            model=model,
            phase=phase,
            duration_ms=duration_ms,
            status=status,
            finish_reason=finish_reason,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            cached_tokens=usage.get("cached_tokens"),
            request_preview=request_preview,
            response_preview=response_preview,
            reasoning_preview=reasoning_preview,
            tool_calls=tool_calls,
            error=error,
            endpoint_host=endpoint_host,
            endpoint_class=endpoint_class,
            request_nonce=request_nonce,
            response_sha256=response_sha256,
            tool_calls_sha256=tool_calls_sha256,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
        )

    def chain_payload(self) -> dict[str, Any]:
        """The exact JSON object persisted for this entry, minus ``entry_hash``.

        Mirrors :meth:`to_jsonl`'s None-dropping so the digest covers the
        bytes a verifier will parse back, not an in-memory shape that may
        have picked up extra ``None`` fields.
        """
        payload = {k: v for k, v in asdict(self).items() if v is not None}
        payload.pop("entry_hash", None)
        return payload

    def apply_chain(self, prev_hash: str | None) -> str:
        """Link this entry to ``prev_hash``; set and return ``entry_hash``.

        Deliberately *not* part of :meth:`make`: the predecessor hash is only
        known by whoever holds the append handle for the target file, so the
        constructor stays pure and side-effect free.
        """
        self.prev_hash = prev_hash or ""
        self.entry_hash = compute_entry_hash(self.chain_payload(), self.prev_hash)
        return self.entry_hash

    def to_jsonl(self) -> str:
        payload = {k: v for k, v in asdict(self).items() if v is not None}
        return json.dumps(payload, ensure_ascii=False, default=str)


@dataclass
class MCPLogRecord:
    """One MCP tool call."""

    timestamp: str
    task_id: str | None
    session_id: str | None
    server: str
    tool: str
    duration_ms: int
    status: str  # "ok" | "error"
    arguments_preview: str | None = None
    result_preview: str | None = None
    error: str | None = None

    @classmethod
    def make(
        cls,
        *,
        task_id: str | None,
        session_id: str | None,
        server: str,
        tool: str,
        duration_ms: int,
        status: str,
        arguments_preview: str | None = None,
        result_preview: str | None = None,
        error: str | None = None,
    ) -> MCPLogRecord:
        return cls(
            timestamp=_utcnow_iso(),
            task_id=task_id,
            session_id=session_id,
            server=server,
            tool=tool,
            duration_ms=duration_ms,
            status=status,
            arguments_preview=arguments_preview,
            result_preview=result_preview,
            error=error,
        )

    def to_jsonl(self) -> str:
        payload = {k: v for k, v in asdict(self).items() if v is not None}
        return json.dumps(payload, ensure_ascii=False, default=str)


def truncate(text: Any, limit: int = 2000) -> str | None:
    """Cap *text* to ``limit`` chars, preserving JSON-serialisable shape.

    Used by the LLM/MCP loggers to avoid blowing up disk with multi-MB
    prompts. Returns ``None`` when the input is falsy.
    """
    if text is None:
        return None
    if not isinstance(text, str):
        try:
            text = json.dumps(text, ensure_ascii=False, default=str)
        except Exception:  # noqa: BLE001 - a pathological __str__ must not
            # take the caller's logging path down with it; str() is the last
            # resort that almost always still yields something loggable.
            text = str(text)
    if len(text) <= limit:
        return text
    head = limit - 32
    return text[:head] + f"...[truncated {len(text) - head} chars]"


__all__ = [
    "LLMLogRecord",
    "MCPLogRecord",
    "SystemLogRecord",
    "canonical_json",
    "compute_entry_hash",
    "sha256_hex",
    "truncate",
]
