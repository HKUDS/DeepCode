"""Frontend-neutral Model Context Protocol client runtime.

The package deliberately owns no CLI, Desktop, Plugin, or paper workflow
policy.  Those surfaces contribute validated server definitions; this package
resolves them into one session-scoped runtime and exposes ordinary DeepCode
tools.
"""

from core.mcp.models import (
    McpApprovalMode,
    McpConfigurationError,
    McpCredentialRef,
    McpRuntimePlan,
    McpServerDefinition,
    McpServerPolicyOverlay,
    McpServerSource,
    McpStartupError,
    McpToolIdentity,
    McpToolPolicy,
    ResolvedMcpServer,
    validate_no_literal_secrets,
)
from core.mcp.oauth import (
    McpAuthorizationRequiredError,
    McpOAuthCredentialStore,
    McpOAuthManager,
    create_mcp_oauth_provider,
)
from core.mcp.presets import McpPresetCatalog
from core.mcp.probe import McpProbeResult, probe_mcp_server
from core.mcp.resolver import McpConfigResolver, resolve_plugin_servers
from core.mcp.runtime import McpSessionRuntime

__all__ = [
    "McpApprovalMode",
    "McpAuthorizationRequiredError",
    "McpConfigResolver",
    "McpConfigurationError",
    "McpCredentialRef",
    "McpOAuthCredentialStore",
    "McpOAuthManager",
    "McpPresetCatalog",
    "McpProbeResult",
    "McpRuntimePlan",
    "McpServerDefinition",
    "McpServerPolicyOverlay",
    "McpServerSource",
    "McpSessionRuntime",
    "McpStartupError",
    "McpToolIdentity",
    "McpToolPolicy",
    "ResolvedMcpServer",
    "create_mcp_oauth_provider",
    "probe_mcp_server",
    "resolve_plugin_servers",
    "validate_no_literal_secrets",
]
