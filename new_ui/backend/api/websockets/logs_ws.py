"""
Logs WebSocket Handler
Provides real-time log streaming
"""

import asyncio
import json
from datetime import datetime
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from settings import PROJECT_ROOT


router = APIRouter()


@router.websocket("/logs/{session_id}")
async def logs_websocket(websocket: WebSocket, session_id: str):
    """
    WebSocket endpoint for real-time log streaming.

    Streams log entries from the logs directory.

    Message format:
    {
        "type": "log",
        "level": "INFO" | "WARNING" | "ERROR" | "DEBUG",
        "message": str,
        "namespace": str,
        "timestamp": str
    }
    """
    await websocket.accept()

    logs_dir = PROJECT_ROOT / "logs"
    last_position = 0
    current_log_file = None

    try:
        while True:
            try:
                # Find the most recent log file
                if logs_dir.exists():
                    log_files = sorted(
                        logs_dir.glob("*.jsonl"),
                        key=lambda p: p.stat().st_mtime,
                        reverse=True,
                    )

                    if log_files:
                        newest_log = log_files[0]

                        # Check if we switched to a new log file
                        if current_log_file != newest_log:
                            current_log_file = newest_log
                            last_position = 0

                        # Read new entries
                        try:
                            with open(current_log_file, "r", encoding="utf-8") as f:
                                f.seek(last_position)
                                new_lines = f.readlines()
                                last_position = f.tell()

                            for line in new_lines:
                                line = line.strip()
                                if not line:
                                    continue

                                try:
                                    log_entry = json.loads(line)
                                    await websocket.send_json(
                                        {
                                            "type": "log",
                                            "level": log_entry.get("level", "INFO"),
                                            "message": log_entry.get("message", ""),
                                            "namespace": log_entry.get("namespace", ""),
                                            "timestamp": log_entry.get(
                                                "timestamp",
                                                datetime.utcnow().isoformat(),
                                            ),
                                        }
                                    )
                                except json.JSONDecodeError:
                                    # Raw text log
                                    await websocket.send_json(
                                        {
                                            "type": "log",
                                            "level": "INFO",
                                            "message": line,
                                            "namespace": "",
                                            "timestamp": datetime.utcnow().isoformat(),
                                        }
                                    )

                        except Exception as e:
                            await websocket.send_json(
                                {
                                    "type": "error",
                                    "message": f"Error reading log file: {str(e)}",
                                    "timestamp": datetime.utcnow().isoformat(),
                                }
                            )

                # Wait before checking for more logs
                await asyncio.sleep(0.5)

            except asyncio.CancelledError:
                break

    except WebSocketDisconnect:
        pass
