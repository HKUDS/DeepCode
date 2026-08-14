"""Model-neutral timeout policy for LLM requests and streaming activity."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Iterable
from typing import TypeVar

from loguru import logger


DEFAULT_LLM_REQUEST_TIMEOUT_S = 300.0
DEFAULT_STREAM_IDLE_TIMEOUT_S = 90.0

_T = TypeVar("_T")


class StreamIdleTimeoutError(TimeoutError):
    """Raised when a provider stream produces no event before its deadline."""

    def __init__(self, timeout_s: float) -> None:
        self.timeout_s = timeout_s
        super().__init__(f"stream idle for more than {timeout_s:g} seconds")


def resolve_request_timeout_s(explicit: float | None = None) -> float | None:
    """Return the non-streaming wall-clock deadline.

    ``DEEPCODE_LLM_TIMEOUT_S`` retains its historical meaning for regular
    requests. A non-positive explicit/environment value disables the limit.
    """

    if explicit is not None:
        return _normalize_optional_timeout(explicit)
    return _timeout_from_env(
        ("DEEPCODE_LLM_TIMEOUT_S", "NANOBOT_LLM_TIMEOUT_S"),
        default=DEFAULT_LLM_REQUEST_TIMEOUT_S,
        label="LLM request timeout",
        non_positive_disables=True,
    )


def resolve_stream_max_runtime_s(explicit: float | None = None) -> float | None:
    """Return an optional total runtime cap for active streams.

    Streams are bounded by inactivity rather than a short fixed wall clock.
    Operators that need an additional safety ceiling can set
    ``DEEPCODE_LLM_STREAM_MAX_RUNTIME_S``. An explicitly supplied legacy
    ``llm_timeout_s`` still acts as a hard cap for backwards compatibility.
    """

    if explicit is not None:
        return _normalize_optional_timeout(explicit)
    return _timeout_from_env(
        (
            "DEEPCODE_LLM_STREAM_MAX_RUNTIME_S",
            "NANOBOT_LLM_STREAM_MAX_RUNTIME_S",
        ),
        default=None,
        label="LLM stream maximum runtime",
        non_positive_disables=True,
    )


def resolve_stream_idle_timeout_s() -> float:
    """Return the maximum gap allowed between any two provider stream events."""

    timeout = _timeout_from_env(
        ("DEEPCODE_STREAM_IDLE_TIMEOUT_S", "NANOBOT_STREAM_IDLE_TIMEOUT_S"),
        default=DEFAULT_STREAM_IDLE_TIMEOUT_S,
        label="LLM stream idle timeout",
        non_positive_disables=False,
    )
    return timeout if timeout is not None else DEFAULT_STREAM_IDLE_TIMEOUT_S


async def wait_for_stream_activity(awaitable: Awaitable[_T], *, timeout_s: float) -> _T:
    """Wait for one stream operation and expose an unambiguous idle error."""

    try:
        return await asyncio.wait_for(awaitable, timeout=timeout_s)
    except asyncio.TimeoutError as exc:
        raise StreamIdleTimeoutError(timeout_s) from exc


async def iter_with_stream_idle_timeout(
    stream: AsyncIterator[_T], *, timeout_s: float
) -> AsyncIterator[_T]:
    """Yield every stream event, resetting the idle deadline after each one."""

    iterator = stream.__aiter__()
    while True:
        try:
            item = await wait_for_stream_activity(
                iterator.__anext__(), timeout_s=timeout_s
            )
        except StopAsyncIteration:
            return
        yield item


def _timeout_from_env(
    names: Iterable[str],
    *,
    default: float | None,
    label: str,
    non_positive_disables: bool,
) -> float | None:
    selected_name: str | None = None
    raw: str | None = None
    for name in names:
        value = os.environ.get(name)
        if value is not None:
            selected_name = name
            raw = value.strip()
            break
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid {} in {}={!r}; using {}",
            label,
            selected_name,
            raw,
            _format_default(default),
        )
        return default
    if value <= 0:
        if non_positive_disables:
            return None
        logger.warning(
            "Non-positive {} in {}={!r}; using {}",
            label,
            selected_name,
            raw,
            _format_default(default),
        )
        return default
    return value


def _normalize_optional_timeout(value: float) -> float | None:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return DEFAULT_LLM_REQUEST_TIMEOUT_S
    return normalized if normalized > 0 else None


def _format_default(value: float | None) -> str:
    return "disabled" if value is None else f"{value:g}s"


__all__ = [
    "DEFAULT_LLM_REQUEST_TIMEOUT_S",
    "DEFAULT_STREAM_IDLE_TIMEOUT_S",
    "StreamIdleTimeoutError",
    "iter_with_stream_idle_timeout",
    "resolve_request_timeout_s",
    "resolve_stream_idle_timeout_s",
    "resolve_stream_max_runtime_s",
    "wait_for_stream_activity",
]
