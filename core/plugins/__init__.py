"""Local, inert Plugin discovery and Skill contribution."""

from core.plugins.domain import (
    PluginError,
    PluginLoadResult,
    PluginPackage,
    PluginRegistration,
    PluginRegistryError,
    PluginSnapshot,
    PluginStatus,
    PluginValidationError,
    ResolvedPlugin,
)
from core.plugins.host import LocalPluginHost
from core.plugins.registry import LocalPluginRegistry
from core.plugins.resolver import resolve_plugin

__all__ = [
    "LocalPluginHost",
    "LocalPluginRegistry",
    "PluginError",
    "PluginLoadResult",
    "PluginPackage",
    "PluginRegistration",
    "PluginRegistryError",
    "PluginSnapshot",
    "PluginStatus",
    "PluginValidationError",
    "ResolvedPlugin",
    "resolve_plugin",
]
