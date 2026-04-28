"""Real-time log streaming WebSockets.

Two routes:

* ``/ws/tasks/{task_id}/logs`` — tail the JSONL files inside the task's
  log directory. Optional ``?channel=`` filter (``system`` /
  ``llm`` / ``mcp``); defaults to all channels merged.
* ``/ws/sessions/{session_id}/logs`` — convenience aggregator that
  follows every task currently attached to the session.

The legacy ``/ws/logs/{session_id}`` endpoint that silently tailed the
global log directory is gone — it was a dead parameter.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services.session_service import session_store


router = APIRouter()


_DEFAULT_CHANNELS = ("system", "llm", "mcp")


def _channel_path(task_dir: Path, channel: str) -> Path:
    return task_dir / "logs" / f"{channel}.jsonl"


async def _tail_jsonl(
    path: Path,
    channel: str,
    *,
    poll_interval: float = 0.5,
) -> AsyncIterator[dict]:
    """Yield new JSON entries as they are appended to ``path``.

    Tolerates the file not existing yet (it gets created lazily by
    the workflow). Each yielded entry is a dict already enriched with
    ``channel`` so the multiplexer can label streams.
    """
    last_position = 0
    while True:
        try:
            if path.exists():
                with path.open("r", encoding="utf-8") as fh:
                    fh.seek(last_position)
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            entry = {"raw": line, "level": "INFO"}
                        entry["channel"] = channel
                        yield entry
                    last_position = fh.tell()
        except OSError:
            pass
        await asyncio.sleep(poll_interval)


async def _multiplex(
    websocket: WebSocket,
    sources: list[tuple[Path, str]],
):
    """Fan-in multiple JSONL tails to a single WebSocket."""
    queue: asyncio.Queue[dict] = asyncio.Queue()
    stop_event = asyncio.Event()

    async def feeder(path: Path, channel: str):
        try:
            async for entry in _tail_jsonl(path, channel):
                if stop_event.is_set():
                    return
                await queue.put(entry)
        except asyncio.CancelledError:
            raise

    feeders = [asyncio.create_task(feeder(p, c)) for p, c in sources]

    try:
        while True:
            try:
                entry = await asyncio.wait_for(queue.get(), timeout=30.0)
                await websocket.send_json(entry)
            except asyncio.TimeoutError:
                # Heartbeat keeps proxies (nginx etc.) from dropping idle WS.
                await websocket.send_json(
                    {
                        "type": "heartbeat",
                        "timestamp": datetime.utcnow().isoformat(),
                    }
                )
    except WebSocketDisconnect:
        pass
    finally:
        stop_event.set()
        for fut in feeders:
            fut.cancel()
        await asyncio.gather(*feeders, return_exceptions=True)


@router.websocket("/tasks/{task_id}/logs")
async def task_logs_websocket(
    websocket: WebSocket,
    task_id: str,
    channel: str | None = None,
):
    """Tail one task's log directory. Pass ``?channel=llm`` to filter."""
    await websocket.accept()

    # Resolve task_dir via the session store: task_id here is the short
    # 8-char id used in the on-disk task directory naming.
    target = _resolve_task_dir(task_id)
    if target is None:
        await websocket.send_json(
            {
                "type": "error",
                "message": f"task_id '{task_id}' has no known log directory",
            }
        )
        await websocket.close()
        return

    channels = (
        [channel] if channel and channel in _DEFAULT_CHANNELS else list(_DEFAULT_CHANNELS)
    )
    sources = [(_channel_path(target, c), c) for c in channels]
    await _multiplex(websocket, sources)


@router.websocket("/sessions/{session_id}/logs")
async def session_logs_websocket(websocket: WebSocket, session_id: str):
    """Tail every task currently attached to a session, merged."""
    await websocket.accept()

    session = session_store.get_session(session_id)
    if session is None:
        await websocket.send_json(
            {"type": "error", "message": f"session '{session_id}' not found"}
        )
        await websocket.close()
        return

    sources: list[tuple[Path, str]] = []
    for task in session.tasks:
        if not task.task_dir:
            continue
        td = Path(task.task_dir)
        for c in _DEFAULT_CHANNELS:
            sources.append((_channel_path(td, c), f"{task.task_id}/{c}"))

    if not sources:
        await websocket.send_json(
            {
                "type": "info",
                "message": f"session '{session_id}' has no attached tasks yet",
            }
        )
        # Still keep the WS open so the client can reconnect when a task
        # is added; for now just heartbeat until disconnect.
        try:
            while True:
                await asyncio.sleep(15.0)
                await websocket.send_json(
                    {
                        "type": "heartbeat",
                        "timestamp": datetime.utcnow().isoformat(),
                    }
                )
        except WebSocketDisconnect:
            return

    await _multiplex(websocket, sources)


def _resolve_task_dir(task_id: str) -> Path | None:
    """Find a task_dir by short ``task_id`` via the session store."""
    session = session_store.find_session_by_task(task_id)
    if session is None:
        return None
    for task in session.tasks:
        if task.task_id == task_id and task.task_dir:
            return Path(task.task_dir)
    return None
