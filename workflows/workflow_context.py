"""Single-source-of-truth context object for the multi-agent research pipeline.

Replaces the ad-hoc combination of ``input_source: str`` / ``download_result:
str (JSON)`` / ``dir_info: dict[str, Any]`` that the pipeline used to thread
through 11 phases. A :class:`WorkflowContext` is built once by
:func:`workflows.environment.prepare_workflow_environment` (the unified
Phase 0+1) and consumed by every subsequent phase.

Design notes:

* Pure data + derived path properties; no I/O, no LLM, no business logic.
  All side-effects live in :mod:`workflows.environment`.
* All paths are :class:`pathlib.Path` (absolute) so downstream callers
  cannot accidentally mix the legacy ``./deepcode_lab/papers`` relative
  string with the new resolved value.
* :meth:`to_dir_info` exists solely as a transitional bridge for Phase
  4-10 which still consume the old stringly-typed dict. New code should
  read ``ctx.task_dir`` etc. directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

InputKind = Literal["pdf", "md", "docx", "txt", "html", "url"]

# Recognised file extensions and the ``InputKind`` they map to. Used by the
# environment module's normaliser; kept here because the type alias and the
# extension set are conceptually one piece of metadata.
EXTENSION_TO_KIND: dict[str, InputKind] = {
    ".pdf": "pdf",
    ".md": "md",
    ".markdown": "md",
    ".docx": "docx",
    ".doc": "docx",
    ".txt": "txt",
    ".html": "html",
    ".htm": "html",
}


TaskKind = Literal["paper2code", "chat2code", "text2web"]
"""Top-level pipeline modality. Drives task-directory naming under
``deepcode_lab/tasks/<prefix>_<task_id>/``.
"""

TASK_KIND_PREFIX: dict[TaskKind, str] = {
    "paper2code": "paper",
    "chat2code": "chat",
    "text2web": "web",
}
"""Short per-modality prefix used in the on-disk task directory name."""

TASKS_DIRNAME = "tasks"
"""Single root subdirectory that holds *all* per-task work, replacing the
legacy ``papers/`` directory which conflated every modality."""


@dataclass(slots=True)
class WorkflowContext:
    """Everything the pipeline needs to know about one task.

    Built by :func:`workflows.environment.prepare_workflow_environment` and
    mutated by Phase 2/3 to fill in ``paper_path`` /
    ``standardized_text``. After Phase 3 it is effectively immutable.
    """

    task_id: str
    """Short hex string identifying this run (UUID8 by default)."""

    input_source: str
    """Normalised input: absolute local path or ``http(s)://`` URL."""

    input_kind: InputKind
    """Detected input flavour, used to drive Phase 2 routing."""

    workspace_root: Path
    """Resolved workspace root (env > yaml > ``cwd/deepcode_lab``)."""

    task_dir: Path
    """Per-task directory: ``workspace_root / "tasks" / "<prefix>_<task_id>"``."""

    enable_indexing: bool
    """Forwarded from the entry-point; controls Phase 6/7/8 skip logic."""

    task_kind: TaskKind = "paper2code"
    """Pipeline modality. Decides the on-disk prefix (``paper_``, ``chat_``,
    ``web_``) under ``tasks/`` so users can tell at a glance which kind of
    task created each directory."""

    skip_research_analysis: bool = False
    """Set when the input already lives inside ``workspace_root/tasks/``."""

    paper_path: Path | None = None
    """The original PDF/MD/etc. file inside ``task_dir`` (filled by Phase 2)."""

    paper_md_path: Path | None = None
    """The markdown rendition of ``paper_path`` (filled by Phase 2/3)."""

    standardized_text: str | None = None
    """Phase 3 output: the parsed-and-normalised paper body."""

    # --- derived paths -------------------------------------------------

    @property
    def reference_path(self) -> Path:
        return self.task_dir / "reference.txt"

    @property
    def initial_plan_path(self) -> Path:
        return self.task_dir / "initial_plan.txt"

    @property
    def download_path(self) -> Path:
        return self.task_dir / "github_download.txt"

    @property
    def index_report_path(self) -> Path:
        return self.task_dir / "codebase_index_report.txt"

    @property
    def implementation_report_path(self) -> Path:
        return self.task_dir / "code_implementation_report.txt"

    # --- legacy bridge -------------------------------------------------

    def to_dir_info(self) -> dict[str, Any]:
        """Build the legacy ``dir_info`` dict consumed by Phase 4-10.

        Keys mirror the contract previously produced by
        ``synthesize_workspace_infrastructure_agent``. Values are stringified
        because downstream phases concatenate them into prompts and pass
        them to MCP filesystem tools that expect ``str``.
        """
        return {
            "paper_dir": str(self.task_dir),
            "standardized_text": self.standardized_text,
            "reference_path": str(self.reference_path),
            "initial_plan_path": str(self.initial_plan_path),
            "download_path": str(self.download_path),
            "index_report_path": str(self.index_report_path),
            "implementation_report_path": str(self.implementation_report_path),
            "workspace_dir": str(self.workspace_root),
        }


def resolve_workspace_root(yaml_root: str | None) -> Path:
    """Decide where ``deepcode_lab`` should live.

    Priority (highest first):

    1. ``DEEPCODE_WORKSPACE`` environment variable.
    2. ``workspace.root`` in ``mcp_agent.config.yaml`` (passed in as
       ``yaml_root``; ``None`` if absent).
    3. ``<cwd>/deepcode_lab`` as a sane default that matches existing
       behaviour for users who do not customise anything.

    The returned path is always absolute and resolved.
    """
    env_value = os.environ.get("DEEPCODE_WORKSPACE")
    if env_value:
        return Path(env_value).expanduser().resolve()
    if yaml_root:
        return Path(yaml_root).expanduser().resolve()
    return (Path.cwd() / "deepcode_lab").resolve()
