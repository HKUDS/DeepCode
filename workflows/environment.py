"""Workspace + input preparation for the multi-agent research pipeline.

This module owns all of the housekeeping that *used* to be scattered across
``agent_orchestration_engine.py`` Phase 0 (mkdir), Phase 1
(``_process_input_source``), and the head of Phase 2 (resume detection +
PDF→MD inlining):

* deciding where ``deepcode_lab`` lives (env > yaml > cwd default)
* normalising the user-supplied input string (``file://``, ``~``, URL
  decoding, Windows backslash, relative→absolute)
* validating it (existence, file size, extension whitelist) so we fail
  *before* the LLM bill starts
* detecting "user re-fed an existing paper directory" → resume mode
* allocating an isolated ``papers/<task_id>/`` directory using a UUID so
  concurrent tasks cannot collide on the old ``max+1`` scheme
* re-pointing the ``filesystem`` MCP server at the resolved workspace
  *and* ``cwd`` (the previous three entry-point patches all did this
  inline; now exactly one place owns it)

The single public entry point is :func:`prepare_workflow_environment`.
Every helper is a private ``_xxx`` so the contract stays small.
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import unquote, urlparse

from loguru import logger as default_logger

from core.compat.runtime import get_runtime
from workflows.workflow_context import (
    EXTENSION_TO_KIND,
    InputKind,
    TASK_KIND_PREFIX,
    TASKS_DIRNAME,
    TaskKind,
    WorkflowContext,
    resolve_workspace_root,
)

ProgressCallback = Callable[[int, str], Any]
"""Same shape as the legacy ``progress_callback(progress, message)`` hook."""

_DEFAULT_MAX_INPUT_MB = 100
"""Default upper bound on input file size; override via yaml ``workspace.max_input_mb``."""

_LOW_DISK_THRESHOLD_BYTES = 500 * 1024 * 1024
"""Warn (do not fail) if the workspace volume has less than 500 MB free."""


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


async def prepare_workflow_environment(
    raw_input: str,
    *,
    enable_indexing: bool,
    task_kind: TaskKind = "paper2code",
    task_id: str | None = None,
    progress_cb: ProgressCallback | None = None,
    logger: Any | None = None,
) -> WorkflowContext:
    """Run all non-LLM workspace + input housekeeping in one place.

    Returns a fully-populated :class:`WorkflowContext` ready for Phase 2.
    Raises :class:`ValueError` (with a human-readable reason) if the input
    is missing, oversized, or has a disallowed extension; caller should
    catch it and mark the task failed without spending any LLM tokens.

    The on-disk task directory is named
    ``deepcode_lab/tasks/<prefix>_<task_id>/`` where ``<prefix>`` is
    derived from ``task_kind`` (``paper`` / ``chat`` / ``web`` …). Old
    ``deepcode_lab/papers/<id>/`` directories created by previous releases
    are **not** auto-resumed; users who want to continue an old run need
    to re-submit the input.
    """
    log = logger or default_logger
    _maybe_progress(progress_cb, 1, "🔧 Resolving workspace and validating input...")

    yaml_root, max_input_mb = _load_workspace_config()
    workspace_root = resolve_workspace_root(yaml_root)

    normalized, kind = _normalize_input(raw_input)
    _validate_input(normalized, kind, max_input_mb, log)

    prefix = TASK_KIND_PREFIX[task_kind]
    existing_dir, detected_kind, is_resume = _detect_resume(normalized, workspace_root)

    if is_resume and existing_dir is not None:
        # Resume preserves the existing directory name (and therefore its
        # original modality) regardless of what the caller passed in. The
        # alternative — silently relocating an in-progress task — would
        # break MCP allowed-roots and on-disk references.
        full_dirname = existing_dir.name
        chosen_id = _strip_known_prefix(full_dirname, detected_kind)
        if detected_kind is not None and detected_kind != task_kind:
            log.info(
                "Resume: caller asked for task_kind={} but existing task_dir is {} (kind={}); "
                "honouring the on-disk kind.",
                task_kind,
                full_dirname,
                detected_kind,
            )
            task_kind = detected_kind
            prefix = TASK_KIND_PREFIX[task_kind]
        task_dir = existing_dir
    else:
        chosen_id = (task_id or uuid.uuid4().hex[:8]).strip()
        if not chosen_id:
            chosen_id = uuid.uuid4().hex[:8]
        task_dir = workspace_root / TASKS_DIRNAME / f"{prefix}_{chosen_id}"

    _ensure_workspace(workspace_root, task_dir, allow_existing=is_resume, logger=log)
    _register_workspace_for_filesystem_mcp(workspace_root, log)

    paper_path: Path | None = None
    if is_resume and kind != "url":
        candidate = Path(normalized)
        if candidate.is_file():
            paper_path = candidate

    log.info(
        "🗂️  Workspace={} task_kind={} task_dir={} kind={} resume={}",
        workspace_root,
        task_kind,
        task_dir.name,
        kind,
        is_resume,
    )

    _maybe_progress(progress_cb, 4, f"📁 Task workspace ready: {task_dir.name}")

    return WorkflowContext(
        task_id=chosen_id,
        input_source=normalized,
        input_kind=kind,
        workspace_root=workspace_root,
        task_dir=task_dir,
        enable_indexing=enable_indexing,
        task_kind=task_kind,
        skip_research_analysis=is_resume,
        paper_path=paper_path,
    )


def _strip_known_prefix(dirname: str, detected_kind: TaskKind | None) -> str:
    """Return the bare task_id portion of a ``<prefix>_<id>`` directory name.

    Falls back to the full name if no known prefix matches (e.g. user
    manually named the directory).
    """
    if detected_kind is not None:
        prefix = TASK_KIND_PREFIX[detected_kind] + "_"
        if dirname.startswith(prefix):
            return dirname[len(prefix):]
    return dirname


# ---------------------------------------------------------------------------
# private helpers (one job each)
# ---------------------------------------------------------------------------


def _maybe_progress(cb: ProgressCallback | None, pct: int, msg: str) -> None:
    """Best-effort progress notification; never raises into the caller."""
    if cb is None:
        return
    try:
        result = cb(pct, msg)
        # Accept both sync and awaitable callbacks; legacy code uses both.
        if isinstance(result, Awaitable):  # type: ignore[arg-type]
            # Schedule but do not await - caller's event loop will pick it up.
            # Fire-and-forget keeps prepare_workflow_environment cheap.
            import asyncio

            asyncio.ensure_future(result)  # noqa: RUF006
    except Exception as exc:  # pragma: no cover - cosmetic
        default_logger.debug("progress callback failed: {}", exc)


def _load_workspace_config() -> tuple[str | None, int]:
    """Read ``workspace.{root,max_input_mb}`` from the active yaml config."""
    try:
        runtime = get_runtime()
    except Exception as exc:  # pragma: no cover - defensive
        default_logger.warning("Could not load DeepCode runtime config: {}", exc)
        return None, _DEFAULT_MAX_INPUT_MB

    block = runtime.config.raw.get("workspace") or {}
    if not isinstance(block, dict):
        default_logger.warning(
            "Ignoring malformed yaml 'workspace' block (expected mapping, got {})",
            type(block).__name__,
        )
        return None, _DEFAULT_MAX_INPUT_MB

    yaml_root = block.get("root")
    yaml_root = str(yaml_root) if yaml_root else None

    max_mb_raw = block.get("max_input_mb", _DEFAULT_MAX_INPUT_MB)
    try:
        max_mb = max(1, int(max_mb_raw))
    except (TypeError, ValueError):
        default_logger.warning(
            "Invalid workspace.max_input_mb={!r}; falling back to {}MB",
            max_mb_raw,
            _DEFAULT_MAX_INPUT_MB,
        )
        max_mb = _DEFAULT_MAX_INPUT_MB
    return yaml_root, max_mb


def _normalize_input(raw_input: str) -> tuple[str, InputKind]:
    """Return ``(normalized_path_or_url, detected_kind)``.

    Normalisation:

    * trim whitespace
    * ``http(s)://`` → kind == "url" (returned verbatim, no further fs work)
    * ``file://`` decoded to a real local path
    * ``~`` expanded, relative paths resolved against ``cwd``
    * Windows: forward slashes preserved as-is, but the resulting Path is
      absolute and uses the platform separator after ``Path.resolve()``.
    """
    if not isinstance(raw_input, str) or not raw_input.strip():
        raise ValueError("input is empty")
    text = raw_input.strip()

    lower = text.lower()
    if lower.startswith(("http://", "https://")):
        return text, "url"

    if lower.startswith("file://"):
        parsed = urlparse(text)
        local = unquote(parsed.path)
        # On Windows, ``file:///C:/foo`` parses to ``/C:/foo`` -> strip the leading slash.
        if os.name == "nt" and len(local) >= 3 and local[0] == "/" and local[2] == ":":
            local = local[1:]
        text = local

    expanded = os.path.expanduser(text)
    abs_path = Path(expanded).resolve()

    suffix = abs_path.suffix.lower()
    kind = EXTENSION_TO_KIND.get(suffix)
    if kind is None:
        raise ValueError(
            f"input '{raw_input}': unsupported extension '{suffix or '(none)'}'. "
            f"Allowed: {sorted(set(EXTENSION_TO_KIND))}"
        )
    return str(abs_path), kind


def _validate_input(normalized: str, kind: InputKind, max_mb: int, log: Any) -> None:
    """Cheap, deterministic checks before any LLM call."""
    if kind == "url":
        # We *could* HEAD the URL, but many origins (incl. arXiv) reject HEAD
        # or rate-limit it; trying to verify here would create flaky failures.
        # Phase 2's downloader is the right place to surface URL errors.
        return

    path = Path(normalized)
    if not path.exists():
        raise ValueError(f"input '{normalized}': file does not exist")
    if not path.is_file():
        raise ValueError(f"input '{normalized}': is not a regular file")

    size_bytes = path.stat().st_size
    size_mb = size_bytes / (1024 * 1024)
    if size_mb > max_mb:
        raise ValueError(
            f"input '{normalized}': size {size_mb:.1f}MB exceeds limit {max_mb}MB "
            f"(raise workspace.max_input_mb in mcp_agent.config.yaml to override)"
        )

    suffix = path.suffix.lower()
    if EXTENSION_TO_KIND.get(suffix) != kind:
        raise ValueError(
            f"input '{normalized}': extension/kind mismatch ({suffix} vs {kind})"
        )

    log.debug("input validated: {} ({:.2f} MB, kind={})", path, size_mb, kind)


def _detect_resume(
    normalized: str, workspace_root: Path
) -> tuple[Path | None, TaskKind | None, bool]:
    """Detect when the user re-fed a file already inside ``tasks/<prefix>_<id>/``.

    Returns ``(existing_task_dir, detected_kind, True)`` on a hit, where
    ``detected_kind`` is inferred from the directory's prefix
    (``paper_`` → ``"paper2code"`` etc.). Returns ``(None, None, False)``
    otherwise. A resume reuses the existing ``task_id`` and skips Phase 2.

    Old-style ``deepcode_lab/papers/<id>/`` paths are intentionally **not**
    resumed — the user opted out of legacy migration, so they must
    re-submit the input to get a fresh ``tasks/paper_<uuid>/`` directory.
    """
    if normalized.startswith(("http://", "https://")):
        return None, None, False

    try:
        candidate = Path(normalized).resolve()
    except OSError:
        return None, None, False

    tasks_root = (workspace_root / TASKS_DIRNAME).resolve()
    try:
        rel = candidate.relative_to(tasks_root)
    except ValueError:
        return None, None, False

    parts = rel.parts
    if not parts:
        return None, None, False

    task_dir = tasks_root / parts[0]
    if not task_dir.is_dir():
        return None, None, False

    detected_kind: TaskKind | None = None
    for kind, prefix in TASK_KIND_PREFIX.items():
        if parts[0].startswith(prefix + "_"):
            detected_kind = kind
            break
    return task_dir, detected_kind, True


def _ensure_workspace(
    workspace_root: Path,
    task_dir: Path,
    *,
    allow_existing: bool,
    logger: Any,
) -> None:
    """Create the workspace tree and fail loudly on permission/disk issues."""
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / TASKS_DIRNAME).mkdir(parents=True, exist_ok=True)

    probe = workspace_root / ".deepcode_write_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
    except OSError as exc:
        raise ValueError(
            f"workspace '{workspace_root}' is not writable: {exc}"
        ) from exc
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass

    try:
        free_bytes = shutil.disk_usage(workspace_root).free
        if free_bytes < _LOW_DISK_THRESHOLD_BYTES:
            logger.warning(
                "Workspace '{}' has only {:.0f} MB free (< {:.0f} MB threshold)",
                workspace_root,
                free_bytes / (1024 * 1024),
                _LOW_DISK_THRESHOLD_BYTES / (1024 * 1024),
            )
    except OSError:
        # Some networked filesystems do not report free space; ignore.
        pass

    if allow_existing:
        task_dir.mkdir(parents=True, exist_ok=True)
        return

    try:
        task_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise ValueError(
            f"task directory '{task_dir}' already exists; pick a different task_id"
        ) from exc


def _register_workspace_for_filesystem_mcp(workspace_root: Path, log: Any) -> None:
    """Make sure the ``filesystem`` MCP server is rooted at the workspace.

    The DeepCode ``filesystem`` MCP server takes its allowed roots as
    trailing positional ``args``. We keep the entries that already point
    at the project tree (``.``) and append the resolved workspace + cwd
    if either is missing. This is the single owner of that wiring;
    entry-points (CLI / UI / new_ui) used to do it inline.
    """
    try:
        runtime = get_runtime()
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("Could not register filesystem MCP root: {}", exc)
        return

    fs = runtime.config.mcp_servers.get("filesystem")
    if fs is None:
        log.debug("No 'filesystem' MCP server configured; skip allowed-dir sync")
        return

    cwd = os.getcwd()
    wanted = [str(workspace_root), cwd]
    existing = list(fs.args)
    appended = False
    for entry in wanted:
        if entry not in existing:
            existing.append(entry)
            appended = True
    if appended:
        fs.args = existing
        log.debug("filesystem MCP allowed-dirs updated: {}", existing)
