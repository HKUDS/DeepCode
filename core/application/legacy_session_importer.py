"""Compatibility import from another SessionStore into the canonical store.

Established sessions in the application's own store are never "legacy" and
are projected directly by :class:`ThreadService`. This adapter exists only for
an explicitly supplied external store. Source files remain read-only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from core.application.errors import (
    ProjectNotFoundError,
    WorkspaceOutOfScopeError,
)
from core.application.event_service import EventBroker
from core.application.views import thread_view
from core.domain.common import utc_now
from core.domain.item import Item, ItemKind, ItemStatus
from core.domain.thread import Thread, ThreadMode
from core.domain.turn import Turn, TurnStatus
from core.domain.workflow import WorkflowRun, WorkflowStatus
from core.persistence.database import Database
from core.persistence.event_repository import EventRepository
from core.persistence.execution_repository import ItemRepository, TurnRepository
from core.persistence.legacy_import_repository import LegacyImportRepository
from core.persistence.project_repository import ProjectRepository
from core.persistence.thread_repository import ThreadRepository
from core.persistence.workflow_repository import WorkflowRepository
from core.sessions import Session, SessionMessage, SessionStore


class LegacySessionImporter:
    def __init__(
        self,
        database: Database,
        store: SessionStore,
        target_store: SessionStore,
        broker: EventBroker,
    ) -> None:
        self.database = database
        self.store = store
        self.target_store = target_store
        self.broker = broker

    def import_session(self, session_id: str, *, project_id: str) -> Thread:
        source = self.store.get_session(session_id)
        if source is None:
            raise ValueError(f"legacy session not found: {session_id}")
        source_key = f"{self.store.root.resolve()}::{session_id}"

        with self.database.read() as connection:
            existing_thread_id = LegacyImportRepository(connection).imported_thread_id(
                source_key
            )
            if existing_thread_id is not None:
                imported = ThreadRepository(connection).get(existing_thread_id)
                if imported is not None:
                    return imported

        canonical = self._copy_into_canonical_store(source, source_key)

        with self.database.transaction() as connection:
            imports = LegacyImportRepository(connection)
            existing_thread_id = imports.imported_thread_id(source_key)
            threads = ThreadRepository(connection)
            if existing_thread_id is not None:
                imported = threads.get(existing_thread_id)
                if imported is None:  # pragma: no cover - protected by foreign keys
                    raise RuntimeError(
                        "legacy import record points to a missing thread"
                    )
                return imported

            project = ProjectRepository(connection).get(project_id)
            if project is None:
                raise ProjectNotFoundError(f"project not found: {project_id}")
            workspace = self._workspace_for(canonical, project.canonical_path)
            mode = self._mode_for(canonical)
            created = self._parse_time(canonical.created_at)
            updated = self._parse_time(canonical.updated_at)
            thread = Thread(
                id=canonical.session_id,
                project_id=project_id,
                title=canonical.title.strip()
                or f"Imported session {canonical.session_id}",
                mode=mode,
                workspace_path=str(workspace),
                created_at=created,
                updated_at=max(created, updated),
            )
            existing = threads.get(thread.id)
            if existing is not None:
                imports.add(
                    source_key=source_key,
                    source_session_id=source.session_id,
                    project_id=existing.project_id,
                    thread_id=existing.id,
                    imported_at=utc_now(),
                )
                return existing
            threads.add(thread)
            turn_count, item_count, last_turn = self._import_messages(
                connection, thread, canonical
            )
            workflow_count = self._import_workflows(
                connection, thread, canonical, last_turn
            )
            event = EventRepository(connection).append(
                thread_id=thread.id,
                type="session.imported",
                payload={
                    "thread": thread_view(thread),
                    "sourceSessionId": source.session_id,
                    "turnCount": turn_count,
                    "itemCount": item_count,
                    "workflowCount": workflow_count,
                },
            )
            imports.add(
                source_key=source_key,
                source_session_id=source.session_id,
                project_id=project_id,
                thread_id=thread.id,
                imported_at=utc_now(),
            )
        self.broker.publish(event)
        return thread

    def _copy_into_canonical_store(
        self,
        source: Session,
        source_key: str,
    ) -> Session:
        if self.store.root.resolve() == self.target_store.root.resolve():
            return source
        metadata = {
            **(source.metadata or {}),
            "import_source_key": source_key,
        }
        try:
            target = self.target_store.create_session(
                session_id=source.session_id,
                title=source.title,
                metadata=metadata,
            )
        except FileExistsError:
            existing = self.target_store.get_session(source.session_id)
            if (
                existing is not None
                and existing.metadata.get("import_source_key") == source_key
            ):
                return existing
            target = self.target_store.create_session(
                title=source.title,
                metadata=metadata,
            )
        for message in source.messages:
            self.target_store.append_message(
                target.session_id,
                message.role,
                message.content,
                task_id_ref=message.task_id_ref,
                metadata=message.metadata,
            )
        for task in source.tasks:
            self.target_store.attach_task(
                target.session_id,
                task.task_id,
                task_kind=task.task_kind,
                task_dir=task.task_dir,
                status=task.status,
                metadata=task.metadata,
            )
        copied = self.target_store.get_session(target.session_id)
        if copied is None:  # pragma: no cover - target was just written
            raise RuntimeError("canonical session copy disappeared")
        return copied

    def _import_messages(
        self,
        connection,
        thread: Thread,
        source: Session,
    ) -> tuple[int, int, Turn | None]:
        turn_repository = TurnRepository(connection)
        item_repository = ItemRepository(connection)
        groups = self._message_groups(source.messages)
        item_count = 0
        last_turn: Turn | None = None
        for ordinal, messages in enumerate(groups, start=1):
            first = messages[0]
            prompt = (
                first.content if first.role == "user" else "Imported legacy messages"
            )
            timestamps = [self._parse_time(message.timestamp) for message in messages]
            last_turn = Turn(
                thread_id=thread.id,
                ordinal=ordinal,
                prompt=prompt,
                status=TurnStatus.COMPLETED,
                stop_reason="legacy_import",
                started_at=min(timestamps),
                completed_at=max(timestamps),
            )
            turn_repository.add(last_turn)
            for item_ordinal, (message, created_at) in enumerate(
                zip(messages, timestamps, strict=True), start=1
            ):
                kind = (
                    ItemKind.USER_MESSAGE
                    if message.role == "user"
                    else ItemKind.ASSISTANT_MESSAGE
                )
                item_repository.add(
                    Item(
                        thread_id=thread.id,
                        turn_id=last_turn.id,
                        ordinal=item_ordinal,
                        kind=kind,
                        status=ItemStatus.COMPLETED,
                        summary=self._summary(message.content),
                        payload={
                            "text": message.content,
                            "legacyRole": message.role,
                            "taskIdRef": message.task_id_ref,
                            "metadata": message.metadata or {},
                        },
                        created_at=created_at,
                        updated_at=created_at,
                    )
                )
                item_count += 1
        return len(groups), item_count, last_turn

    def _import_workflows(
        self,
        connection,
        thread: Thread,
        source: Session,
        last_turn: Turn | None,
    ) -> int:
        if not source.tasks:
            return 0
        if last_turn is None:
            now = self._parse_time(source.updated_at)
            last_turn = Turn(
                thread_id=thread.id,
                ordinal=1,
                prompt="Imported legacy workflow session",
                status=TurnStatus.COMPLETED,
                stop_reason="legacy_import",
                started_at=now,
                completed_at=now,
            )
            TurnRepository(connection).add(last_turn)
        repository = WorkflowRepository(connection)
        for task in source.tasks:
            status = self._workflow_status(task.status)
            created = self._parse_time(task.created_at)
            updated = self._parse_time(task.updated_at)
            repository.add(
                WorkflowRun(
                    thread_id=thread.id,
                    turn_id=last_turn.id,
                    kind=task.task_kind or "legacy",
                    status=status,
                    current_stage=None,
                    checkpoint={
                        "legacyTaskId": task.task_id,
                        "legacyTaskDir": task.task_dir,
                        "metadata": task.metadata or {},
                    },
                    created_at=created,
                    updated_at=max(created, updated),
                    completed_at=max(created, updated) if status.is_terminal else None,
                )
            )
        return len(source.tasks)

    @staticmethod
    def _message_groups(messages: list[SessionMessage]) -> list[list[SessionMessage]]:
        groups: list[list[SessionMessage]] = []
        for message in messages:
            if message.role == "user" or not groups:
                groups.append([message])
            else:
                groups[-1].append(message)
        return groups

    @staticmethod
    def _workspace_for(source: Session, project_path: str) -> Path:
        project = Path(project_path).resolve()
        raw = str((source.metadata or {}).get("workspace") or project)
        workspace = Path(raw).expanduser().resolve()
        if not workspace.is_dir() or not workspace.is_relative_to(project):
            raise WorkspaceOutOfScopeError(
                f"legacy workspace is outside project boundary: {workspace}"
            )
        return workspace

    @staticmethod
    def _mode_for(source: Session) -> ThreadMode:
        task_kinds = {task.task_kind for task in source.tasks}
        return ThreadMode.PAPER if "paper" in task_kinds else ThreadMode.CODE

    @staticmethod
    def _workflow_status(raw: str) -> WorkflowStatus:
        normalized = raw.strip().lower()
        return {
            "running": WorkflowStatus.RUNNING,
            "waiting": WorkflowStatus.WAITING,
            "completed": WorkflowStatus.COMPLETED,
            "complete": WorkflowStatus.COMPLETED,
            "failed": WorkflowStatus.FAILED,
            "cancelled": WorkflowStatus.CANCELLED,
            "canceled": WorkflowStatus.CANCELLED,
        }.get(normalized, WorkflowStatus.QUEUED)

    @staticmethod
    def _parse_time(raw: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return utc_now()

    @staticmethod
    def _summary(content: str) -> str:
        compact = " ".join(content.strip().split())
        return compact[:197] + "..." if len(compact) > 200 else compact
