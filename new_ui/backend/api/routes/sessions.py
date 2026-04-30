"""Sessions REST API.

Surfaces the persistent session store (``core.sessions``) over HTTP so
the frontend can render a Cursor-style sidebar of past chats and let
users continue / branch / delete them. The endpoints are deliberately
side-effect-free with respect to running workflows — actually starting
a new run still happens via ``/api/v1/workflows/...`` with an explicit
``session_id``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from models.requests import (
    SessionBranchRequest,
    SessionCreateRequest,
    SessionMessageRequest,
)
from services.session_service import session_store
from services.workflow_service import workflow_service


router = APIRouter()


def _serialize_message(msg) -> dict[str, Any]:
    return {
        "role": msg.role,
        "content": msg.content,
        "timestamp": msg.timestamp,
        "task_id_ref": msg.task_id_ref,
        "metadata": msg.metadata or {},
    }


def _serialize_task(task) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "task_kind": task.task_kind,
        "task_dir": task.task_dir,
        "status": task.status,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "metadata": task.metadata or {},
    }


def _serialize_session(session) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "title": session.title,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "metadata": session.metadata or {},
        "messages": [_serialize_message(m) for m in session.messages],
        "tasks": [_serialize_task(t) for t in session.tasks],
    }


@router.post("")
async def create_session(request: SessionCreateRequest):
    session = session_store.create_session(title=request.title)
    return _serialize_session(session)


@router.get("")
async def list_sessions(limit: int = 50, order: str = "recent"):
    summaries = session_store.list_sessions(limit=limit, order=order)
    return {"sessions": [s.to_dict() for s in summaries]}


@router.get("/{session_id}")
async def get_session(session_id: str):
    session = session_store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return _serialize_session(session)


@router.delete("/{session_id}")
async def delete_session(session_id: str):
    report = workflow_service.delete_session_cascade(session_id)
    if report["status"] == "not_found":
        raise HTTPException(status_code=404, detail="Session not found")
    if report["status"] == "blocked":
        raise HTTPException(status_code=409, detail=report)
    return report


@router.post("/{session_id}/messages")
async def append_message(session_id: str, request: SessionMessageRequest):
    msg = session_store.append_message(
        session_id, role=request.role, content=request.content
    )
    if msg is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return _serialize_message(msg)


@router.post("/{session_id}/branch")
async def branch_session(session_id: str, request: SessionBranchRequest):
    forked = session_store.branch_session(
        session_id,
        from_message_index=request.from_message_index,
        title=request.title,
    )
    if forked is None:
        raise HTTPException(status_code=404, detail="Source session not found")
    return _serialize_session(forked)


@router.get("/{session_id}/tasks")
async def list_session_tasks(session_id: str):
    session = session_store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"tasks": [_serialize_task(t) for t in session.tasks]}
