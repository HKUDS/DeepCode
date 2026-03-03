"""
Code Stream WebSocket Handler
Provides real-time streaming of generated code
"""

import asyncio
from datetime import datetime
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services.workflow_service import workflow_service


router = APIRouter()


@router.websocket("/code-stream/{task_id}")
async def code_stream_websocket(websocket: WebSocket, task_id: str):
    """
    WebSocket endpoint for real-time code streaming.

    Streams generated code as it's being written, similar to ChatGPT.

    Message format:
    {
        "type": "code_chunk" | "file_start" | "file_end" | "complete",
        "task_id": str,
        "content": str,  # Code content for code_chunk
        "filename": str | null,  # For file_start/file_end
        "timestamp": str
    }
    """
    await websocket.accept()

    task = workflow_service.get_task(task_id)
    # Subscribe to get our own queue for this task
    queue = workflow_service.subscribe(task_id)

    if not task:
        await websocket.send_json(
            {
                "type": "error",
                "task_id": task_id,
                "error": "Task not found",
                "timestamp": datetime.utcnow().isoformat(),
            }
        )
        await websocket.close()
        return

    try:
        # Track current file being streamed
        current_file = None

        if queue:
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=60.0)

                    # Transform progress messages into code stream format
                    if message.get("type") == "progress":
                        msg_text = message.get("message", "")

                        # Detect file creation events
                        if "Creating file:" in msg_text or "Writing:" in msg_text:
                            filename = msg_text.split(":")[-1].strip()
                            if current_file:
                                await websocket.send_json(
                                    {
                                        "type": "file_end",
                                        "task_id": task_id,
                                        "filename": current_file,
                                        "timestamp": datetime.utcnow().isoformat(),
                                    }
                                )
                            current_file = filename
                            await websocket.send_json(
                                {
                                    "type": "file_start",
                                    "task_id": task_id,
                                    "filename": filename,
                                    "timestamp": datetime.utcnow().isoformat(),
                                }
                            )

                        # Forward progress message
                        await websocket.send_json(
                            {
                                "type": "progress",
                                "task_id": task_id,
                                "progress": message.get("progress", 0),
                                "message": msg_text,
                                "timestamp": datetime.utcnow().isoformat(),
                            }
                        )

                    elif message.get("type") == "code_chunk":
                        # Direct code chunk forwarding
                        await websocket.send_json(
                            {
                                "type": "code_chunk",
                                "task_id": task_id,
                                "content": message.get("content", ""),
                                "filename": message.get("filename"),
                                "timestamp": datetime.utcnow().isoformat(),
                            }
                        )

                    elif message.get("type") in ("complete", "error"):
                        msg_type = message.get("type")
                        print(
                            f"[CodeStreamWS] Workflow finished: task={task_id[:8]}... type={msg_type}"
                        )
                        if current_file:
                            await websocket.send_json(
                                {
                                    "type": "file_end",
                                    "task_id": task_id,
                                    "filename": current_file,
                                    "timestamp": datetime.utcnow().isoformat(),
                                }
                            )
                        await websocket.send_json(message)
                        # Wait a bit before closing to ensure frontend processes the message
                        await asyncio.sleep(0.5)
                        await websocket.close()
                        break

                except asyncio.TimeoutError:
                    await websocket.send_json(
                        {
                            "type": "heartbeat",
                            "task_id": task_id,
                            "timestamp": datetime.utcnow().isoformat(),
                        }
                    )

    except WebSocketDisconnect:
        pass
    finally:
        # Unsubscribe from task updates
        if queue:
            workflow_service.unsubscribe(task_id, queue)
