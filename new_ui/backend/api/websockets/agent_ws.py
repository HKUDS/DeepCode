"""Agent chat WebSocket — the SQ/EQ stream over the wire (P2, L5).

``/ws/agent/{session_id}`` is a thin bidirectional pipe:

- client → server: ``{"type": "user_input", "text": ...}`` submits a turn;
  ``{"type": "interrupt"}`` aborts the active one.
- server → client: every kernel event, exactly as ``serialize_event`` emits
  it (``turn_started`` / ``agent_message_delta`` / ``tool_started`` /
  ``tool_completed`` / ``agent_message`` / ``error`` / ``task_complete``),
  so the browser renders the same stream the terminal renderer does.

The receive loop stays live *during* a turn (events are forwarded from a
separate task), which is what makes interrupt work mid-turn.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services.agent_chat_service import agent_chat_service

router = APIRouter()


async def _forward_turn(websocket: WebSocket, session_id: str, text: str) -> None:
    """Stream one turn's events to the client; errors become error frames."""
    try:
        async for event in agent_chat_service.run_turn(session_id, text):
            await websocket.send_json(event)
    except WebSocketDisconnect:
        raise
    except Exception as exc:  # noqa: BLE001 - surface, never crash the socket
        try:
            await websocket.send_json(
                {"id": "0", "msg": {"type": "error", "message": str(exc)}}
            )
            await websocket.send_json(
                {
                    "id": "0",
                    "msg": {
                        "type": "task_complete",
                        "final_text": None,
                        "stop_reason": "error",
                    },
                }
            )
        except Exception:  # noqa: BLE001
            pass


@router.websocket("/agent/{session_id}")
async def agent_websocket(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    try:
        agent_chat_service.get_agent(session_id)  # revive early, fail early
    except KeyError:
        await websocket.send_json(
            {"id": "0", "msg": {"type": "error", "message": "unknown session"}}
        )
        await websocket.close(code=4404)
        return

    turn_task: asyncio.Task | None = None
    try:
        while True:
            data = await websocket.receive_json()
            kind = data.get("type")
            if kind == "user_input":
                text = str(data.get("text") or "").strip()
                if not text:
                    continue
                if turn_task is not None and not turn_task.done():
                    await websocket.send_json(
                        {
                            "id": "0",
                            "msg": {
                                "type": "error",
                                "message": "a turn is already in progress",
                            },
                        }
                    )
                    continue
                turn_task = asyncio.create_task(
                    _forward_turn(websocket, session_id, text)
                )
            elif kind == "interrupt":
                await agent_chat_service.interrupt(session_id)
    except WebSocketDisconnect:
        pass
    finally:
        if turn_task is not None and not turn_task.done():
            turn_task.cancel()
