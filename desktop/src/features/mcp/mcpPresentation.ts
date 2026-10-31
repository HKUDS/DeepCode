import type { TFunction } from "i18next";

import type { McpServerInfo } from "../../generated/app-server";

/** Where a server is configured, in words. */
export function mcpSourceLabel(source: McpServerInfo["source"], t: TFunction): string {
  switch (source) {
    case "user":
      return t("mcp.source.user", "User settings");
    case "project":
      return t("mcp.source.project", "This project");
    case "plugin":
      return t("mcp.source.plugin", "Plugin");
  }
}

/** How DeepCode talks to a server, in words. */
export function mcpTransportLabel(
  transport: McpServerInfo["transport"],
  t: TFunction,
): string {
  switch (transport) {
    case "stdio":
      return t("mcp.transport.stdio", "Local command");
    case "sse":
      return t("mcp.transport.sse", "HTTP (SSE)");
    case "streamableHttp":
      return t("mcp.transport.http", "HTTP");
  }
}

/** A server's configuration state, in words. */
export function mcpConfigurationLabel(
  state: McpServerInfo["configurationState"],
  t: TFunction,
): string {
  switch (state) {
    case "configured":
      return t("mcp.configuration.configured", "Ready");
    case "invalid":
      return t("mcp.configuration.invalid", "Invalid");
    case "disabled":
      return t("mcp.configuration.disabled", "Disabled");
    case "blocked":
      return t("mcp.configuration.blocked", "Blocked by policy");
    case "missing_credentials":
      return t("mcp.configuration.missingCredentials", "Missing credentials");
  }
}

/**
 * What a server's configuration means for the user. The states with a fixed
 * meaning are said here; an invalid or blocked one keeps the service's
 * explanation, which names the specific problem.
 */
export function mcpConfigurationText(server: McpServerInfo, t: TFunction): string {
  switch (server.configurationState) {
    case "configured":
      return t("mcp.configuration.configuredHelp", "Ready to use; the connection is checked when a task needs it.");
    case "disabled":
      return t("mcp.configuration.disabledHelp", "Turned off. Enable it to use its tools.");
    case "missing_credentials":
      // The variables are listed under "Missing environment".
      return t("mcp.configuration.missingHelp", "Set the missing environment variables first.");
    default:
      return server.configurationMessage;
  }
}

/** A server's sign-in state, in words. */
export function mcpAuthLabel(state: McpServerInfo["authState"], t: TFunction): string {
  switch (state) {
    case "not_required":
      return t("mcp.auth.notRequired", "Not required");
    case "login_required":
      return t("mcp.auth.loginRequired", "Sign-in required");
    case "authorizing":
      return t("mcp.auth.authorizing", "Signing in…");
    case "authenticated":
      return t("mcp.auth.authenticated", "Signed in");
  }
}

/**
 * When a server's tool calls ask for approval, in words. The mode only adds
 * checks over the Session's access (core/harness/permissions.py): "auto" and
 * "approve" add none.
 */
export function mcpApprovalLabel(
  mode: McpServerInfo["approvalMode"],
  t: TFunction,
): string {
  switch (mode) {
    case "auto":
    case "approve":
      return t("mcp.approval.session", "Follows the Session's access");
    case "prompt":
      return t("mcp.approval.prompt", "Ask before every call");
    case "writes":
      return t("mcp.approval.writes", "Ask before calls that change things");
  }
}

/** The state shown on a server's badge: its runtime state, or disabled. */
export function mcpStatus(server: McpServerInfo): string {
  return server.enabled ? server.runtimeState : "disabled";
}

/** A server's badge, in words. */
export function mcpStatusLabel(server: McpServerInfo, t: TFunction): string {
  if (!server.enabled) return t("mcp.status.disabled", "Disabled");
  switch (server.runtimeState) {
    case "stopped":
      return t("mcp.status.stopped", "Not connected");
    case "connecting":
      return t("mcp.status.connecting", "Connecting");
    case "tested":
      return t("mcp.status.tested", "Test passed");
    case "connected":
      return t("mcp.status.connected", "Connected");
    case "failed":
      return t("mcp.status.failed", "Failed");
  }
}

/**
 * The last connection check, in words. A failure keeps the service's error,
 * which says what went wrong; the other states have fixed meanings.
 */
export function mcpRuntimeText(server: McpServerInfo, t: TFunction): string {
  switch (server.runtimeState) {
    case "stopped":
      return t("mcp.runtime.stopped", "Not connected yet in this service");
    case "connecting":
      return t("mcp.runtime.connecting", "Testing the connection…");
    case "tested":
      return t("mcp.runtime.tested", "Connection test passed");
    case "connected":
      return t("mcp.runtime.connected", "Connected");
    case "failed":
      return server.runtimeMessage;
  }
}

/** A template's category, in words; a category DeepCode does not know keeps its name. */
export function mcpCategoryLabel(category: string, t: TFunction): string {
  switch (category) {
    case "browser":
      return t("mcp.category.browser", "Browser");
    case "docs":
      return t("mcp.category.docs", "Documentation");
    case "web":
      return t("mcp.category.web", "Web");
    case "api":
      return t("mcp.category.api", "API");
    case "design":
      return t("mcp.category.design", "Design");
    case "productivity":
      return t("mcp.category.productivity", "Productivity");
    case "code":
      return t("mcp.category.code", "Code");
    case "database":
      return t("mcp.category.database", "Database");
    default:
      return category;
  }
}
