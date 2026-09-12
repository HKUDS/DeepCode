"""LogBus: the single home for all loguru sink wiring.

The bus is intentionally module-level and idempotent. The first call to
:func:`setup_logging` wires up:

* a console sink (human-readable, level >= INFO by default);
* a global JSONL sink at ``logs/server-{date}.jsonl`` (rotation by day,
  retention configurable, all log records);
* a per-task JSONL sink at ``deepcode_lab/tasks/<task_id>/logs/system.jsonl``
  routed dynamically based on ``record["extra"]["task_id"]``;
* a global ``logger.patch`` that injects the active ``task_id`` and
  ``session_id`` (from :mod:`core.observability.context`) into every
  loguru record so existing ``from loguru import logger`` calls stay
  unmodified.

LLM and MCP records are emitted via :func:`log_llm_call` /
:func:`log_mcp_call` directly (they bypass loguru because their schemas
are richer and they need their own files).

``llm.jsonl`` is also the home of the append-only transparency log (P1-1):
every :class:`LLMLogRecord` we append is linked into a hash chain
(``prev_hash`` -> ``entry_hash``) *at write time* — the link depends on the
target file, so it cannot live in the record constructor. MCP and system
records keep their existing formats and are deliberately **not** chained:
the forensic question is about credential flow to the model endpoint, and
mixing three schemas into one chain would make the verifier's line numbers
meaningless. Telemetry stays local (console + JSONL sinks only); do not add
a network exporter here — ``MCPLogRecord.arguments_preview`` can contain
credentials (see the P1-1 note in ``docs/ROUTER_SUPPLY_CHAIN_HARDENING.md``).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from loguru import logger as _loguru_logger

from core.observability.context import current_session_id, current_task_id
from core.observability.records import (
    LLMLogRecord,
    MCPLogRecord,
    sha256_hex,
    truncate,
)

if TYPE_CHECKING:
    from core.config import LoggerConfig


class _StdlibBridgeHandler(logging.Handler):
    """Route stdlib ``logging`` records through loguru's configured sinks.

    Half the application logs through ``logging.getLogger(__name__)`` while
    every sink decision (console vs file, levels, task routing) lives in
    loguru. Without this bridge those records fall through to Python's
    ``lastResort`` stderr handler, ignoring the sink configuration entirely —
    which is how a background thread's WARNING traceback could shred an
    interactive TUI transcript even though the console transport was off.
    The frame walk is loguru's own documented interception recipe: it makes
    the record appear from its real call site, not from this handler.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = _loguru_logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        # Climb past this handler's own frame and every frame inside the
        # logging package; what remains is the record's real call site, and
        # the climb count is exactly loguru's ``depth``. The walk starts at
        # THIS frame rather than ``logging.currentframe()``, whose internal
        # offset is a CPython implementation detail — the supported matrix
        # spans 3.12 to 3.14, and a wrong start would misattribute every
        # bridged record.
        frame, depth = sys._getframe(), 0
        while frame is not None and (
            depth == 0 or frame.f_code.co_filename == logging.__file__
        ):
            frame = frame.f_back
            depth += 1
        _loguru_logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

_LOCK = threading.Lock()
_INITIALISED = False
_SINK_IDS: list[int] = []
_TASK_DIRS: dict[str, Path] = {}
_DEFAULT_TASK_LOG_DIR_FALLBACK = Path("logs") / "tasks"
_GLOBAL_LOG_DIR_FALLBACK = Path("logs")
_LLM_PREVIEW_CHARS = 2000
_MCP_PREVIEW_CHARS = 2000

# --- transparency-log chain state (P1-1) -----------------------------------
# Keyed by *resolved* log path, not by task_id: two tasks can share a
# fallback file (both unbound -> ``logs/llm.jsonl``) and splitting the chain
# by task id would hand out the same predecessor hash twice. The value is the
# ``entry_hash`` of the last line this process appended, or the hash recovered
# from the file tail the first time we touch a file after a restart.
_CHAIN_LOCK = threading.Lock()
_CHAIN_TAIL: dict[str, str] = {}
# Backward-scan chunk for tail recovery. Small enough not to buffer a big log,
# large enough that the common case (one short line) is found in one read.
_CHAIN_SCAN_CHUNK = 64 * 1024


