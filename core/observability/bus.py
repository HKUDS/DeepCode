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
"""

from __future__ import annotations

import json
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger as _loguru_logger

from core.observability.context import current_session_id, current_task_id
from core.observability.records import LLMLogRecord, MCPLogRecord, truncate

if TYPE_CHECKING:
    from core.config import LoggerConfig

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


# ---------------------------------------------------------------------------
# Public: setup / shutdown
# ---------------------------------------------------------------------------


def setup_logging(
    config: "LoggerConfig | None" = None,
    *,
    workspace_root: Path | None = None,
    force: bool = False,
) -> None:
    """Wire up loguru sinks. Idempotent unless ``force=True``.

    ``config`` is a :class:`core.config.LoggerConfig` instance. When
    omitted, sensible defaults are used (level=INFO, console + global
    JSONL + per-task JSONL).

    ``workspace_root`` controls where the global log file lives (``logs/``
    is created relative to it). When omitted the current working
    directory is used.
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

        if "console" in transports or not transports:
            sid = _loguru_logger.add(
                sys.stderr,
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
) -> None:
    """Append an :class:`LLMLogRecord` to the active task's ``llm.jsonl``.

    Falls back to the global ``logs/llm.jsonl`` when no task is bound
    (e.g. process-startup probes).
    """
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
    )
    _write_jsonl(_resolve_channel_path(record.task_id, "llm.jsonl"), record.to_jsonl())


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
        else datetime.utcnow().isoformat(),
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
            ts.strftime("%Y%m%d") if ts else datetime.utcnow().strftime("%Y%m%d")
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


def _write_jsonl(path: Path, line: str) -> None:
    """Append a single JSONL line to ``path``, creating parents on demand."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.write("\n")
    except OSError:
        # Logging must never break the workflow. Swallow filesystem errors.
        pass


def _resolve_global_log_dir(workspace_root: Path | None) -> Path:
    if workspace_root is None:
        return _GLOBAL_LOG_DIR_FALLBACK
    return Path(workspace_root) / _GLOBAL_LOG_DIR_FALLBACK


__all__ = [
    "log_llm_call",
    "log_mcp_call",
    "register_task_dir",
    "set_task_dir",
    "setup_logging",
    "shutdown_logging",
]
