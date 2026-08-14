"""Bounded background asyncio runtime owned by the application process."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
from collections.abc import Awaitable, Callable

JobFactory = Callable[[], Awaitable[None]]
logger = logging.getLogger(__name__)


class ExecutionRegistry:
    """Run background jobs outside request threads with bounded concurrency."""

    def __init__(self, *, max_concurrent: int = 2) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be positive")
        self.max_concurrent = max_concurrent
        self._lock = threading.RLock()
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self._jobs: dict[str, concurrent.futures.Future[None]] = {}
        self._close_lock = threading.Lock()
        self._closing = False
        self._closed = False
        self._shutdown_started = False
        self._shutdown_error: BaseException | None = None

    def start(
        self,
        job_id: str,
        job_factory: JobFactory,
        *,
        on_cancelled_before_start: Callable[[], None] | None = None,
    ) -> None:
        with self._lock:
            if self._closing or self._closed:
                raise RuntimeError("execution registry is closed")
            if job_id in self._jobs:
                raise ValueError(f"job is already registered: {job_id}")
            loop = self._ensure_loop_locked()
            future = asyncio.run_coroutine_threadsafe(
                self._run_bounded(job_factory, on_cancelled_before_start), loop
            )
            self._jobs[job_id] = future
            future.add_done_callback(lambda done: self._forget(job_id, done))

    def interrupt(self, job_id: str) -> bool:
        with self._lock:
            future = self._jobs.get(job_id)
            if future is None or future.done():
                return False
            return future.cancel()

    def is_active(self, job_id: str) -> bool:
        with self._lock:
            future = self._jobs.get(job_id)
            return future is not None and not future.done()

    def run_maintenance(
        self,
        job_factory: JobFactory,
        *,
        timeout: float = 5.0,
    ) -> None:
        """Run bounded cleanup on the registry's owning event loop."""

        with self._lock:
            if self._closing or self._closed:
                return
            loop = self._loop
            owner = self._thread
        if loop is None:
            return
        if owner is threading.current_thread():
            raise RuntimeError("maintenance cannot block the execution event loop")
        future = asyncio.run_coroutine_threadsafe(job_factory(), loop)
        future.result(timeout=timeout)

    def close(
        self,
        *,
        timeout: float = 5.0,
        cleanup: JobFactory | None = None,
    ) -> None:
        if timeout < 0:
            raise ValueError("timeout must not be negative")
        with self._close_lock:
            with self._lock:
                if self._closed:
                    return
                first_attempt = not self._shutdown_started
                self._shutdown_started = True
                self._closing = True
                loop = self._loop
                thread = self._thread

            if first_attempt and loop is not None:
                try:
                    self._drain(loop, cleanup=cleanup, timeout=timeout)
                except BaseException as exc:  # noqa: BLE001 - finalize before re-raise
                    self._remember_shutdown_error(exc)

            if loop is not None and thread is not None and thread.is_alive():
                try:
                    loop.call_soon_threadsafe(loop.stop)
                except BaseException as exc:  # noqa: BLE001 - preserve stop failure
                    self._remember_shutdown_error(exc)

            join_error: BaseException | None = None
            if thread is threading.current_thread():
                join_error = RuntimeError(
                    "execution registry cannot join its own runtime thread"
                )
            elif thread is not None:
                try:
                    thread.join(timeout=timeout)
                except BaseException as exc:  # noqa: BLE001 - preserve join failure
                    self._remember_shutdown_error(exc)
                if thread.is_alive():
                    join_error = TimeoutError(
                        "execution runtime did not stop before the shutdown timeout"
                    )

            if join_error is None:
                with self._lock:
                    self._loop = None
                    self._thread = None
                    self._semaphore = None
                    self._jobs.clear()
                    self._closing = False
                    self._closed = True

            shutdown_error = self._shutdown_error
            if join_error is not None:
                raise _combine_shutdown_errors(shutdown_error, join_error)
            if shutdown_error is not None:
                raise shutdown_error

    def _drain(
        self,
        loop: asyncio.AbstractEventLoop,
        *,
        cleanup: JobFactory | None,
        timeout: float,
    ) -> None:
        coroutine = self._cancel_tasks(cleanup)
        try:
            drain = asyncio.run_coroutine_threadsafe(coroutine, loop)
        except BaseException:
            coroutine.close()
            raise
        try:
            drain.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            if drain.done():
                # ``Future.result`` also relays a TimeoutError raised by the
                # cleanup itself. Read a completed Future again so that error
                # is preserved instead of being mislabeled as a wait timeout.
                drain.result()
            drain.cancel()
            raise TimeoutError(
                "execution runtime cleanup did not finish before the shutdown timeout"
            ) from exc

    def _remember_shutdown_error(self, error: BaseException) -> None:
        with self._lock:
            self._shutdown_error = _combine_shutdown_errors(
                self._shutdown_error,
                error,
            )

    async def _run_bounded(
        self,
        job_factory: JobFactory,
        on_cancelled_before_start: Callable[[], None] | None,
    ) -> None:
        assert self._semaphore is not None
        started = False
        try:
            async with self._semaphore:
                started = True
                await job_factory()
        except asyncio.CancelledError:
            if not started and on_cancelled_before_start is not None:
                on_cancelled_before_start()
            raise

    async def _cancel_tasks(self, cleanup: JobFactory | None = None) -> None:
        current = asyncio.current_task()
        tasks = [task for task in asyncio.all_tasks() if task is not current]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if cleanup is not None:
            await cleanup()

    def _ensure_loop_locked(self) -> asyncio.AbstractEventLoop:
        if self._loop is not None:
            return self._loop
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="deepcode-background-runtime",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait(timeout=5.0)
        if self._loop is None:
            raise RuntimeError("failed to start execution runtime")
        return self._loop

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._semaphore = asyncio.Semaphore(self.max_concurrent)
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.close()

    def _forget(
        self,
        job_id: str,
        future: concurrent.futures.Future[None],
    ) -> None:
        if not future.cancelled():
            exception = future.exception()
            if exception is not None:
                logger.error(
                    "unhandled background runtime failure for %s",
                    job_id,
                    exc_info=(type(exception), exception, exception.__traceback__),
                )
        with self._lock:
            if self._jobs.get(job_id) is future:
                self._jobs.pop(job_id, None)


def _combine_shutdown_errors(
    first: BaseException | None,
    second: BaseException,
) -> BaseException:
    if first is None:
        return second
    if isinstance(first, BaseExceptionGroup):
        return BaseExceptionGroup(
            "execution registry shutdown failed",
            [*first.exceptions, second],
        )
    return BaseExceptionGroup(
        "execution registry shutdown failed",
        [first, second],
    )