# ---------------------------------------------------------------------------
# Public: setup / shutdown
# ---------------------------------------------------------------------------


def setup_logging(
    config: LoggerConfig | None = None,
    *,
    workspace_root: Path | None = None,
    force: bool = False,
    console_sink: TextIO | logging.Handler | None = None,
) -> None:
    """Wire up loguru sinks. Idempotent unless ``force=True``.

    ``config`` is a :class:`core.config.LoggerConfig` instance. When
    omitted, sensible defaults are used (level=INFO, console + global
    JSONL + per-task JSONL).

    ``workspace_root`` controls where the global log file lives (``logs/``
    is created relative to it). When omitted the current working
    directory is used.

    ``console_sink`` lets a headless host redirect the console channel without
    replacing the shared stdlib/loguru bridge or duplicating its routing.
    """
    global _INITIALISED, _LLM_PREVIEW_CHARS, _MCP_PREVIEW_CHARS

    with _LOCK:
        if _INITIALISED and not force:
            return

        _remove_managed_sinks()
        # On first wire-up also drop loguru's built-in stderr sink so we
        # don't double-print every line. Subsequent calls are no-ops at
        # this layer because we already own the sink ids.
        if not _INITIALISED:
            try:
                _loguru_logger.remove()
            except ValueError:
                pass

        level = (getattr(config, "level", None) or "INFO").upper()
        transports = list(getattr(config, "transports", []) or ["console", "file"])
        truncate_chars = int(
            getattr(getattr(config, "llm", None), "truncate_preview_chars", 0)
            or _LLM_PREVIEW_CHARS
        )
        _LLM_PREVIEW_CHARS = truncate_chars
        _MCP_PREVIEW_CHARS = truncate_chars

        # Apply the global patch once: every loguru record gets the
        # current task_id / session_id from contextvars so business code
        # never has to thread these through.
        _loguru_logger.configure(patcher=_inject_context)

        # Take over stdlib logging so its records obey the same transports.
        # level=0 hands everything to loguru, whose sinks apply the real
        # level filter; force=True keeps this idempotent across re-setups.
        logging.basicConfig(handlers=[_StdlibBridgeHandler()], level=0, force=True)

        if "console" in transports or not transports:
            sid = _loguru_logger.add(
                console_sink if console_sink is not None else sys.stderr,
                level=level,
                format=_console_format,
                backtrace=False,
                diagnose=False,
                enqueue=False,
            )
            _SINK_IDS.append(sid)

        if "global_file" in transports or "file" in transports:
            global_dir = _resolve_global_log_dir(workspace_root)
            global_dir.mkdir(parents=True, exist_ok=True)
            global_dir_str = str(global_dir)
            sid = _loguru_logger.add(
                _make_global_sink(global_dir_str),
                level=level,
                enqueue=True,
                catch=True,
            )
            _SINK_IDS.append(sid)

        if "task_file" in transports or "file" in transports:
            sid = _loguru_logger.add(
                _per_task_sink,
                level=level,
                filter=_per_task_filter,
                enqueue=True,
                catch=True,
            )
            _SINK_IDS.append(sid)

        _INITIALISED = True


def shutdown_logging() -> None:
    """Remove all sinks installed by :func:`setup_logging`.

    Safe to call multiple times; primarily used by tests.
    """
    global _INITIALISED
    with _LOCK:
        _remove_managed_sinks()
        _INITIALISED = False


def _remove_managed_sinks() -> None:
    while _SINK_IDS:
        sid = _SINK_IDS.pop()
        try:
            _loguru_logger.remove(sid)
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# Public: per-task log directory registration
# ---------------------------------------------------------------------------


def set_task_dir(task_id: str, task_dir: Path | str) -> None:
    """Tell the bus where to write per-task log files for ``task_id``.

    Called by the workflow layer once
    :func:`workflows.environment.prepare_workflow_environment` has
    decided on the task workspace. Subsequent loguru records carrying
    that ``task_id`` will be tee'd to
    ``<task_dir>/logs/system.jsonl``.
    """
    if not task_id:
        return
    p = Path(task_dir).expanduser().resolve()
    _TASK_DIRS[task_id] = p


# Compat alias used by callers that prefer "register" wording.
register_task_dir = set_task_dir


def _resolve_task_dir(task_id: str | None) -> Path | None:
    if not task_id:
        return None
    return _TASK_DIRS.get(task_id)


