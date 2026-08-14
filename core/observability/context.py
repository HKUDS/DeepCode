"""Async-safe context vars for the active task / session.

The workflow layer calls :func:`bind_task` (and optionally
:func:`set_session`) when a workflow starts; from then on any code on
the same async task — including deeply nested helpers, agents, MCP
tool calls — can recover the IDs without having to thread them through
every function signature.

This is the single hook that lets ``observability.bus`` enrich every
``loguru`` record and route per-task logs to the correct directory
without touching business code.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Iterator


_task_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "deepcode_task_id", default=None
)
_session_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "deepcode_session_id", default=None
)


def bind_task(task_id: str | None) -> contextvars.Token:
    """Bind the current async context to ``task_id``.

    Returns the token so the caller can :func:`pop_task` later. Most
    call sites use the :func:`task_scope` context manager instead.
    """
    return _task_id_var.set(task_id)


def pop_task(token: contextvars.Token) -> None:
    """Restore the previous ``task_id`` saved in ``token``."""
    _task_id_var.reset(token)


def set_session(session_id: str | None) -> contextvars.Token:
    """Bind the current async context to ``session_id``."""
    return _session_id_var.set(session_id)


def pop_session(token: contextvars.Token) -> None:
    _session_id_var.reset(token)


def current_task_id() -> str | None:
    """Return the active ``task_id`` for the current async context."""
    return _task_id_var.get()


def current_session_id() -> str | None:
    """Return the active ``session_id`` for the current async context."""
    return _session_id_var.get()


@contextmanager
def task_scope(task_id: str | None, session_id: str | None = None) -> Iterator[None]:
    """Context manager that binds task_id (and optionally session_id).

    Restores both IDs on exit, even when the inner block raises.
    """
    task_token = bind_task(task_id)
    session_token = set_session(session_id) if session_id is not None else None
    try:
        yield
    finally:
        if session_token is not None:
            pop_session(session_token)
        pop_task(task_token)


__all__ = [
    "bind_task",
    "current_session_id",
    "current_task_id",
    "pop_session",
    "pop_task",
    "set_session",
    "task_scope",
]
