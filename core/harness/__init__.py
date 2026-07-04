"""Harness services layered on top of the agent kernel.

P1 security base (see DEEPCODE_V2_MASTER_PLAN.md §4.3):
- :mod:`core.harness.permissions` — three-valued permission engine
  (allow / ask / deny) with a non-overridable sensitive-path denylist,
  two-dimensional wildcard rules and default / plan / full_auto modes.
- :mod:`core.harness.sandbox` — platform sandbox command wrapping
  (macOS seatbelt, Linux bubblewrap) with graceful degradation.

Design rule: these modules are pure mechanism — they decide and wrap,
they never talk to models or UIs. Enforcement points live in the kernel
(``AgentRunSpec.permission_checker``) and in tool executors.
"""

from core.harness.permissions import (
    PermissionDecision,
    PermissionEngine,
    PermissionMode,
    PermissionRule,
)
from core.harness.sandbox import SandboxPolicy, sandbox_backend, wrap_shell_command

__all__ = [
    "PermissionDecision",
    "PermissionEngine",
    "PermissionMode",
    "PermissionRule",
    "SandboxPolicy",
    "sandbox_backend",
    "wrap_shell_command",
]
