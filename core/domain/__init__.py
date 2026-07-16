"""UI- and transport-independent product domain."""

from core.domain.approval import Approval, ApprovalCategory, ApprovalStatus
from core.domain.artifact import Artifact
from core.domain.automation import (
    Automation,
    AutomationRun,
    AutomationRunStatus,
    AutomationScheduleKind,
    AutomationStatus,
    AutomationTrigger,
)
from core.domain.event import DomainEvent
from core.domain.item import Item, ItemKind, ItemStatus
from core.domain.project import Project, TrustState
from core.domain.thread import Thread, ThreadMode, ThreadStatus
from core.domain.turn import Turn, TurnStatus
from core.domain.workflow import WorkflowRun, WorkflowStatus

__all__ = [
    "Approval",
    "ApprovalCategory",
    "ApprovalStatus",
    "Artifact",
    "Automation",
    "AutomationRun",
    "AutomationRunStatus",
    "AutomationScheduleKind",
    "AutomationStatus",
    "AutomationTrigger",
    "DomainEvent",
    "Item",
    "ItemKind",
    "ItemStatus",
    "Project",
    "Thread",
    "ThreadMode",
    "ThreadStatus",
    "TrustState",
    "Turn",
    "TurnStatus",
    "WorkflowRun",
    "WorkflowStatus",
]
