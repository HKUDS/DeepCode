"""Composition root used by all product frontends."""

from __future__ import annotations

from pathlib import Path

from core.application.agent_adapter import AgentSessionFactory
from core.application.application_lease import ApplicationLease
from core.application.approval_service import ApprovalService
from core.application.automation_service import AutomationService
from core.application.diagnostics_service import DiagnosticsService
from core.application.event_service import EventBroker, EventService
from core.application.execution_registry import ExecutionRegistry
from core.application.extension_service import ExtensionService
from core.application.file_service import FileService
from core.application.git_service import GitService
from core.application.mcp_service import McpService
from core.application.project_service import ProjectService
from core.application.settings_service import SettingsService
from core.application.skill_service import SkillService
from core.application.terminal_service import TerminalService
from core.application.test_service import TestService
from core.application.thread_service import ThreadService
from core.application.turn_service import TurnService
from core.application.workspace_service import WorkspaceService
from core.application.worktree_service import WorktreeService
from core.application.workflow_adapter import WorkflowRunner
from core.application.workflow_service import WorkflowService
from core.persistence.database import Database
from core.application.legacy_session_importer import LegacySessionImporter
from core.sessions import SessionStore, get_default_store


class DeepCodeApplication:
    """Own product services without depending on CLI, JSON-RPC, or Tauri."""

    def __init__(
        self,
        database: Database,
        *,
        event_queue_capacity: int = 256,
        max_concurrent_turns: int = 2,
        session_factory: AgentSessionFactory | None = None,
        session_store: SessionStore | None = None,
        workflow_runner: WorkflowRunner | None = None,
    ) -> None:
        self.database = database
        self.session_store = session_store or get_default_store()
        self._application_lease: ApplicationLease | None = None
        self.broker = EventBroker(default_capacity=event_queue_capacity)
        self.projects = ProjectService(database)
        self.settings = SettingsService(self.projects)
        self.skills = SkillService(self.projects)
        self.extensions = ExtensionService(self.projects, self.skills)
        self.mcp = McpService(self.projects)
        self.diagnostics = DiagnosticsService(
            database,
            self.projects,
            self.session_store,
        )
        self.threads = ThreadService(database, self.broker, self.session_store)
        self.events = EventService(database, self.broker)
        self.workspaces = WorkspaceService(database)
        self.files = FileService(database, self.workspaces)
        self.git = GitService(database, self.workspaces)
        self.terminals = TerminalService(self.workspaces)
        self.worktrees = WorktreeService(database, self.broker, self.git)
        self.worktrees.set_terminal_activity_check(self.terminals.active_for_thread)
        self.tests = TestService(database, self.broker, self.workspaces)
        self.executions = ExecutionRegistry(max_concurrent=max_concurrent_turns)
        self.approvals = ApprovalService(database, self.broker)
        self.turns = TurnService(
            database,
            self.broker,
            self.approvals,
            self.executions,
            session_factory=session_factory,
            session_store=self.session_store,
        )
        self.automations = AutomationService(
            database,
            self.broker,
            self.projects,
            self.threads,
            self.turns,
        )
        self.workflows = WorkflowService(
            database,
            self.broker,
            self.workspaces,
            self.executions,
            session_store=self.session_store,
            runner=workflow_runner,
        )

    def legacy_importer(self, store: SessionStore) -> LegacySessionImporter:
        return LegacySessionImporter(
            self.database,
            store,
            self.session_store,
            self.broker,
        )

    @classmethod
    def open(
        cls,
        database_path: Path | str | None = None,
        *,
        event_queue_capacity: int = 256,
        max_concurrent_turns: int = 2,
        session_factory: AgentSessionFactory | None = None,
        session_store: SessionStore | None = None,
        workflow_runner: WorkflowRunner | None = None,
    ) -> "DeepCodeApplication":
        database = Database(database_path)
        database.initialize()
        lease = ApplicationLease.acquire(database.path)
        application: DeepCodeApplication | None = None
        try:
            application = cls(
                database,
                event_queue_capacity=event_queue_capacity,
                max_concurrent_turns=max_concurrent_turns,
                session_factory=session_factory,
                session_store=session_store,
                workflow_runner=workflow_runner,
            )
            application._application_lease = lease
            if lease.recovery_owner:
                application.threads.reconcile()
                application.workflows.recover_incomplete()
                application.turns.recover_incomplete()
            lease.downgrade()
            return application
        except BaseException:
            if application is not None:
                application.close()
            else:
                lease.close()
            raise

    def close(self) -> None:
        try:
            self.automations.close()
            self.terminals.close_all()
            self.executions.close(cleanup=self.turns.close_live_sessions)
        finally:
            lease = self._application_lease
            self._application_lease = None
            if lease is not None:
                lease.close()
