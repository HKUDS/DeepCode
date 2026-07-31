"""UI- and transport-independent product domain."""

from core.domain.approval import (
    Approval,
    ApprovalCategory,
    ApprovalGrant,
    ApprovalStatus,
)
from core.domain.artifact import Artifact
from core.domain.automation import (
    Automation,
    AutomationActivationStatus,
    AutomationOccurrence,
    AutomationRevision,
    AutomationRun,
    AutomationRunStatus,
    AutomationScheduleKind,
    AutomationStatus,
    AutomationTrigger,
)
from core.domain.event import DomainEvent
from core.domain.execution_permission import ExecutionPermissionMode
from core.domain.execution_profile import ExecutionProfile, ExecutionSelection
from core.domain.execution_security import (
    ApprovalPolicy,
    ExecutionAccessPreset,
    ExecutionPermissionRuleAction,
    ExecutionPermissionRuleSnapshot,
    ExecutionSecurityProfile,
    FilesystemScope,
    normalize_permission_rules,
)
from core.domain.item import Item, ItemKind, ItemStatus
from core.domain.message_provenance import (
    ClientSurface,
    TurnInputDelivery,
    TurnInputSource,
)
from core.domain.project import Project, TrustState
from core.domain.runtime_coordination import (
    ExecutionClass,
    ResourceClaim,
    ResourceLease,
    RuntimeWorker,
)
from core.domain.thread import Thread, ThreadMode, ThreadStatus
from core.domain.thread_goal import (
    GoalDecisionSource,
    GoalEvidenceRef,
    GoalOutcome,
    ThreadGoal,
    ThreadGoalStatus,
)
from core.domain.turn import Turn, TurnExecutor, TurnStatus
from core.domain.workflow import WorkflowRun, WorkflowStatus

__all__ = [
    "Approval",
    "ApprovalCategory",
    "ApprovalGrant",
    "ApprovalPolicy",
    "ApprovalStatus",
    "Artifact",
    "Automation",
    "AutomationActivationStatus",
    "AutomationOccurrence",
    "AutomationRevision",
    "AutomationRun",
    "AutomationRunStatus",
    "AutomationScheduleKind",
    "AutomationStatus",
    "AutomationTrigger",
    "ClientSurface",
    "DomainEvent",
    "ExecutionAccessPreset",
    "ExecutionClass",
    "ExecutionPermissionMode",
    "ExecutionPermissionRuleAction",
    "ExecutionPermissionRuleSnapshot",
    "ExecutionProfile",
    "ExecutionSecurityProfile",
    "ExecutionSelection",
    "FilesystemScope",
    "GoalDecisionSource",
    "GoalEvidenceRef",
    "GoalOutcome",
    "Item",
    "ItemKind",
    "ItemStatus",
    "Project",
    "ResourceClaim",
    "ResourceLease",
    "RuntimeWorker",
    "Thread",
    "ThreadGoal",
    "ThreadGoalStatus",
    "ThreadMode",
    "ThreadStatus",
    "TrustState",
    "Turn",
    "TurnExecutor",
    "TurnInputDelivery",
    "TurnInputSource",
    "TurnStatus",
    "WorkflowRun",
    "WorkflowStatus",
    "normalize_permission_rules",
]
