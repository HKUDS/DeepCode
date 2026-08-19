"""Session-backed Thread lifecycle and rebuildable Desktop projection."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from core.agent_presets import METADATA_KEY as PRESET_METADATA_KEY
from core.agent_presets import AgentPresetError, resolve_agent_preset
from core.config import ConfigError, load_config_for_workspace
from core.application.errors import (
    ConflictError,
    InvalidArgumentError,
    ProjectNotFoundError,
    ThreadNotFoundError,
    WorkspaceOutOfScopeError,
)
from core.application.event_service import EventBroker
from core.application.views import item_view, thread_view, turn_view, workflow_view
from core.domain.common import new_id, utc_now
from core.domain.event import DomainEvent
from core.domain.execution_profile import ExecutionProfile
from core.domain.execution_security import (
    ExecutionAccessPreset,
    ExecutionSecurityProfile,
    parse_access_preset_override,
)
from core.domain.item import Item, ItemKind, ItemStatus
from core.domain.project import Project, TrustState
from core.domain.runtime_coordination import ExecutionClass
from core.domain.thread import Thread, ThreadMode, ThreadStatus
from core.domain.turn import Turn, TurnExecutor, TurnStatus
from core.domain.workflow import WorkflowRun, WorkflowStatus
from core.persistence.automation_repository import AutomationRepository
from core.persistence.database import Database
from core.persistence.event_repository import EventRepository
from core.persistence.execution_repository import ItemRepository, TurnRepository
from core.persistence.legacy_import_repository import LegacyImportRepository
from core.persistence.project_repository import ProjectRepository
from core.persistence.thread_repository import ThreadRepository
from core.persistence.workflow_repository import WorkflowRepository
from core.providers.reasoning import normalize_reasoning_effort
from core.sessions import Session, SessionMessage, SessionStore
from core.sessions.transcript import (
    is_compaction_checkpoint as _is_compaction_checkpoint,
)

logger = logging.getLogger(__name__)

_DESKTOP_KIND = "desktop"
_AUTOMATION_KIND = "automation"
_MISSING_WORKSPACE_DIR = ".missing-workspaces"
_UNSET = object()


def _projected_item_kind(message: SessionMessage) -> ItemKind:
    """The item kind a rebuilt-from-JSONL record should carry.

    Mirrors the live projection's tool families
    (`TurnProjection._kind_for_tool`) so a session rebuilt from the canonical
    file renders the same way as one watched live.
    """
    if message.role == "user":
        return ItemKind.USER_MESSAGE
    if message.role != "tool":
        return ItemKind.ASSISTANT_MESSAGE
    name = str((message.metadata or {}).get("name") or "").lower()
    if name in {"update_plan", "plan"}:
        return ItemKind.PLAN
    if name in {"bash", "exec", "execute_bash", "execute_commands"}:
        return ItemKind.COMMAND_EXECUTION
    if any(token in name for token in ("write", "edit", "apply_patch")):
        return ItemKind.FILE_CHANGE
    return ItemKind.TOOL_CALL


def _projected_item_payload(message: SessionMessage) -> dict[str, object]:
    """Payload matching what the live projection stores for the same kind."""
    if message.role != "tool":
        return {"text": message.content, "projectedFromSession": True}
    metadata = message.metadata or {}
    name = str(metadata.get("name") or "tool")
    return {
        "callId": str(metadata.get("toolCallId") or ""),
        "name": name,
        "detail": message.content[:160],
        "activity": None,
        "isError": False,
        "resultPreview": message.content,
        "projectedFromSession": True,
    }


class ThreadService:
    """Expose canonical SessionStore records through Desktop Thread projections.

    JSONL sessions own identity, title, transcript, workspace origin, model,
    access preset, and archive metadata. SQLite owns only rebuildable UI/runtime
    state such as Turns, Items, approvals, worktrees, and event replay.
    """

    def __init__(
        self,
        database: Database,
        broker: EventBroker,
        session_store: SessionStore,
    ) -> None:
        self.database = database
        self.broker = broker
        self.session_store = session_store
        # A cross-directory resume is process-local execution context, not a
        # rewrite of the Session's recorded origin. Keep it stable until the
        # App Server exits so list/read reconciliation cannot silently undo the
        # user's explicit choice.
        self._workspace_overrides: dict[str, Path] = {}

    def reconcile(self) -> int:
        """Repair both sides without treating established sessions as legacy."""

        self._adopt_sqlite_only_threads()
        repaired = 0
        for summary in self.session_store.list_sessions(limit=100_000):
            session = self.session_store.get_session(summary.session_id)
            if session is None:
                continue
            _thread, changed = self._ensure_projection(session)
            repaired += int(changed)
        return repaired

    def start(
        self,
        project_id: str,
        *,
        title: str,
        session_kind: str = _DESKTOP_KIND,
        mode: ThreadMode = ThreadMode.CODE,
        model: str | None = None,
        connection_id: str | None = None,
        reasoning_effort: str | None = None,
        access_preset_override: ExecutionAccessPreset | None = None,
        workspace_path: str | None = None,
        parent_thread_id: str | None = None,
        agent_preset: str | None = None,
        inherit_default_preset: bool = True,
    ) -> Thread:
        clean_title = title.strip()
        if not clean_title:
            raise InvalidArgumentError("thread title must not be empty")
        clean_session_kind = session_kind.strip()
        if not clean_session_kind:
            raise InvalidArgumentError("session kind must not be empty")
        with self.database.read() as connection:
            project = ProjectRepository(connection).get(project_id)
            if project is None:
                raise ProjectNotFoundError(f"project not found: {project_id}")
            parent = (
                ThreadRepository(connection).get(parent_thread_id)
                if parent_thread_id is not None
                else None
            )
        if parent_thread_id is not None and (
            parent is None or parent.project_id != project_id
        ):
            raise ThreadNotFoundError(
                f"parent thread not found in project: {parent_thread_id}"
            )
        workspace = self._workspace_within(
            workspace_path or project.canonical_path,
            project.canonical_path,
        )
        resolved_model = model.strip() if model and model.strip() else None
        resolved_connection = (
            connection_id.strip().lower()
            if connection_id and connection_id.strip()
            else None
        )
        resolved_reasoning = normalize_reasoning_effort(reasoning_effort)
        if access_preset_override is not None and not isinstance(
            access_preset_override,
            ExecutionAccessPreset,
        ):
            raise TypeError(
                "access_preset_override must be an ExecutionAccessPreset or None"
            )
        preset_snapshot = None
        if agent_preset is not None and agent_preset.strip():
            # Resolve once and persist BY VALUE: editing the preset file later
            # never changes what this Session means (dsh pins a composition
            # generation; the canonical-JSONL equivalent is a snapshot).
            try:
                preset_snapshot = resolve_agent_preset(
                    agent_preset.strip(),
                    workspace,
                )
            except AgentPresetError as exc:
                raise InvalidArgumentError(str(exc)) from exc
        elif inherit_default_preset:
            # No explicit choice: fill the blank with the configured default
            # for new Sessions (agents.defaults.defaultPreset), through the
            # same by-value snapshot. The Session stays clearable/selectable
            # while blank via set_agent_preset, exactly as if the user had
            # picked the preset themselves.
            #
            # Callers that are not a human starting a session opt out: a
            # default chosen for safe interactive chatting (say, a read-only
            # composition) must not silently strip an automated run's tools
            # in a way nothing announces.
            preset_snapshot = self._configured_default_preset(workspace)
            if preset_snapshot is not None:
                logger.info(
                    "applying configured default agent preset %r to new session",
                    preset_snapshot.id,
                )
        metadata = {
            "kind": clean_session_kind,
            "workspace": str(workspace),
            "project_path": project.canonical_path,
            "mode": mode.value,
            "model": resolved_model,
            "connection_id": resolved_connection,
            "reasoning_effort": resolved_reasoning,
            "access_preset_override": (
                access_preset_override.value
                if access_preset_override is not None
                else None
            ),
            "archived": False,
        }
        if preset_snapshot is not None:
            metadata[PRESET_METADATA_KEY] = preset_snapshot.to_metadata()
        if parent_thread_id is not None:
            metadata["parent_session_id"] = parent_thread_id

        session = self.session_store.create_session(
            title=clean_title,
            metadata=metadata,
        )
        try:
            thread, _changed = self._ensure_projection(
                session,
                project_hint=project.id,
                event_type="thread.created",
            )
            return thread
        except BaseException:
            # The operation was never accepted and the new canonical session is
            # still empty, so compensating deletion is safe.
            self.session_store.delete_session(session.session_id)
            raise

    def resume(
        self,
        session_id: str,
        *,
        workspace_path: str | None = None,
    ) -> Thread:
        session = self.session_store.get_session(session_id)
        if session is None:
            raise ThreadNotFoundError(f"session not found: {session_id}")
        override = None
        if workspace_path is not None:
            override = self._existing_directory(workspace_path)
            self._workspace_overrides[session_id] = override
        thread, _changed = self._ensure_projection(
            session,
            workspace_override=override,
            event_type="thread.resumed",
        )
        return thread

    def materialize_session(self, thread_id: str) -> Session:
        """Create the exact canonical Session for one committed SQLite Thread.

        Automation creation is the one narrow bootstrap exception to the
        usual Session-first lifecycle: its Thread, definition, revision, and
        events commit atomically before this idempotent filesystem step. Every
        read/recovery caller uses this same method, so a stopped creator and a
        second live process converge on identical Session metadata.
        """

        while True:
            thread, project_path, automation_id = self._materialization_snapshot(
                thread_id
            )
            metadata = self._materialization_metadata(
                thread,
                project_path=project_path,
                automation_id=automation_id,
            )
            created_here = False
            created_fingerprint: tuple[object, ...] | None = None
            try:
                session = self.session_store.create_session(
                    session_id=thread.id,
                    title=thread.title,
                    metadata=metadata,
                    created_at=thread.created_at.isoformat(),
                    updated_at=thread.updated_at.isoformat(),
                )
                created_here = True
                created_fingerprint = self._empty_session_fingerprint(session)
            except FileExistsError:
                session = self.session_store.get_session(thread.id)
                if session is None:
                    raise ConflictError(
                        "the canonical Session path exists without readable metadata"
                    )

            try:
                current, current_project_path, current_automation_id = (
                    self._materialization_snapshot(thread_id)
                )
            except ThreadNotFoundError:
                if created_here and automation_id is not None:
                    assert created_fingerprint is not None
                    self._discard_new_empty_automation_session(
                        thread_id,
                        automation_id=automation_id,
                        expected_fingerprint=created_fingerprint,
                    )
                raise

            if (
                current.project_id != thread.project_id
                or current_automation_id != automation_id
            ):
                if created_here and automation_id is not None:
                    assert created_fingerprint is not None
                    self._discard_new_empty_automation_session(
                        thread_id,
                        automation_id=automation_id,
                        expected_fingerprint=created_fingerprint,
                    )
                raise ThreadNotFoundError(
                    f"thread ownership changed during materialization: {thread_id}"
                )

            refreshed = self.session_store.get_session(thread.id)
            if refreshed is None:
                raise ConflictError(
                    f"canonical Session disappeared during materialization: {thread.id}"
                )
            if not self._session_matches_materialization(
                refreshed,
                thread=current,
                project_path=current_project_path,
                automation_id=current_automation_id,
            ):
                raise ConflictError(
                    "an existing canonical Session conflicts with the committed "
                    f"Thread ownership: {thread.id}"
                )

            if (
                created_here
                and automation_id is not None
                and self._materialization_signature(
                    thread,
                    project_path=project_path,
                    automation_id=automation_id,
                )
                != self._materialization_signature(
                    current,
                    project_path=current_project_path,
                    automation_id=current_automation_id,
                )
            ):
                assert created_fingerprint is not None
                if self._discard_new_empty_automation_session(
                    thread_id,
                    automation_id=automation_id,
                    expected_fingerprint=created_fingerprint,
                ):
                    continue
                refreshed = self.session_store.get_session(thread.id)
                if refreshed is None or not self._session_matches_materialization(
                    refreshed,
                    thread=current,
                    project_path=current_project_path,
                    automation_id=current_automation_id,
                ):
                    raise ConflictError(
                        "Thread changed while its canonical Session was materialized"
                    )
            return refreshed

    def read(self, thread_id: str) -> Thread:
        session = self.session_store.get_session(thread_id)
        if session is not None:
            thread, _changed = self._ensure_projection(session)
            return thread

        if self.session_store.is_deletion_pending(thread_id):
            raise ThreadNotFoundError(f"thread not found: {thread_id}")

        # A projection-only thread may be encountered before startup
        # reconciliation. Adopt it in place (preserving its id) only when
        # SQLite is genuinely its system of record — legacy import or
        # Automation bootstrap; a stale shadow of a removed Session is
        # dropped, never resurrected (one-way data flow).
        with self.database.read() as connection:
            existing = ThreadRepository(connection).get(thread_id)
        if existing is not None:
            if self._sqlite_is_system_of_record(existing.id):
                self._adopt_thread(existing)
                session = self.session_store.get_session(thread_id)
                if session is not None:
                    thread, _changed = self._ensure_projection(session)
                    return thread
            else:
                self._drop_stale_projection(existing)
        raise ThreadNotFoundError(f"thread not found: {thread_id}")

    def list(
        self,
        project_id: str | None = None,
        *,
        cwd: str | None = None,
        include_archived: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Thread]:
        if not 1 <= limit <= 500 or offset < 0:
            raise InvalidArgumentError("invalid pagination")
        if project_id is not None:
            with self.database.read() as connection:
                if ProjectRepository(connection).get(project_id) is None:
                    raise ProjectNotFoundError(f"project not found: {project_id}")
        exact_cwd = str(self._existing_directory(cwd)) if cwd is not None else None
        self.reconcile()
        with self.database.read() as connection:
            rows = ThreadRepository(connection).list_all(
                include_archived=include_archived,
                limit=100_000,
            )
        visible = [
            thread
            for thread in rows
            if self.session_store.get_session(thread.id) is not None
            and (project_id is None or thread.project_id == project_id)
            and (exact_cwd is None or thread.workspace_path == exact_cwd)
        ]
        return visible[offset : offset + limit]

    def rename(self, thread_id: str, title: str) -> Thread:
        clean_title = title.strip()
        if not clean_title:
            raise InvalidArgumentError("thread title must not be empty")
        if not self.session_store.rename_session(thread_id, clean_title):
            raise ThreadNotFoundError(f"thread not found: {thread_id}")
        return self._project_updated_session(thread_id, "thread.renamed")

    def set_model(self, thread_id: str, model: str | None) -> Thread:
        """Change the model snapshot used by future Turns."""

        resolved = model.strip() if model and model.strip() else None
        if not self.session_store.update_metadata(thread_id, {"model": resolved}):
            raise ThreadNotFoundError(f"thread not found: {thread_id}")
        return self._project_updated_session(thread_id, "thread.model_changed")

    def set_execution_selection(
        self,
        thread_id: str,
        *,
        connection_id: str | None,
        model: str | None,
        reasoning_effort: str | None | object = _UNSET,
    ) -> Thread:
        """Atomically change the selection used by future Turns."""

        resolved_connection = (
            connection_id.strip().lower()
            if connection_id and connection_id.strip()
            else None
        )
        resolved_model = model.strip() if model and model.strip() else None
        metadata: dict[str, str | None] = {
            "connection_id": resolved_connection,
            "model": resolved_model,
        }
        if reasoning_effort is not _UNSET:
            metadata["reasoning_effort"] = normalize_reasoning_effort(
                reasoning_effort if isinstance(reasoning_effort, str) else None
            )
        if not self.session_store.update_metadata(thread_id, metadata):
            raise ThreadNotFoundError(f"thread not found: {thread_id}")
        return self._project_updated_session(thread_id, "thread.model_changed")

    def set_access_preset(
        self,
        thread_id: str,
        access_preset: ExecutionAccessPreset | None,
    ) -> Thread:
        """Change the access preset inherited by future Turns in one Session."""

        if access_preset is not None and not isinstance(
            access_preset,
            ExecutionAccessPreset,
        ):
            raise TypeError("access_preset must be an ExecutionAccessPreset or None")
        if not self.session_store.update_metadata(
            thread_id,
            {
                "access_preset_override": (
                    access_preset.value if access_preset is not None else None
                )
            },
        ):
            raise ThreadNotFoundError(f"thread not found: {thread_id}")
        return self._project_updated_session(
            thread_id,
            "thread.permission_changed",
        )

    def set_agent_preset(self, thread_id: str, preset_id: str | None) -> Thread:
        """Select or clear the agent preset — only while the Session is blank.

        dsh's lock, for dsh's reason: once the conversation has started, its
        history was produced under one composition; swapping personas or tool
        faces mid-Session would make the record mean something it never
        meant. A new Session is the way to change composition.
        """

        session = self.session_store.get_session(thread_id)
        if session is None:
            raise ThreadNotFoundError(f"thread not found: {thread_id}")
        if session.messages:
            raise ConflictError(
                "agent preset is locked once the conversation has started",
                user_message=(
                    "This Session already has messages; start a new Session "
                    "to use a different agent preset."
                ),
            )
        snapshot_value = None
        if preset_id is not None and preset_id.strip():
            try:
                snapshot_value = resolve_agent_preset(
                    preset_id.strip(),
                    session.metadata.get("workspace"),
                ).to_metadata()
            except AgentPresetError as exc:
                raise InvalidArgumentError(str(exc)) from exc
        if not self.session_store.update_metadata(
            thread_id,
            {PRESET_METADATA_KEY: snapshot_value},
        ):
            raise ThreadNotFoundError(f"thread not found: {thread_id}")
        return self._project_updated_session(thread_id, "thread.reconciled")

    def archive(self, thread_id: str) -> Thread:
        now = utc_now()
        if not self.session_store.update_metadata(
            thread_id,
            {
                "archived": True,
                "archived_at": now.isoformat(),
            },
        ):
            raise ThreadNotFoundError(f"thread not found: {thread_id}")
        return self._project_updated_session(thread_id, "thread.archived")

    def forget(self, thread_id: str) -> None:
        """Drop process-local resume context after permanent deletion."""

        self._workspace_overrides.pop(thread_id, None)

    def fork(self, thread_id: str, *, title: str | None = None) -> Thread:
        source = self.session_store.get_session(thread_id)
        if source is None:
            raise ThreadNotFoundError(f"thread not found: {thread_id}")
        fork_title = title.strip() if title is not None else f"Fork of {source.title}"
        if not fork_title:
            raise InvalidArgumentError("thread title must not be empty")
        forked = self.session_store.branch_session(
            source.session_id,
            from_message_index=len(source.messages),
            title=fork_title,
        )
        if forked is None:  # pragma: no cover - source was read under the same store
            raise ThreadNotFoundError(f"thread not found: {thread_id}")
        metadata = {
            **source.metadata,
            "kind": _DESKTOP_KIND,
            "parent_session_id": source.session_id,
            "branched_from": source.session_id,
            "branched_at_message": len(source.messages),
            "archived": False,
            "archived_at": None,
            # A fork is a new Session. Never carry an access grant, especially
            # Full Access, into it without a fresh explicit user selection.
            "access_preset_override": None,
        }
        self.session_store.update_metadata(forked.session_id, metadata)
        forked = self.session_store.get_session(forked.session_id)
        assert forked is not None
        thread, _changed = self._ensure_projection(
            forked,
            event_type="thread.created",
        )
        return thread

    def _project_updated_session(self, session_id: str, event_type: str) -> Thread:
        session = self.session_store.get_session(session_id)
        if session is None:  # pragma: no cover - guarded by the mutation
            raise ThreadNotFoundError(f"thread not found: {session_id}")
        thread, _changed = self._ensure_projection(session, event_type=event_type)
        return thread

    def _ensure_projection(
        self,
        session: Session,
        *,
        project_hint: str | None = None,
        workspace_override: Path | None = None,
        event_type: str | None = None,
    ) -> tuple[Thread, bool]:
        workspace = (
            workspace_override
            or self._workspace_overrides.get(session.session_id)
            or self._workspace_for(session)
        )
        canonical_created = self._parse_time(session.created_at)
        canonical_updated = self._parse_time(session.updated_at)
        metadata = session.metadata or {}
        mode = self._mode_for(session)
        model = self._model_for(metadata)
        connection_id = self._connection_for(metadata)
        reasoning_effort = self._reasoning_for(metadata)
        access_preset_override = self._access_preset_for(metadata)
        archived = bool(metadata.get("archived"))
        archived_at = (
            self._parse_time(str(metadata.get("archived_at"))) if archived else None
        )
        events: list[DomainEvent] = []
        changed = False

        with self.database.transaction() as connection:
            projects = ProjectRepository(connection)
            project = self._project_for_workspace(
                projects,
                workspace,
                project_hint=project_hint,
            )
            threads = ThreadRepository(connection)
            existing = threads.get(session.session_id)
            parent_id = self._parent_for(metadata, threads)
            title = session.title.strip() or f"Session {session.session_id}"

            if existing is None:
                projected = Thread(
                    id=session.session_id,
                    project_id=project.id,
                    parent_thread_id=parent_id,
                    title=title,
                    mode=mode,
                    status=ThreadStatus.ARCHIVED if archived else ThreadStatus.IDLE,
                    model=model,
                    connection_id=connection_id,
                    reasoning_effort=reasoning_effort,
                    access_preset_override=access_preset_override,
                    workspace_path=str(workspace),
                    created_at=canonical_created,
                    updated_at=canonical_updated,
                    archived_at=archived_at,
                )
                threads.add(projected)
                changed = True
                event = EventRepository(connection).append(
                    thread_id=projected.id,
                    type=event_type or "thread.projected",
                    payload={"thread": thread_view(projected)},
                )
                events.append(event)
            else:
                status = (
                    ThreadStatus.ARCHIVED
                    if archived
                    else (
                        ThreadStatus.IDLE
                        if existing.status is ThreadStatus.ARCHIVED
                        else existing.status
                    )
                )
                # Canonical transcript time drives recency, but projection-only
                # runtime transitions (recovery, approval, worktree state) may
                # legitimately be newer and must never move backwards.
                updated_at = max(existing.updated_at, canonical_updated)
                projected = replace(
                    existing,
                    project_id=project.id,
                    parent_thread_id=parent_id,
                    title=title,
                    mode=mode,
                    status=status,
                    model=model,
                    connection_id=connection_id,
                    reasoning_effort=reasoning_effort,
                    access_preset_override=access_preset_override,
                    workspace_path=str(workspace),
                    updated_at=updated_at,
                    archived_at=archived_at,
                )
                if projected != existing:
                    threads.update(projected)
                    changed = True
                    event = EventRepository(connection).append(
                        thread_id=projected.id,
                        type=event_type or "thread.reconciled",
                        payload={"thread": thread_view(projected)},
                    )
                    events.append(event)

            transcript_events = self._reconcile_transcript(
                connection,
                projected,
                session,
            )
            if transcript_events:
                changed = True
                events.extend(transcript_events)
            workflow_events = self._reconcile_session_tasks(
                connection,
                projected,
                session,
            )
            if workflow_events:
                changed = True
                events.extend(workflow_events)

        for event in events:
            self.broker.publish(event)
        return projected, changed

    def _reconcile_transcript(
        self,
        connection,
        thread: Thread,
        session: Session,
    ) -> list[DomainEvent]:
        """Append missing visible JSONL messages into the disposable timeline."""

        # Two views of the same records. The TRANSCRIPT is what the
        # projected/canonical comparison is defined over — text the user
        # exchanged with the agent, which is what `conversation_for_thread`
        # returns. The TIMELINE additionally carries the tool records Phase 1
        # started persisting, so a session rebuilt from JSONL shows what the
        # agent did and not only what it said. A compaction checkpoint is in
        # neither: it rides a user-role record but is bookkeeping, not
        # conversation.
        timeline = [
            message
            for message in session.messages
            if message.role in {"user", "assistant", "tool"}
            and (message.content or (message.metadata or {}).get("toolCalls"))
            and not self._is_context_note(message)
            and not _is_compaction_checkpoint(message)
        ]
        canonical = [
            message
            for message in timeline
            if message.role in {"user", "assistant"} and message.content
        ]
        items = ItemRepository(connection)
        projected_items = items.conversation_for_thread(thread.id)
        projected = [
            (
                "user" if item.kind is ItemKind.USER_MESSAGE else "assistant",
                str(item.payload.get("text", item.summary)),
            )
            for item in projected_items
        ]
        canonical_pairs = [(message.role, message.content) for message in canonical]

        if projected == canonical_pairs:
            return []
        if canonical_pairs == projected[: len(canonical_pairs)]:
            # The live SQLite stream may briefly be ahead of JSONL between the
            # assistant event and its canonical append. Never duplicate it.
            return []
        if projected != canonical_pairs[: len(projected)]:
            # Preserve execution artifacts on disagreement. The next explicit
            # rebuild (delete projection DB) still deterministically follows
            # JSONL, while this event makes the conflict diagnosable.
            event_repo = EventRepository(connection)
            if event_repo.has_type(thread.id, "thread.projection_conflict"):
                return []
            event = event_repo.append(
                thread_id=thread.id,
                type="thread.projection_conflict",
                payload={
                    "canonicalMessageCount": len(canonical_pairs),
                    "projectedMessageCount": len(projected),
                },
            )
            return [event]

        # Resume the timeline just past the text messages already projected,
        # so tool records that belong to an already-projected turn are not
        # appended a second time.
        consumed = len(projected)
        start = len(timeline)
        seen = 0
        for index, message in enumerate(timeline):
            if message.role in {"user", "assistant"} and message.content:
                if seen == consumed:
                    start = index
                    break
                seen += 1
        else:
            if seen == consumed:
                start = len(timeline)
        missing = timeline[start:]
        groups: list[list[SessionMessage]] = []
        for message in missing:
            # A compaction checkpoint is runtime bookkeeping that happens to
            # ride a user-role record. Treating it as a prompt opened an empty
            # Turn in the Desktop projection whose title was the summary
            # preamble; it belongs to the Turn it closed, not to a new one.
            starts_turn = message.role == "user" and not _is_compaction_checkpoint(
                message
            )
            if starts_turn or not groups:
                groups.append([message])
            else:
                groups[-1].append(message)

        turns = TurnRepository(connection)
        event_repo = EventRepository(connection)
        events: list[DomainEvent] = []
        for messages in groups:
            timestamps = [self._parse_time(message.timestamp) for message in messages]
            execution_security_profile = self._execution_security_for(messages)
            first_user = next(
                (
                    message
                    for message in messages
                    if message.role == "user" and not _is_compaction_checkpoint(message)
                ),
                None,
            )
            turn = Turn(
                thread_id=thread.id,
                ordinal=turns.next_ordinal(thread.id),
                prompt=(
                    first_user.content
                    if first_user is not None
                    else "Projected session response"
                ),
                execution_profile=next(
                    (
                        parsed
                        for message in messages
                        for parsed in [
                            ExecutionProfile.from_dict(
                                (message.metadata or {}).get("executionProfile")
                            )
                        ]
                        if parsed is not None
                    ),
                    None,
                ),
                execution_permission_mode=(
                    execution_security_profile.permission_mode
                    if execution_security_profile is not None
                    else None
                ),
                execution_security_profile=execution_security_profile,
                goal_id=self._goal_id_for(messages),
                execution_class=self._execution_class_for(messages),
                status=TurnStatus.COMPLETED,
                stop_reason="session_projection",
                started_at=min(timestamps),
                completed_at=max(timestamps),
            )
            turns.add(turn)
            events.append(
                event_repo.append(
                    thread_id=thread.id,
                    turn_id=turn.id,
                    type="turn.projected",
                    payload={"turn": turn_view(turn)},
                )
            )
            for message, created_at in zip(messages, timestamps, strict=True):
                reasoning_summary = (message.metadata or {}).get("reasoningSummary")
                if (
                    message.role == "assistant"
                    and isinstance(reasoning_summary, str)
                    and reasoning_summary.strip()
                ):
                    reasoning_item = Item(
                        thread_id=thread.id,
                        turn_id=turn.id,
                        ordinal=items.next_ordinal(turn.id),
                        kind=ItemKind.REASONING_SUMMARY,
                        status=ItemStatus.COMPLETED,
                        summary=reasoning_summary.strip()[:160],
                        payload={
                            "text": reasoning_summary.strip(),
                            "projectedFromSession": True,
                        },
                        created_at=created_at,
                        updated_at=created_at,
                    )
                    items.add(reasoning_item)
                    events.append(
                        event_repo.append(
                            thread_id=thread.id,
                            turn_id=turn.id,
                            item_id=reasoning_item.id,
                            type="item.projected",
                            payload={"item": item_view(reasoning_item)},
                        )
                    )
                item = Item(
                    thread_id=thread.id,
                    turn_id=turn.id,
                    ordinal=items.next_ordinal(turn.id),
                    kind=_projected_item_kind(message),
                    status=ItemStatus.COMPLETED,
                    summary=message.content[:160],
                    payload=_projected_item_payload(message),
                    created_at=created_at,
                    updated_at=created_at,
                )
                items.add(item)
                events.append(
                    event_repo.append(
                        thread_id=thread.id,
                        turn_id=turn.id,
                        item_id=item.id,
                        type="item.projected",
                        payload={"item": item_view(item)},
                    )
                )
        return events

    def _reconcile_session_tasks(
        self,
        connection,
        thread: Thread,
        session: Session,
    ) -> list[DomainEvent]:
        """Rebuild minimal historical WorkflowRuns from canonical task links."""

        workflows = WorkflowRepository(connection)
        existing_task_ids = {
            str(
                run.checkpoint.get("sessionTaskId")
                or run.checkpoint.get("legacyTaskId")
                or run.checkpoint.get("taskId")
                or ""
            )
            for run in workflows.list_for_thread(thread.id, limit=10_000)
        }
        turns = TurnRepository(connection)
        items = ItemRepository(connection)
        event_repo = EventRepository(connection)
        events: list[DomainEvent] = []
        for task in session.tasks:
            if task.task_id in existing_task_ids:
                continue
            status = self._workflow_status_for_task(task.status)
            created = self._parse_time(task.created_at)
            updated = max(created, self._parse_time(task.updated_at))
            turn_status = (
                TurnStatus.COMPLETED
                if status is WorkflowStatus.COMPLETED
                else TurnStatus.INTERRUPTED
                if status is WorkflowStatus.CANCELLED
                else TurnStatus.FAILED
            )
            turn = Turn(
                thread_id=thread.id,
                ordinal=turns.next_ordinal(thread.id),
                prompt=f"Recovered workflow task {task.task_id}",
                executor=TurnExecutor.WORKFLOW,
                status=turn_status,
                stop_reason="session_task_projection",
                error_code=(
                    "SESSION_TASK_NOT_LIVE"
                    if turn_status is TurnStatus.FAILED
                    else None
                ),
                error_message=(
                    "Workflow runtime state was not available after projection rebuild"
                    if turn_status is TurnStatus.FAILED
                    else None
                ),
                started_at=created,
                completed_at=updated,
            )
            turns.add(turn)
            metadata = task.metadata or {}
            preferred_run_id = str(metadata.get("workflowRunId") or "")
            if (
                not preferred_run_id.startswith("wfr_")
                or workflows.get(preferred_run_id) is not None
            ):
                preferred_run_id = new_id("wfr")
            retry_candidate = str(metadata.get("retryOf") or "")
            run = WorkflowRun(
                id=preferred_run_id,
                thread_id=thread.id,
                turn_id=turn.id,
                kind="paper2code",
                status=status,
                input=(
                    metadata.get("input")
                    if isinstance(metadata.get("input"), dict)
                    else {}
                ),
                result=(
                    metadata.get("result")
                    if isinstance(metadata.get("result"), dict)
                    else {}
                ),
                attempt=self._positive_int(metadata.get("attempt")),
                retry_of=(
                    retry_candidate
                    if retry_candidate.startswith("wfr_")
                    and workflows.get(retry_candidate) is not None
                    else None
                ),
                checkpoint={
                    **(
                        metadata.get("checkpoint")
                        if isinstance(metadata.get("checkpoint"), dict)
                        else {}
                    ),
                    "sessionTaskId": task.task_id,
                    "taskDir": task.task_dir,
                    "projectedFromSession": True,
                },
                created_at=created,
                updated_at=updated,
                started_at=created,
                completed_at=updated,
                error_code=(
                    "SESSION_TASK_NOT_LIVE" if status is WorkflowStatus.FAILED else None
                ),
                error_message=(
                    "Workflow runtime state was not available after projection rebuild"
                    if status is WorkflowStatus.FAILED
                    else None
                ),
            )
            workflows.add(run)
            completion = Item(
                thread_id=thread.id,
                turn_id=turn.id,
                ordinal=items.next_ordinal(turn.id),
                kind=ItemKind.COMPLETION,
                status=(
                    ItemStatus.COMPLETED
                    if status is WorkflowStatus.COMPLETED
                    else ItemStatus.FAILED
                ),
                summary=f"Workflow {status.value}",
                payload={
                    "workflowRunId": run.id,
                    "sessionTaskId": task.task_id,
                    "status": status.value,
                    "projectedFromSession": True,
                },
                created_at=updated,
                updated_at=updated,
            )
            items.add(completion)
            events.extend(
                (
                    event_repo.append(
                        thread_id=thread.id,
                        turn_id=turn.id,
                        type="turn.projected",
                        payload={"turn": turn_view(turn)},
                    ),
                    event_repo.append(
                        thread_id=thread.id,
                        turn_id=turn.id,
                        type="workflow.projected",
                        payload={"workflow": workflow_view(run)},
                    ),
                    event_repo.append(
                        thread_id=thread.id,
                        turn_id=turn.id,
                        item_id=completion.id,
                        type="item.projected",
                        payload={"item": item_view(completion)},
                    ),
                )
            )
            existing_task_ids.add(task.task_id)
        return events

    @staticmethod
    def _workflow_status_for_task(raw: str) -> WorkflowStatus:
        normalized = raw.strip().lower()
        if normalized in {"completed", "complete"}:
            return WorkflowStatus.COMPLETED
        if normalized in {"cancelled", "canceled"}:
            return WorkflowStatus.CANCELLED
        return WorkflowStatus.FAILED

    @staticmethod
    def _positive_int(raw) -> int:
        try:
            return max(1, int(raw or 1))
        except (TypeError, ValueError):
            return 1

    def _adopt_sqlite_only_threads(self) -> None:
        """Resolve projection rows whose canonical Session file is missing.

        One-way data flow (the dsh invariant): JSONL owns identity, so a
        Thread row is promoted back into the canonical store only when SQLite
        is genuinely its system of record — a P1-P6 legacy import, or an
        Automation bootstrap whose idempotent Session materialization has not
        landed yet. Any other projection-only Thread is a stale shadow of a
        Session removed outside DeepCode; it is dropped from the projection
        instead of resurrected.
        """
        with self.database.read() as connection:
            threads = ThreadRepository(connection).list_all(
                include_archived=True,
                limit=100_000,
            )
        for thread in threads:
            if self.session_store.get_session(
                thread.id
            ) is not None or self.session_store.is_deletion_pending(thread.id):
                continue
            if self._sqlite_is_system_of_record(thread.id):
                self._adopt_thread(thread)
            else:
                self._drop_stale_projection(thread)

    def _sqlite_is_system_of_record(self, thread_id: str) -> bool:
        """True when this Thread legitimately predates its canonical Session."""

        with self.database.read() as connection:
            if (
                LegacyImportRepository(connection).source_for_thread(thread_id)
                is not None
            ):
                return True
            return (
                AutomationRepository(connection).get_for_thread(
                    thread_id,
                    include_retired=True,
                )
                is not None
            )

    def _drop_stale_projection(self, thread: Thread) -> None:
        with self.database.transaction() as connection:
            removed = ThreadRepository(connection).remove(thread.id)
        if removed:
            logger.warning(
                "Dropped stale thread projection %s (%r): its canonical Session "
                "no longer exists and SQLite was never its system of record",
                thread.id,
                thread.title,
            )

    def _adopt_thread(self, thread: Thread) -> None:
        """Promote P1-P6 SQLite-only data into the canonical JSONL store."""

        with self.database.read() as connection:
            messages = ItemRepository(connection).conversation_for_thread(thread.id)
            legacy_source = LegacyImportRepository(connection).source_for_thread(
                thread.id
            )
        if legacy_source is not None:
            source_key, source_session_id = legacy_source
            source_root, separator, _source_id = source_key.rpartition("::")
            canonical_source = self.session_store.get_session(source_session_id)
            if (
                separator
                and Path(source_root).resolve() == self.session_store.root.resolve()
                and canonical_source is not None
                and self._merge_projection_tail(
                    canonical_source,
                    messages,
                    projection_thread_id=thread.id,
                )
            ):
                return
        session = self.materialize_session(thread.id)
        self._merge_projection_tail(
            session,
            messages,
            projection_thread_id=thread.id,
        )

    def _materialization_snapshot(
        self,
        thread_id: str,
    ) -> tuple[Thread, str, str | None]:
        """Read Thread ownership in one SQLite snapshot."""

        with self.database.read() as connection:
            thread = ThreadRepository(connection).get(thread_id)
            if thread is None:
                raise ThreadNotFoundError(f"thread not found: {thread_id}")
            project = ProjectRepository(connection).get(thread.project_id)
            if project is None:
                raise ThreadNotFoundError(
                    f"thread project no longer exists: {thread_id}"
                )
            automation = AutomationRepository(connection).get_for_thread(
                thread_id,
                include_retired=True,
            )
        return (
            thread,
            project.canonical_path,
            automation.id if automation is not None else None,
        )

    @staticmethod
    def _materialization_metadata(
        thread: Thread,
        *,
        project_path: str,
        automation_id: str | None,
    ) -> dict:
        metadata = {
            "kind": (_AUTOMATION_KIND if automation_id is not None else _DESKTOP_KIND),
            "workspace": thread.workspace_path,
            "project_path": project_path,
            "mode": thread.mode.value,
            "model": thread.model,
            "connection_id": thread.connection_id,
            "reasoning_effort": thread.reasoning_effort,
            "access_preset_override": (
                thread.access_preset_override.value
                if thread.access_preset_override is not None
                else None
            ),
            "archived": thread.status is ThreadStatus.ARCHIVED,
            "archived_at": (
                thread.archived_at.isoformat()
                if thread.archived_at is not None
                else None
            ),
        }
        if automation_id is not None:
            metadata["automation_id"] = automation_id
        if thread.parent_thread_id is not None:
            metadata["parent_session_id"] = thread.parent_thread_id
        return metadata

    @staticmethod
    def _materialization_signature(
        thread: Thread,
        *,
        project_path: str,
        automation_id: str | None,
    ) -> tuple[object, ...]:
        """Fields a new empty Session would make canonical on projection."""

        return (
            thread.id,
            thread.project_id,
            thread.parent_thread_id,
            thread.title,
            thread.mode,
            thread.workspace_path,
            thread.model,
            thread.connection_id,
            thread.reasoning_effort,
            thread.access_preset_override,
            thread.status is ThreadStatus.ARCHIVED,
            thread.archived_at,
            project_path,
            automation_id,
        )

    @staticmethod
    def _session_matches_materialization(
        session: Session,
        *,
        thread: Thread,
        project_path: str,
        automation_id: str | None,
    ) -> bool:
        """Reject identity collisions while allowing later presentation edits."""

        if session.session_id != thread.id:
            return False
        # Outside the narrow DB-first Automation bootstrap, JSONL remains
        # canonical. A concurrently appearing CLI/TUI Session may legitimately
        # have a different surface kind; ordinary projection reconciliation
        # owns those metadata semantics.
        if automation_id is None:
            return True
        metadata = session.metadata or {}
        return bool(
            metadata.get("kind") == _AUTOMATION_KIND
            and metadata.get("automation_id") == automation_id
            and metadata.get("workspace") == thread.workspace_path
            and metadata.get("project_path") == project_path
            and metadata.get("mode") == thread.mode.value
        )

    def _discard_new_empty_automation_session(
        self,
        thread_id: str,
        *,
        automation_id: str,
        expected_fingerprint: tuple[object, ...],
    ) -> bool:
        """Remove only this materializer's untouched, now-ownerless Session."""

        with self.session_store.deletion_guard(thread_id) as guarded:
            if not guarded.exists or guarded.pending:
                return False
            session = self.session_store.get_session(thread_id)
            if (
                session is None
                or session.messages
                or session.tasks
                or session.metadata.get("kind") != _AUTOMATION_KIND
                or session.metadata.get("automation_id") != automation_id
            ):
                return False
            if self._empty_session_fingerprint(session) != expected_fingerprint:
                return False
            try:
                children = {child.name for child in guarded.directory.iterdir()}
            except OSError:
                return False
            if children != {"session.jsonl"}:
                return False
            ticket = guarded.stage()
            return guarded.finalize(ticket)

    @staticmethod
    def _empty_session_fingerprint(session: Session) -> tuple[object, ...]:
        return (
            session.session_id,
            session.title,
            session.created_at,
            session.updated_at,
            dict(session.metadata),
        )

    @staticmethod
    def _is_context_note(message: SessionMessage) -> bool:
        """True for runner-injected mid-turn context, not a conversational turn.

        The context-note sink stamps every note with a ``delivery`` marker
        (``mid_turn``/``between_turns``). Notes are model-visible log entries
        — repeat-guard reminders, sub-agent results, Goal updates — that sit
        BETWEEN a Turn's user prompt and its assistant reply in the canonical
        JSONL. The projection timeline tracks conversational turns only, so
        pair-matching must skip them or every noted Turn would read as a
        projection conflict.
        """
        metadata = message.metadata or {}
        return "delivery" in metadata

    def _merge_projection_tail(
        self,
        canonical: Session,
        projected_items: list[Item],
        *,
        projection_thread_id: str,
    ) -> bool:
        canonical_pairs = [
            (message.role, message.content)
            for message in canonical.messages
            if message.role in {"user", "assistant"}
            and message.content
            and not self._is_context_note(message)
        ]
        projected_pairs = [
            (
                "user" if item.kind is ItemKind.USER_MESSAGE else "assistant",
                str(item.payload.get("text", item.summary)),
            )
            for item in projected_items
        ]
        if projected_pairs == canonical_pairs[: len(projected_pairs)]:
            return True
        if canonical_pairs != projected_pairs[: len(canonical_pairs)]:
            return False
        for item, (role, content) in zip(
            projected_items[len(canonical_pairs) :],
            projected_pairs[len(canonical_pairs) :],
            strict=True,
        ):
            stored = self.session_store.append_message(
                canonical.session_id,
                role,
                content,
                metadata={
                    "recoveredFromP6Projection": projection_thread_id,
                    "recoveredFromItemId": item.id,
                    "createdAt": item.created_at.isoformat(),
                },
            )
            if stored is None:
                return False
        return True

    def _project_for_workspace(
        self,
        projects: ProjectRepository,
        workspace: Path,
        *,
        project_hint: str | None,
    ) -> Project:
        if project_hint is not None:
            hinted = projects.get(project_hint)
            if hinted is None:
                raise ProjectNotFoundError(f"project not found: {project_hint}")
            if not self._is_within(workspace, Path(hinted.canonical_path)):
                raise WorkspaceOutOfScopeError(
                    f"workspace is outside project boundary: {workspace}"
                )
            return hinted

        candidates = [
            project
            for project in projects.list(limit=100_000)
            if self._is_within(workspace, Path(project.canonical_path))
        ]
        manual = [
            project
            for project in candidates
            if not bool(project.settings.get("sessionDiscovered"))
        ]
        selected = manual or candidates
        if selected:
            return max(
                selected, key=lambda project: len(Path(project.canonical_path).parts)
            )

        now = utc_now()
        canonical = str(workspace)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
        project = Project(
            id=f"proj_{digest}",
            canonical_path=canonical,
            display_name=workspace.name or canonical,
            trust_state=TrustState.UNTRUSTED,
            settings={"sessionDiscovered": True},
            created_at=now,
            updated_at=now,
            last_opened_at=now,
        )
        projects.add(project)
        return project

    def _workspace_for(self, session: Session) -> Path:
        raw = str((session.metadata or {}).get("workspace") or "").strip()
        if raw:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = Path.cwd() / path
            return path.resolve(strict=False)
        return (
            self.session_store.root / _MISSING_WORKSPACE_DIR / session.session_id
        ).resolve(strict=False)

    @staticmethod
    def _parent_for(metadata: dict, threads: ThreadRepository) -> str | None:
        raw = metadata.get("parent_session_id") or metadata.get("branched_from")
        if not raw:
            return None
        parent_id = str(raw)
        return parent_id if threads.get(parent_id) is not None else None

    @staticmethod
    def _mode_for(session: Session) -> ThreadMode:
        metadata = session.metadata or {}
        inferred = (
            ThreadMode.PAPER
            if any(task.task_kind == "paper" for task in session.tasks)
            else ThreadMode.CODE
        )
        raw = str(metadata.get("mode") or inferred)
        try:
            return ThreadMode(raw)
        except ValueError:
            return ThreadMode.CODE

    @staticmethod
    def _model_for(metadata: dict) -> str | None:
        raw = metadata.get("model")
        if raw is None:
            return None
        return str(raw).strip() or None

    @staticmethod
    def _connection_for(metadata: dict) -> str | None:
        raw = metadata.get("connection_id") or metadata.get("connectionId")
        if raw is None:
            return None
        return str(raw).strip().lower() or None

    @staticmethod
    def _reasoning_for(metadata: dict) -> str | None:
        raw = metadata.get("reasoning_effort") or metadata.get("reasoningEffort")
        return normalize_reasoning_effort(str(raw)) if raw is not None else None

    @staticmethod
    def _access_preset_for(metadata: dict) -> ExecutionAccessPreset | None:
        try:
            return parse_access_preset_override(metadata)
        except ValueError:
            # Keep the application recoverable while never interpreting
            # damaged metadata as inheritance (which could resolve to Full
            # access). Admission still rejects the corrupt canonical value
            # until the user saves a valid Session selection.
            return ExecutionAccessPreset.ASK

    @staticmethod
    def _execution_security_for(
        messages: list[SessionMessage],
    ) -> ExecutionSecurityProfile | None:
        """Recover one canonical Turn snapshot without guessing on damage."""

        selected: ExecutionSecurityProfile | None = None
        for message in messages:
            metadata = message.metadata or {}
            if "executionSecurityProfile" not in metadata:
                continue
            raw = metadata["executionSecurityProfile"]
            if raw is None:
                # Explicit null is the canonical representation of an older
                # Turn that predates complete security snapshots.
                continue
            parsed = ExecutionSecurityProfile.from_dict(raw)
            if parsed is None:
                raise ConflictError(
                    "canonical Session contains an invalid execution security "
                    "profile; projection rebuild stopped fail-closed"
                )
            if selected is not None and parsed != selected:
                raise ConflictError(
                    "canonical Session contains conflicting execution security "
                    "profiles for one Turn"
                )
            selected = parsed
        return selected

    @staticmethod
    def _goal_id_for(messages: list[SessionMessage]) -> str | None:
        selected: str | None = None
        for message in messages:
            raw = (message.metadata or {}).get("goalId")
            if raw is None:
                continue
            candidate = str(raw).strip()
            if not candidate.startswith("goal_"):
                raise ConflictError("canonical Session contains an invalid goalId")
            if selected is not None and candidate != selected:
                raise ConflictError(
                    "canonical Session contains conflicting goalId values"
                )
            selected = candidate
        return selected

    @staticmethod
    def _execution_class_for(messages: list[SessionMessage]) -> ExecutionClass:
        selected: ExecutionClass | None = None
        for message in messages:
            raw = (message.metadata or {}).get("executionClass")
            if raw is None:
                continue
            try:
                candidate = ExecutionClass(str(raw))
            except ValueError as exc:
                raise ConflictError(
                    "canonical Session contains an invalid executionClass"
                ) from exc
            if selected is not None and candidate is not selected:
                raise ConflictError(
                    "canonical Session contains conflicting executionClass values"
                )
            selected = candidate
        return selected or ExecutionClass.INTERACTIVE

    @staticmethod
    def _parse_time(raw: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except (TypeError, ValueError):
            return utc_now()

    @staticmethod
    def _is_within(workspace: Path, project: Path) -> bool:
        workspace = workspace.expanduser().resolve(strict=False)
        project = project.expanduser().resolve(strict=False)
        return workspace == project or workspace.is_relative_to(project)

    @staticmethod
    def _existing_directory(path: str) -> Path:
        if not str(path).strip():
            raise InvalidArgumentError("workspace path must not be empty")
        try:
            workspace = Path(path).expanduser().resolve(strict=True)
        except OSError as exc:
            raise InvalidArgumentError(
                f"workspace path does not exist: {path}"
            ) from exc
        if not workspace.is_dir():
            raise InvalidArgumentError("workspace path must be a directory")
        return workspace

    @classmethod
    def _workspace_within(cls, workspace_path: str, project_path: str) -> Path:
        workspace = cls._existing_directory(workspace_path)
        if not cls._is_within(workspace, Path(project_path)):
            raise WorkspaceOutOfScopeError(
                f"workspace is outside project boundary: {workspace}"
            )
        return workspace

    @staticmethod
    def _configured_default_preset(workspace: Path):
        """The configured default composition for new Sessions, if usable.

        Failures are deliberately swallowed: a stale preset name (or an
        unreadable config) must never block creating a Session — it simply
        starts with the default composition, and the roster view is where a
        broken preset gets surfaced.
        """
        try:
            configured = load_config_for_workspace(
                workspace
            ).agents.defaults.default_preset
        except ConfigError:
            return None
        if configured is None or not configured.strip():
            return None
        try:
            return resolve_agent_preset(configured.strip(), workspace)
        except AgentPresetError:
            logger.warning(
                "configured default agent preset %r is not resolvable; "
                "starting the Session without it",
                configured,
            )
            return None
