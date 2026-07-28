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
from core.domain.execution_profile import ExecutionProfile, ExecutionSelection
from core.domain.item import Item, ItemKind, ItemStatus
from core.domain.message_provenance import (
    ClientSurface,
    TurnInputDelivery,
    TurnInputSource,
)
from core.domain.project import Project, TrustState
from core.domain.thread import Thread, ThreadMode, ThreadStatus
from core.domain.thread_goal import (
    GoalDecisionSource,
    GoalEvidenceRef,
    GoalOutcome,
    ThreadGoal,
    ThreadGoalStatus,
)
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
    "ExecutionProfile",
    "ExecutionSelection",
    "Item",
    "ItemKind",
    "ItemStatus",
    "ClientSurface",
    "TurnInputDelivery",
    "TurnInputSource",
    "Project",
    "Thread",
    "ThreadMode",
    "ThreadStatus",
    "ThreadGoal",
    "ThreadGoalStatus",
    "GoalDecisionSource",
    "GoalEvidenceRef",
    "GoalOutcome",
    "TrustState",
    "Turn",
    "TurnStatus",
    "WorkflowRun",
    "WorkflowStatus",
]
