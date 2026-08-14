"""SQLite persistence for the product domain."""

from core.persistence.database import Database, default_database_path
from core.persistence.automation_repository import (
    AutomationOccurrenceRepository,
    AutomationRepository,
    AutomationRevisionRepository,
    AutomationRunRepository,
)
from core.persistence.coordination_repository import RuntimeCoordinationRepository
from core.persistence.event_repository import EventRepository
from core.persistence.errors import PersistenceConflictError
from core.persistence.execution_repository import (
    ApprovalGrantRepository,
    ApprovalRepository,
    ItemRepository,
    TurnRepository,
)
from core.persistence.legacy_import_repository import LegacyImportRepository
from core.persistence.project_repository import ProjectRepository
from core.persistence.thread_repository import ThreadRepository
from core.persistence.workflow_repository import ArtifactRepository, WorkflowRepository

__all__ = [
    "ApprovalRepository",
    "ApprovalGrantRepository",
    "ArtifactRepository",
    "AutomationRepository",
    "AutomationOccurrenceRepository",
    "AutomationRevisionRepository",
    "AutomationRunRepository",
    "Database",
    "EventRepository",
    "ItemRepository",
    "LegacyImportRepository",
    "PersistenceConflictError",
    "ProjectRepository",
    "RuntimeCoordinationRepository",
    "ThreadRepository",
    "TurnRepository",
    "WorkflowRepository",
    "default_database_path",
]
