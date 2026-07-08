"""Agent chat REST API — session lifecycle for the conversation UI.

The WebSocket (``/ws/agent/{id}``) carries the live event stream; these
endpoints carry everything around it: create a chat, list the sidebar,
fetch a transcript when switching chats. Additive — the legacy workflow
routes are untouched.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.agent_chat_service import agent_chat_service

router = APIRouter()


class ChatCreateRequest(BaseModel):
    title: str = ""
    model: str | None = None
    workspace: str | None = Field(
        default=None,
        description="Optional explicit workspace dir; default is a per-chat "
        "directory under deepcode_lab/chats/.",
    )


@router.post("")
async def create_chat(request: ChatCreateRequest):
    return agent_chat_service.create_chat(
        title=request.title, model=request.model, workspace=request.workspace
    )


@router.get("")
async def list_chats(limit: int = 50):
    return {"chats": agent_chat_service.list_chats(limit=limit)}


@router.get("/{session_id}/messages")
async def chat_messages(session_id: str):
    try:
        return {"messages": agent_chat_service.transcript(session_id)}
    except KeyError:
        raise HTTPException(status_code=404, detail="session not found")


class ChatRenameRequest(BaseModel):
    title: str


@router.patch("/{session_id}")
async def rename_chat(session_id: str, request: ChatRenameRequest):
    if not agent_chat_service.rename_chat(session_id, request.title):
        raise HTTPException(status_code=404, detail="session not found")
    return {"session_id": session_id, "title": request.title.strip()}


@router.delete("/{session_id}")
async def delete_chat(session_id: str):
    if not agent_chat_service.delete_chat(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    return {"deleted": session_id}
