"""Optional maintenance utilities outside the interactive Agent loop."""

from core.loop.guards import (
    EvidenceLedger,
    GuardIntervention,
    LoopGuards,
    ProgressGuard,
    StormBreaker,
    delegation_admission,
)

__all__ = [
    "AutodreamResult",
    "consolidate_memory",
    # REASONIX anti-wandering guards (P3.5)
    "EvidenceLedger",
    "GuardIntervention",
    "ProgressGuard",
    "StormBreaker",
    "LoopGuards",
    "delegation_admission",
]


def __getattr__(name: str):
    """Lazily expose the autodream API.

    ``core.loop.autodream`` imports ``core.agent_setup`` (and transitively
    ``core.compat.agent`` -> ``core.agent_runtime.runner``), so eagerly
    importing it here would create a cycle when ``runner`` itself imports this
    package (e.g. for the REASONIX loop guards). Load it only on first access
    -- by then package init has completed and the import chain is safe.
    """
    if name in ("AutodreamResult", "consolidate_memory"):
        from core.loop.autodream import AutodreamResult, consolidate_memory

        value = AutodreamResult if name == "AutodreamResult" else consolidate_memory
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
