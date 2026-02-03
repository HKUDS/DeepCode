"""
Sidebar mission feed utilities.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, Dict, Any

import streamlit as st


def _init_event_store():
    if "sidebar_events" not in st.session_state:
        st.session_state.sidebar_events = []


def log_sidebar_event(
    stage: str,
    message: str,
    level: str = "info",
    extra: Optional[Dict[str, Any]] = None,
):
    """
    Record a sidebar feed event for live mission status display.
    Thread-safe: if called from background thread, just log to Python logger instead.
    """
    try:
        # Check if we're in a Streamlit context
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        if get_script_run_ctx() is None:
            # Running in background thread, just use Python logging
            import logging

            logging.info(f"[{stage}] {message}")
            return

        _init_event_store()
        events = list(st.session_state.sidebar_events)
        events.append(
            {
                "timestamp": datetime.utcnow().strftime("%H:%M:%S"),
                "stage": stage.upper(),
                "message": message,
                "level": level,
                "extra": extra or {},
            }
        )
        st.session_state.sidebar_events = events[-80:]
    except Exception:
        # Fallback to Python logging
        import logging

        logging.info(f"[{stage}] {message}")


class SidebarLogHandler(logging.Handler):
    """Forward Python logging records to the sidebar mission feed."""

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            stage = getattr(record, "stage", record.name.split(".")[-1]).upper()
            level = record.levelname.lower()
            payload = {
                "logger": record.name,
                "level": record.levelname,
            }
            if record.exc_info:
                payload["exception"] = self.formatException(record.exc_info)
            log_sidebar_event(stage, msg, level=level, extra=payload)
        except Exception:
            pass


def ensure_sidebar_logging():
    """
    Attach sidebar logging handler once per session to bridge backend logs.
    """
    if st.session_state.get("_sidebar_logging_attached"):
        return

    handler = SidebarLogHandler()
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)

    logging.getLogger().addHandler(handler)
    st.session_state._sidebar_logging_attached = True