# ---------------------------------------------------------------------------
# Public: structured LLM / MCP records
# ---------------------------------------------------------------------------


def log_llm_call(
    *,
    provider: str,
    model: str,
    phase: str | None = None,
    duration_ms: int = 0,
    status: str = "ok",
    finish_reason: str | None = None,
    usage: dict[str, int] | None = None,
    request: Any = None,
    response: Any = None,
    reasoning: Any = None,
    tool_calls: list[dict[str, Any]] | None = None,
    error: str | None = None,
    endpoint_host: str | None = None,
    endpoint_class: str | None = None,
    request_nonce: str | None = None,
    response_sha256: str | None = None,
    tool_calls_sha256: str | None = None,
) -> None:
    """Append an :class:`LLMLogRecord` to the active task's ``llm.jsonl``.

    Falls back to the global ``logs/llm.jsonl`` when no task is bound
    (e.g. process-startup probes).

    The extra keyword arguments are the transparency-log fields. They are
    optional so every existing caller keeps working unchanged; callers that
    know the serving endpoint should pass ``endpoint_host`` /
    ``endpoint_class``, which is what turns the log into a credential-exposure
    report rather than a wall of provider names.
    """
    # Digest the FULL response and tool calls before truncation. Hashing the
    # previews instead would let a malicious router rewrite everything past
    # the 2000-char cap without changing a single forensic fingerprint.
    if response_sha256 is None:
        response_sha256 = sha256_hex(response)
    if tool_calls_sha256 is None:
        tool_calls_sha256 = sha256_hex(tool_calls)
    record = LLMLogRecord.make(
        task_id=current_task_id(),
        session_id=current_session_id(),
        provider=provider,
        model=model,
        phase=phase,
        duration_ms=duration_ms,
        status=status,
        finish_reason=finish_reason,
        usage=usage,
        request_preview=truncate(request, _LLM_PREVIEW_CHARS),
        response_preview=truncate(response, _LLM_PREVIEW_CHARS),
        reasoning_preview=truncate(reasoning, _LLM_PREVIEW_CHARS),
        tool_calls=tool_calls,
        error=truncate(error, _LLM_PREVIEW_CHARS),
        endpoint_host=endpoint_host,
        endpoint_class=endpoint_class,
        request_nonce=request_nonce,
        response_sha256=response_sha256,
        tool_calls_sha256=tool_calls_sha256,
    )
    _append_llm_record(_resolve_channel_path(record.task_id, "llm.jsonl"), record)


def log_mcp_call(
    *,
    server: str,
    tool: str,
    duration_ms: int = 0,
    status: str = "ok",
    arguments: Any = None,
    result: Any = None,
    error: str | None = None,
) -> None:
    """Append an :class:`MCPLogRecord` to the active task's ``mcp.jsonl``."""
    record = MCPLogRecord.make(
        task_id=current_task_id(),
        session_id=current_session_id(),
        server=server,
        tool=tool,
        duration_ms=duration_ms,
        status=status,
        arguments_preview=truncate(arguments, _MCP_PREVIEW_CHARS),
        result_preview=truncate(result, _MCP_PREVIEW_CHARS),
        error=truncate(error, _MCP_PREVIEW_CHARS),
    )
    _write_jsonl(_resolve_channel_path(record.task_id, "mcp.jsonl"), record.to_jsonl())


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _inject_context(record: dict[str, Any]) -> None:
    """Loguru patcher: enrich each record with task_id / session_id.

    Used as the global ``logger.configure(patcher=...)`` so business
    code stays untouched.
    """
    extra = record.setdefault("extra", {})
    extra.setdefault("task_id", current_task_id())
    extra.setdefault("session_id", current_session_id())


def _console_format(record: dict[str, Any]) -> str:
    """Human-readable console line, with task_id when present."""
    task_id = record.get("extra", {}).get("task_id")
    tag = f"[task={task_id[:8]}] " if task_id else ""
    return (
        "<green>{time:HH:mm:ss}</green> | "
        f"<level>{{level: <8}}</level> | "
        f"{tag}<cyan>{{name}}</cyan>:<cyan>{{line}}</cyan> - "
        "<level>{message}</level>\n"
    )


