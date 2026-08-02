from __future__ import annotations

import asyncio
import threading

import pytest

from core.application.execution_registry import ExecutionRegistry


async def _wait_forever(started: threading.Event) -> None:
    started.set()
    await asyncio.Event().wait()


@pytest.mark.parametrize(
    ("error_type", "message"),
    [
        (RuntimeError, "runtime cleanup failed"),
        (ValueError, "invalid cleanup state"),
        (TimeoutError, "cleanup raised its own timeout"),
    ],
)
def test_cleanup_error_is_surfaced_after_runtime_thread_stops(
    error_type: type[Exception],
    message: str,
) -> None:
    registry = ExecutionRegistry()
    started = threading.Event()
    registry.start("job", lambda: _wait_forever(started))
    assert started.wait(timeout=2)
    runtime_thread = registry._thread
    assert runtime_thread is not None

    async def fail_cleanup() -> None:
        raise error_type(message)

    with pytest.raises(error_type, match=message):
        registry.close(cleanup=fail_cleanup)

    assert not runtime_thread.is_alive()
    assert registry._closed is True
    assert registry._closing is False
    registry.close()
    with pytest.raises(RuntimeError, match="registry is closed"):
        registry.start("late-job", lambda: _wait_forever(threading.Event()))


def test_cleanup_timeout_is_surfaced_after_forced_runtime_finalization() -> None:
    registry = ExecutionRegistry()
    job_started = threading.Event()
    cleanup_started = threading.Event()
    registry.start("job", lambda: _wait_forever(job_started))
    assert job_started.wait(timeout=2)
    runtime_thread = registry._thread
    assert runtime_thread is not None

    async def hang_cleanup() -> None:
        cleanup_started.set()
        await asyncio.Event().wait()

    with pytest.raises(
        TimeoutError,
        match="cleanup did not finish before the shutdown timeout",
    ):
        registry.close(timeout=0.02, cleanup=hang_cleanup)

    assert cleanup_started.is_set()
    assert not runtime_thread.is_alive()
    assert registry._closed is True
    registry.close()


def test_thread_join_timeout_can_be_retried_after_stop_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = ExecutionRegistry()
    started = threading.Event()
    registry.start("job", lambda: _wait_forever(started))
    assert started.wait(timeout=2)
    runtime_thread = registry._thread
    assert runtime_thread is not None
    real_join = runtime_thread.join
    real_is_alive = runtime_thread.is_alive

    with monkeypatch.context() as patch:
        patch.setattr(runtime_thread, "join", lambda timeout=None: None)
        patch.setattr(runtime_thread, "is_alive", lambda: True)
        with pytest.raises(
            TimeoutError,
            match="runtime did not stop before the shutdown timeout",
        ):
            registry.close(timeout=0.01)

    assert registry._closing is True
    assert registry._closed is False
    real_join(timeout=2)
    assert not real_is_alive()

    registry.close(timeout=1)
    assert registry._closed is True
    assert registry._closing is False
    registry.close()


def test_close_without_a_runtime_is_idempotent() -> None:
    registry = ExecutionRegistry()

    registry.close()
    registry.close()

    assert registry._closed is True
