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
    except TimeoutError:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    if process.returncode is None:
        await process.wait()


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