def _serialize_record(record: dict[str, Any]) -> dict[str, Any]:
    """Build a SystemLogRecord-shaped payload from a loguru record.

    Pure function — never mutates ``record``. Both the global file sink
    and the per-task sink use this to keep their schemas in sync.
    """
    extra = record.get("extra", {}) or {}
    payload: dict[str, Any] = {
        "timestamp": record["time"].isoformat()
        if record.get("time")
        else datetime.now(UTC).isoformat(),
        "level": record["level"].name if record.get("level") else "INFO",
        "logger": record.get("name") or "",
        "function": record.get("function") or "",
        "line": record.get("line") or 0,
        "message": record.get("message") or "",
    }
    task_id = extra.get("task_id")
    if task_id:
        payload["task_id"] = task_id
    session_id = extra.get("session_id")
    if session_id:
        payload["session_id"] = session_id
    extra_payload = {
        k: v for k, v in extra.items() if k not in {"task_id", "session_id"}
    }
    if extra_payload:
        payload["extra"] = extra_payload
    exc = record.get("exception")
    if exc is not None:
        payload["exception"] = repr(exc)
    return payload


def _make_global_sink(global_dir: str):
    """Return a loguru-compatible sink that writes JSONL with daily rotation.

    We do day-based rotation by deriving the file name from the record's
    own timestamp, which keeps the sink stateless and concurrency-safe
    across processes.
    """

    def _sink(message: Any) -> None:
        record = message.record if hasattr(message, "record") else None
        if record is None:
            return
        ts = record.get("time")
        date_segment = (
            ts.strftime("%Y%m%d") if ts else datetime.now(UTC).strftime("%Y%m%d")
        )
        path = Path(global_dir) / f"server-{date_segment}.jsonl"
        payload = _serialize_record(record)
        _write_jsonl(path, json.dumps(payload, ensure_ascii=False, default=str))

    return _sink


def _per_task_filter(record: dict[str, Any]) -> bool:
    """Only let records through that have a registered task directory."""
    task_id = record.get("extra", {}).get("task_id")
    if not task_id:
        return False
    return _resolve_task_dir(task_id) is not None


def _per_task_sink(message: Any) -> None:
    """Loguru file sink that picks the destination from the record itself."""
    record = message.record if hasattr(message, "record") else None
    if record is None:
        return
    task_id = record.get("extra", {}).get("task_id")
    task_dir = _resolve_task_dir(task_id)
    if task_dir is None:
        return
    path = task_dir / "logs" / "system.jsonl"
    payload = _serialize_record(record)
    _write_jsonl(path, json.dumps(payload, ensure_ascii=False, default=str))


def _resolve_channel_path(task_id: str | None, filename: str) -> Path:
    """Pick the JSONL output path for an LLM/MCP record."""
    task_dir = _resolve_task_dir(task_id)
    if task_dir is not None:
        return task_dir / "logs" / filename
    fallback = _GLOBAL_LOG_DIR_FALLBACK
    return fallback / filename


# ---------------------------------------------------------------------------
# Transparency-log hash chain (LLM records only)
# ---------------------------------------------------------------------------


def reset_transparency_chain() -> None:
    """Forget the cached chain tails so the next append re-reads the file.

    Needed by tests and by long-lived processes that rotate or truncate the
    log file underneath us; without it the next entry would chain onto a
    predecessor that no longer exists on disk.
    """
    with _CHAIN_LOCK:
        _CHAIN_TAIL.clear()


def _entry_hash_from_line(raw: bytes) -> str | None:
    """Return a line's ``entry_hash``, or ``None`` if the line is unusable.

    One bad line (crash mid-write, partial flush) must never stop logging,
    and must never be silently mistaken for "no chain here".
    """
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    digest = parsed.get("entry_hash")
    return digest if isinstance(digest, str) and digest else None


