"""Observability layer: unified logging for DeepCode.

This module replaces the previous ad-hoc logging surface where
``loguru.logger`` ran with the default stderr sink and orphan helpers
(``utils.simple_llm_logger``, ``utils.dialogue_logger``) defined logging
machinery that nobody invoked.

Public surface (kept intentionally small):

- :func:`setup_logging` — call once at process startup. Configures loguru
  sinks per :class:`core.config.LoggerConfig` and installs a global patch
  so every ``loguru.logger`` call automatically carries the active
  ``task_id`` (looked up from a :data:`contextvars.ContextVar`).
- :func:`bind_task` / :func:`current_task_id` — set / read the active
  task id within an asyncio context.
- :func:`set_task_dir` — register where per-task log files should land
  for a given ``task_id`` (called by the workflow layer once the task
  workspace is ready).
- :func:`log_llm_call` / :func:`log_mcp_call` — append structured
  records to the per-task ``llm.jsonl`` / ``mcp.jsonl`` files.

The aim is "business code does not change a single line": existing
``from loguru import logger`` calls still work, they just get richer
structured sinks for free.
"""

from core.observability.bus import (
    log_llm_call,
    log_mcp_call,
    register_task_dir,
    set_task_dir,
    setup_logging,
    shutdown_logging,
)
from core.observability.context import (
    bind_task,
    current_session_id,
    current_task_id,
    pop_task,
    set_session,
)
from core.observability.records import (
    LLMLogRecord,
    MCPLogRecord,
    SystemLogRecord,
)

__all__ = [
    "LLMLogRecord",
    "MCPLogRecord",
    "SystemLogRecord",
    "bind_task",
    "current_session_id",
    "current_task_id",
    "log_llm_call",
    "log_mcp_call",
    "pop_task",
    "register_task_dir",
    "set_session",
    "set_task_dir",
    "setup_logging",
    "shutdown_logging",
]
