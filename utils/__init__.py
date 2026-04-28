"""Utility helpers for the DeepCode pipeline.

The legacy ``dialogue_logger`` and ``simple_llm_logger`` modules have
been removed; structured logging now lives in
:mod:`core.observability` (per-task ``system.jsonl`` / ``llm.jsonl`` /
``mcp.jsonl`` files) which is wired up automatically at process start.
"""

from .file_processor import FileProcessor

__all__ = [
    "FileProcessor",
]
