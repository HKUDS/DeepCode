"""Composition root used by all product frontends."""

from __future__ import annotations

import os

from pathlib import Path

from core.application.agent_adapter import (
    AgentSessionFactory,
    ConfiguredAgentSessionFactory,
    DefaultAgentSessionFactory,
)
from core.application.application_lease import ApplicationLease
from core.application.approval_service import ApprovalService
from core.application.automation_schedule_policy import AutomationSchedulePolicy
from core.application.automation_scheduler import AutomationScheduler
from core.application.automation_service import AutomationService
from core.application.diagnostics_service import DiagnosticsService
from core.application.errors import UpgradeRequiresExclusiveAccessError
from core.application.event_service import (
    DEFAULT_RELAY_BATCH_SIZE,
    DEFAULT_RELAY_POLL_INTERVAL,
    DurableEventRelay,
    EventBroker,
    EventService,
)
from core.application.execution_coordinator import ExecutionCoordinator
from core.application.execution_handler_registry import ExecutionHandlerRegistry
from core.application.execution_registry import ExecutionRegistry
from core.application.extension_service import ExtensionService
from core.application.file_service import FileService
from core.application.git_service import GitService
from core.application.goal_extension import GoalExtension
from core.application.legacy_session_importer import LegacySessionImporter
from core.application.llm_configuration_service import LLMConfigurationService
from core.application.mcp_service import McpService
from core.application.plugin_service import PluginService
from core.application.project_service import ProjectService
from core.application.session_deletion_service import SessionDeletionService
from core.application.settings_service import SettingsService
from core.application.skill_service import SkillService
from core.application.terminal_service import TerminalService
from core.application.test_service import TestService
from core.application.thread_service import ThreadService
from core.application.turn_service import TurnService
from core.application.workflow_adapter import WorkflowRunner
from core.application.workflow_service import WorkflowService
from core.application.workspace_service import WorkspaceService
from core.application.worktree_service import WorktreeService
from core.domain.common import utc_now
from core.domain.turn import TurnExecutor
from core.persistence.database import Database
from core.persistence.event_repository import EventRepository
from core.persistence.migrations import LATEST_SCHEMA_VERSION
from core.plugins.host import LocalPluginHost
from core.providers.credentials import CredentialStore
from core.sessions import (
    SessionStore,
    ThreadGoalStore,
    get_default_store,
)
from core.skills.host import SkillWorkspaceRegistry


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
        automation_schedule_policy: AutomationSchedulePolicy | None = None,
        host_surface: str = "application",
        worker_stale_after: float = 15.0,
        run_automation_scheduler: bool = False,
        event_relay_poll_interval: float = DEFAULT_RELAY_POLL_INTERVAL,
        event_relay_batch_size: int = DEFAULT_RELAY_BATCH_SIZE,
    ) -> None:
        self.database = database
        self.run_automation_scheduler = run_automation_scheduler
        self.session_store = session_store or get_default_store()
        self._application_lease: ApplicationLease | None = None
        self.broker = EventBroker(default_capacity=event_queue_capacity)
        self.event_relay = DurableEventRelay(
            database,
            self.broker,
            poll_interval=event_relay_poll_interval,
            batch_size=event_relay_batch_size,
        )
        self.projects = ProjectService(database)
        self.settings = SettingsService(self.projects)
        self.credentials = CredentialStore()
        self.llm = LLMConfigurationService(
            self.projects,
            credential_store=self.credentials,
        )
        self.skill_hosts = SkillWorkspaceRegistry()
        self.plugins = PluginService(LocalPluginHost(self.skill_hosts))
        effective_session_factory = session_factory or DefaultAgentSessionFactory()
        if isinstance(effective_session_factory, ConfiguredAgentSessionFactory):
            effective_session_factory.configure_plugin_mcp(
                self.plugins.host.mcp_servers,
                revision_provider=lambda: self.plugins.host.snapshot().revision,
            )
        self.skills = SkillService(self.projects, self.skill_hosts)
        self.extensions = ExtensionService(self.projects, self.skills)
        self.mcp = McpService(
            self.projects,
            plugin_servers=self.plugins.host.mcp_servers,
            credential_resolver=self.llm.resolve_api_credential,
        )
        if isinstance(effective_session_factory, ConfiguredAgentSessionFactory):
            effective_session_factory.configure_mcp_status_observer(
                self.mcp.publish_runtime_status
            )
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
        self.terminals = TerminalService(self.workspaces, self.session_store)
        self.worktrees = WorktreeService(
            database,
            self.broker,
            self.git,
            self.session_store,
        )
        self.worktrees.set_terminal_activity_check(self.terminals.active_for_thread)
        self.tests = TestService(database, self.broker, self.workspaces)
        self.executions = ExecutionRegistry(max_concurrent=max_concurrent_turns)
        self.approvals = ApprovalService(database, self.broker)
        self.turns = TurnService(
            database,
            self.broker,
            self.approvals,
            self.executions,
            session_factory=effective_session_factory,
            session_store=self.session_store,
            llm_configuration=self.llm,
            skill_hosts=self.skill_hosts,
            holder_label=f"{host_surface} (pid {os.getpid()})",
        )
        self.workflows = WorkflowService(
            database,
            self.broker,
            self.workspaces,
            self.executions,
            session_store=self.session_store,
            runner=workflow_runner,
            llm_configuration=self.llm,
        )
        self.execution_handlers = ExecutionHandlerRegistry()
        self.execution_handlers.register(TurnExecutor.AGENT, self.turns)
        self.execution_handlers.register(TurnExecutor.WORKFLOW, self.workflows)
        self.execution_coordinator = ExecutionCoordinator(
            database,
            self.execution_handlers.start_claimed_execution,
            max_concurrent_turns=max_concurrent_turns,
            surface=host_surface,
            stale_worker_after=worker_stale_after,
            on_start_failure=self.execution_handlers.fail_claimed_start,
            on_orphaned_execution=(self.execution_handlers.recover_orphaned_execution),
            on_cancel_requested=self.execution_handlers.cancel_claimed_execution,
        )
        self.turns.configure_execution_coordinator(self.execution_coordinator)
        self.workflows.configure_execution_coordinator(self.execution_coordinator)
        self.thread_goal_store = ThreadGoalStore(self.session_store)
        self.goals = GoalExtension(
            self.thread_goal_store,
            self.turns,
            update_sink=self._publish_goal_update,
            lifecycle_sink=self._publish_goal_lifecycle,
        )
        self.turns.configure_goal_runtime(
            self.goals,
            context_provider=self.goals.turn_association,
            submission_scope=self.goals.turn_submission_scope,
        )
        self.turns.add_settled_listener(self.goals.on_turn_settled)
        self.deletions = SessionDeletionService(
            database,
            self.session_store,
            self.thread_goal_store,
            ensure_projection=self.threads.read,
            on_deleted=self._on_session_deleted,
        )
        self.automations = AutomationService(
            database,
            self.broker,
            self.projects,
            self.threads,
            self.turns,
            self.goals,
            schedule_policy=automation_schedule_policy,
        )
        self.automation_scheduler = AutomationScheduler(
            database.path,
            run_due=self.automations.run_due,
            next_due=self.automations.next_due,
        )
        self.automations.set_scheduler(self.automation_scheduler)
        self.turns.add_admission_guard(self.automations.ensure_turn_admitted)
        self.goals.add_continuation_guard(self.automations.ensure_goal_continuable)
        self.turns.add_settled_listener(self.automations.on_turn_settled)

    def legacy_importer(self, store: SessionStore) -> LegacySessionImporter:
        return LegacySessionImporter(
            self.database,
            store,
            self.session_store,
            self.broker,
        )

    def _publish_goal_update(self, thread_id, record) -> None:
        from core.application.views import goal_outcome_view, thread_goal_view

        outcome = self.goals.read_outcome(thread_id) if record is not None else None
        with self.database.transaction() as connection:
            event = EventRepository(connection).append(
                thread_id=thread_id,
                type="goal.updated",
                payload={
                    "goal": (thread_goal_view(record) if record is not None else None),
                    "outcome": (
                        goal_outcome_view(outcome) if outcome is not None else None
                    ),
                },
            )
        self.broker.publish(event)

    def _publish_goal_lifecycle(
        self,
        thread_id: str,
        event_type: str,
        payload: dict,
    ) -> None:
        with self.database.transaction() as connection:
            event = EventRepository(connection).append(
                thread_id=thread_id,
                type=event_type,
                payload=payload,
            )
        self.broker.publish(event)

    def _on_session_deleted(self, thread_id: str) -> None:
        self.threads.forget(thread_id)
        self.turns.discard_session_runtime(thread_id)

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
        automation_schedule_policy: AutomationSchedulePolicy | None = None,
        host_surface: str = "application",
        worker_stale_after: float = 15.0,
        run_automation_scheduler: bool = False,
        event_relay_poll_interval: float = DEFAULT_RELAY_POLL_INTERVAL,
        event_relay_batch_size: int = DEFAULT_RELAY_BATCH_SIZE,
    ) -> DeepCodeApplication:
        database = Database(database_path)
        lease = ApplicationLease.acquire(database.path)
        application: DeepCodeApplication | None = None
        try:
            if lease.recovery_owner:
                database.initialize()
            else:
                installed_version = database.schema_version()
                if installed_version != LATEST_SCHEMA_VERSION:
                    raise UpgradeRequiresExclusiveAccessError(
                        installed_version,
                        LATEST_SCHEMA_VERSION,
                    )
            application = cls(
                database,
                event_queue_capacity=event_queue_capacity,
                max_concurrent_turns=max_concurrent_turns,
                session_factory=session_factory,
                session_store=session_store,
                workflow_runner=workflow_runner,
                automation_schedule_policy=automation_schedule_policy,
                host_surface=host_surface,
                worker_stale_after=worker_stale_after,
                run_automation_scheduler=run_automation_scheduler,
                event_relay_poll_interval=event_relay_poll_interval,
                event_relay_batch_size=event_relay_batch_size,
            )
            application._application_lease = lease
            application.event_relay.start()
            application.execution_coordinator.start(background=False)
            if lease.recovery_owner:
                application.deletions.recover_pending()
                application.threads.reconcile()
                application.workflows.recover_incomplete()
                application.turns.recover_incomplete(
                    resume_queued=application.turns.may_resume_queued_after_restart
                )
            application.execution_coordinator.recover_candidates(
                heartbeat_before=utc_now(),
            )
            if lease.recovery_owner:
                application.goals.recover_incomplete()
                application.automations.reconcile_runs()
            lease.downgrade()
            application.execution_coordinator.start_background()
            if application.run_automation_scheduler:
                application.automation_scheduler.start()
            return application
        except BaseException:
            if application is not None:
                application.close()
            else:
                lease.close()
            raise

    def close(self) -> None:
        errors: list[Exception] = []

        def attempt(stage: str, operation) -> None:
            try:
                operation()
            except Exception as exc:  # noqa: BLE001 - aggregate every cleanup failure
                exc.add_note(f"DeepCode shutdown stage: {stage}")
                errors.append(exc)

        attempt("event relay", self.event_relay.close)
        attempt("execution coordinator quiesce", self.execution_coordinator.quiesce)
        attempt("automation scheduler", self.automation_scheduler.close)
        attempt("terminal sessions", self.terminals.close_all)
        attempt(
            "execution runtime",
            lambda: self.executions.close(cleanup=self.turns.close_live_sessions),
        )
        attempt("Plugin service", self.plugins.close)
        attempt("Skill service", self.skills.close)
        attempt("Skill workspace registry", self.skill_hosts.close)
        attempt("MCP service", self.mcp.close)
        attempt(
            "queued execution settlement",
            lambda: self.execution_handlers.interrupt_unclaimed_queued_for_worker(
                self.execution_coordinator.worker_id
            ),
        )
        if self.execution_coordinator.active_claims:
            errors.append(
                RuntimeError(
                    "cannot close DeepCode while execution claims remain active"
                )
            )
        else:
            attempt("execution coordinator", self.execution_coordinator.close)

        # A failed stage may still own background work. Retain the application
        # lifetime lease so another process cannot mistake that work for crash
        # residue; callers may retry close after handling the failure.
        if not errors:
            attempt(
                "automation admission guard",
                lambda: self.turns.remove_admission_guard(
                    self.automations.ensure_turn_admitted
                ),
            )
            attempt(
                "automation settled listener",
                lambda: self.turns.remove_settled_listener(
                    self.automations.on_turn_settled
                ),
            )
            attempt(
                "goal settled listener",
                lambda: self.turns.remove_settled_listener(self.goals.on_turn_settled),
            )
            if not errors:
                lease = self._application_lease
                self._application_lease = None
                if lease is not None:
                    attempt("application lifetime lease", lease.close)

        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise ExceptionGroup("DeepCode application shutdown failed", errors)