def _recover_tail_entry_hash(path: Path) -> str:
    """Recover ``prev_hash`` for a file we are about to append to.

    Scans *backwards* in chunks rather than reading the whole file: the line
    we want is the last one, and a transparency log can reach tens of MB. A
    corrupt or partial tail is skipped (we walk further back) instead of
    raising — logging survives it, and the verifier is the tool whose job is
    to complain about the resulting gap. Returns ``""`` for a missing, empty,
    or entirely unparseable file, which is exactly the genesis predecessor.
    """
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            pos = fh.tell()
            buffer = b""
            while pos > 0:
                read = min(_CHAIN_SCAN_CHUNK, pos)
                pos -= read
                fh.seek(pos)
                buffer = fh.read(read) + buffer
                # While unread bytes remain, buffer[0] may be a partial line;
                # hold it back for the next (earlier) chunk instead of parsing.
                lines = buffer.split(b"\n")
                tail = lines[1:] if pos > 0 else lines
                for raw in reversed(tail):
                    if not raw.strip():
                        continue
                    digest = _entry_hash_from_line(raw)
                    if digest is not None:
                        return digest
                buffer = lines[0]
            return ""
    except OSError:
        # Unreadable file: start a fresh chain rather than break logging.
        return ""


def transparency_log_enabled() -> bool:
    """Whether LLM log lines carry a hash chain. On unless disabled.

    On by default because the chain's whole value is forensic and it is worth
    nothing retroactively: an operator who discovers a suspect relay cannot go
    back and chain the sessions that already ran. The cost is two short hex
    fields per line.

    The escape hatch exists for a real limitation rather than for tidiness.
    ``_append_llm_record`` serialises threads within one process but not
    separate processes, so two processes appending to the same ``llm.jsonl``
    will legitimately fork the chain and the verifier will report a fork that
    is not an attack. A deployment that runs several writers against one log
    file should set ``DEEPCODE_TRANSPARENCY_LOG=0``, or give each writer its
    own task directory, rather than learn to ignore the verifier.
    """

    return os.environ.get("DEEPCODE_TRANSPARENCY_LOG", "").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }


def _append_llm_record(path: Path, record: LLMLogRecord) -> None:
    """Append an LLM record, extending the per-file hash chain.

    The lock spans recover + hash + write, so two threads cannot chain onto
    the same predecessor and fork the chain. It does *not* serialise separate
    processes: two processes appending concurrently will fork it, and the
    verifier will report that fork. That is the honest outcome — a claim of
    cross-process safety would need file locking we do not have.
    """
    if not transparency_log_enabled():
        _write_jsonl(path, record.to_jsonl())
        return
    try:
        key = str(path.resolve())
    except OSError:
        key = str(path)
    with _CHAIN_LOCK:
        if key not in _CHAIN_TAIL:
            _CHAIN_TAIL[key] = _recover_tail_entry_hash(path)
        record.apply_chain(_CHAIN_TAIL[key])
        if _append_chained_jsonl(path, record.to_jsonl()):
            # Only advance on a durable append: chaining past a failed write
            # would make the next entry reference a hash nobody can find.
            _CHAIN_TAIL[key] = record.entry_hash or _CHAIN_TAIL[key]


def _append_chained_jsonl(path: Path, line: str) -> bool:
    """Append one chained line, first repairing an unterminated tail.

    ``_recover_tail_entry_hash`` deliberately skips a partial line left by a
    crash, but a bare append would then glue the new entry onto it — one
    unparseable line holding two records, i.e. the *new* entry silently
    loses its verifiability. Writing the missing separator keeps the damage
    confined to the line that was already broken.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as fh:
            fh.seek(0, os.SEEK_END)
            if fh.tell() > 0:
                fh.seek(-1, os.SEEK_END)
                if fh.read(1) != b"\n":
                    fh.write(b"\n")
            fh.write(line.encode("utf-8"))
            fh.write(b"\n")
    except OSError:
        # Logging must never break the workflow. Swallow filesystem errors.
        return False
    return True


def _write_jsonl(path: Path, line: str) -> bool:
    """Append a single JSONL line to ``path``, creating parents on demand.

    Returns whether the append succeeded; callers that maintain state across
    appends (the hash chain) must not assume it did.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.write("\n")
    except OSError:
        # Logging must never break the workflow. Swallow filesystem errors.
        return False
    return True


def _resolve_global_log_dir(workspace_root: Path | None) -> Path:
    if workspace_root is None:
        return _GLOBAL_LOG_DIR_FALLBACK
    return Path(workspace_root) / _GLOBAL_LOG_DIR_FALLBACK


__all__ = [
    "log_llm_call",
    "log_mcp_call",
    "register_task_dir",
    "reset_transparency_chain",
    "set_task_dir",
    "setup_logging",
    "shutdown_logging",
]
