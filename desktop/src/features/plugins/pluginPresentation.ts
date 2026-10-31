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
  const kind =
    component.kind === "skills"
      ? t("plugins.component.skills", "Skills")
      : t("plugins.component.mcp", "MCP servers");
  const status =
    component.status === "ready"
      ? t("plugins.component.ready", "ready")
      : component.status === "unsupported"
        ? t("plugins.component.unsupported", "not supported")
        : t("plugins.component.invalid", "invalid");
  return `${kind}: ${status}`;
}
