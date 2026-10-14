import type { TFunction } from "i18next";

import type { SidecarStatus } from "../../rpc/contracts";

/** The local service's state, as the sidebar names it. */
export function runtimePhaseLabel(
  phase: SidecarStatus["phase"],
  t: TFunction,
): string {
  switch (phase) {
    case "ready":
      return t("sidebar.agentReady", "Local agent ready");
    case "starting":
      return t("sidebar.agentStarting", "Starting local agent");
    case "stopping":
      return t("sidebar.agentStopping", "Stopping local agent");
    case "stopped":
      return t("sidebar.agentStopped", "Local agent stopped");
    case "crashed":
      return t("sidebar.agentCrashed", "Local agent stopped unexpectedly");
  }
}
