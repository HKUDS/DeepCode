import type { TFunction } from "i18next";

import type { PluginInfo } from "../../generated/app-server";

type Component = PluginInfo["components"][number];

/** A plugin's status, in words. */
export function pluginStatusLabel(status: PluginInfo["status"], t: TFunction): string {
  switch (status) {
    case "active":
      return t("plugins.status.active", "Active");
    case "disabled":
      return t("plugins.status.disabled", "Disabled");
    case "invalid":
      return t("plugins.status.invalid", "Invalid");
  }
}

/** One component a plugin provides, in words: its kind and whether it works. */
export function pluginComponentLabel(component: Component, t: TFunction): string {
  return `${componentKindLabel(component.kind, t)}: ${componentStatusLabel(component.status, t)}`;
}

function componentKindLabel(kind: Component["kind"], t: TFunction): string {
  switch (kind) {
    case "skills":
      return t("plugins.component.skills", "Skills");
    case "mcp":
      return t("plugins.component.mcp", "MCP servers");
  }
}

function componentStatusLabel(status: Component["status"], t: TFunction): string {
  switch (status) {
    case "ready":
      return t("plugins.component.ready", "ready");
    case "unsupported":
      return t("plugins.component.unsupported", "not supported");
    case "invalid":
      return t("plugins.component.invalid", "invalid");
  }
}

/** How serious a plugin's diagnostic is, in words. */
export function pluginSeverityLabel(
  severity: PluginInfo["diagnostics"][number]["severity"],
  t: TFunction,
): string {
  switch (severity) {
    case "warning":
      return t("plugins.severity.warning", "Warning");
    case "error":
      return t("plugins.severity.error", "Error");
  }
}
