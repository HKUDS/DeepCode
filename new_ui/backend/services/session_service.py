"""
Session Service
Manages user sessions and conversation history
"""

import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field


@dataclass
class Session:
    """Represents a user session"""

    session_id: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_activity: datetime = field(default_factory=datetime.utcnow)
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    active_tasks: List[str] = field(default_factory=list)
    preferences: Dict[str, Any] = field(default_factory=dict)


class SessionService:
    """Service for managing user sessions"""

    def __init__(self, timeout_minutes: int = 60):
        self._sessions: Dict[str, Session] = {}
        self._timeout = timedelta(minutes=timeout_minutes)

    def create_session(self) -> Session:
        """Create a new session"""
        session_id = str(uuid.uuid4())
        session = Session(session_id=session_id)
        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        """Get session by ID"""
        session = self._sessions.get(session_id)
        if session:
            # Check if session has expired
            if datetime.utcnow() - session.last_activity > self._timeout:
                self.delete_session(session_id)
                return None
            session.last_activity = datetime.utcnow()
        return session

    def delete_session(self, session_id: str):
        """Delete a session"""
        if session_id in self._sessions:
            del self._sessions[session_id]

    def add_to_history(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Add a message to conversation history"""
        session = self.get_session(session_id)
        if session:
            session.conversation_history.append(
                {
                    "role": role,
                    "content": content,
                    "timestamp": datetime.utcnow().isoformat(),
                    "metadata": metadata or {},
                }
            )

    def get_history(self, session_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Get conversation history for a session"""
        session = self.get_session(session_id)
        if session:
            return session.conversation_history[-limit:]
        return []

    def add_active_task(self, session_id: str, task_id: str):
        """Add an active task to the session"""
        session = self.get_session(session_id)
        if session and task_id not in session.active_tasks:
            session.active_tasks.append(task_id)

    def remove_active_task(self, session_id: str, task_id: str):
        """Remove an active task from the session"""
        session = self.get_session(session_id)
        if session and task_id in session.active_tasks:
            session.active_tasks.remove(task_id)

    def update_preferences(self, session_id: str, preferences: Dict[str, Any]):
        """Update session preferences"""
        session = self.get_session(session_id)
        if session:
            session.preferences.update(preferences)

    def cleanup_expired_sessions(self):
        """Remove all expired sessions"""
        now = datetime.utcnow()
        expired = [
            sid
            for sid, session in self._sessions.items()
            if now - session.last_activity > self._timeout
        ]
        for sid in expired:
            del self._sessions[sid]


# Global service instance
session_service = SessionService()
