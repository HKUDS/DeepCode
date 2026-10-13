"""Cross-platform process-group lifecycle helpers for agent commands."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from typing import Any


def subprocess_group_kwargs() -> dict[str, Any]:
    """Put a child in a process group owned by DeepCode."""

    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


async def start_process_group(*argv: str, **kwargs: Any) -> asyncio.subprocess.Process:
    """``asyncio.create_subprocess_exec`` in a process group owned by DeepCode.

    asyncio forks before it has wired the pipes, and a cancel in between makes
    it SIGKILL the direct child only, so whatever that child had already
    started (``sleep 30 &``) lived on under init. A cancelled start is let
    finish instead, and its whole group is terminated before the cancel goes on.
    """

    starting = asyncio.create_task(
        asyncio.create_subprocess_exec(*argv, **kwargs, **subprocess_group_kwargs())
    )
    try:
        return await asyncio.shield(starting)
    except asyncio.CancelledError:
        try:
            process = await starting
        except Exception:  # noqa: BLE001 - nothing started; the cancel stands
            process = None
        if process is not None:
            await terminate_process_tree(process)
        raise


async def terminate_process_tree(
    process: asyncio.subprocess.Process,
    *,
    grace_seconds: float = 0.5,
) -> None:
    """Terminate a command and descendants, escalating after a short grace."""

    if process.returncode is not None:
        return
    if os.name == "nt":
        await _terminate_windows_tree(process)
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=grace_seconds)
    except asyncio.TimeoutError:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    if process.returncode is None:
        await process.wait()


async def run_captured(
    argv: list[str], *, timeout: float
) -> tuple[int, bytes, bytes] | None:
    """Run ``argv`` to completion: ``(returncode, stdout, stderr)``.

    ``None`` when it outlives ``timeout``. The timeout and cancellation (an
    interrupted turn) both end the process and its descendants instead of
    leaving them to run on unseen. A spawn failure raises ``OSError``.
    """

    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **subprocess_group_kwargs(),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        await terminate_process_tree(proc)
        return None
    except asyncio.CancelledError:
        await terminate_process_tree(proc)
        raise
    assert proc.returncode is not None
    return proc.returncode, stdout, stderr


async def _terminate_windows_tree(process: asyncio.subprocess.Process) -> None:
    try:
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.wait()
    except OSError:
        process.kill()
    if process.returncode is None:
        await process.wait()
