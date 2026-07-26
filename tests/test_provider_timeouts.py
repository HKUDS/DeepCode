from __future__ import annotations

import asyncio

import pytest

from core.providers.timeouts import (
    DEFAULT_LLM_REQUEST_TIMEOUT_S,
    DEFAULT_STREAM_IDLE_TIMEOUT_S,
    StreamIdleTimeoutError,
    iter_with_stream_idle_timeout,
    resolve_request_timeout_s,
    resolve_stream_idle_timeout_s,
    resolve_stream_max_runtime_s,
)


def test_timeout_policy_separates_regular_requests_from_active_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "DEEPCODE_LLM_TIMEOUT_S",
        "NANOBOT_LLM_TIMEOUT_S",
        "DEEPCODE_LLM_STREAM_MAX_RUNTIME_S",
        "NANOBOT_LLM_STREAM_MAX_RUNTIME_S",
        "DEEPCODE_STREAM_IDLE_TIMEOUT_S",
        "NANOBOT_STREAM_IDLE_TIMEOUT_S",
    ):
        monkeypatch.delenv(name, raising=False)

    assert resolve_request_timeout_s() == DEFAULT_LLM_REQUEST_TIMEOUT_S
    assert resolve_stream_max_runtime_s() is None
    assert resolve_stream_idle_timeout_s() == DEFAULT_STREAM_IDLE_TIMEOUT_S

    monkeypatch.setenv("DEEPCODE_LLM_TIMEOUT_S", "0.01")
    assert resolve_request_timeout_s() == 0.01
    assert resolve_stream_max_runtime_s() is None

    monkeypatch.setenv("DEEPCODE_LLM_STREAM_MAX_RUNTIME_S", "15")
    assert resolve_stream_max_runtime_s() == 15
    assert resolve_stream_max_runtime_s(2.5) == 2.5


@pytest.mark.asyncio
async def test_stream_idle_deadline_renews_after_every_event() -> None:
    async def active_stream():
        for value in range(4):
            await asyncio.sleep(0.006)
            yield value

    values = [
        value
        async for value in iter_with_stream_idle_timeout(
            active_stream(), timeout_s=0.015
        )
    ]

    assert values == [0, 1, 2, 3]


@pytest.mark.asyncio
async def test_stream_idle_deadline_fails_a_genuinely_stalled_stream() -> None:
    async def stalled_stream():
        yield "started"
        await asyncio.sleep(0.03)
        yield "too late"

    with pytest.raises(StreamIdleTimeoutError):
        _ = [
            value
            async for value in iter_with_stream_idle_timeout(
                stalled_stream(), timeout_s=0.01
            )
        ]
