import type { TFunction } from "i18next";

import type { AgentPresetEntry } from "../../generated/app-server";

/** Where an agent preset is defined, in words. */
export function presetTrustLabel(trust: AgentPresetEntry["trust"], t: TFunction): string {
  switch (trust) {
    case "system":
      return t("presets.trust.system", "Built in");
    case "user":
      return t("presets.trust.user", "User settings");
    case "project":
      return t("presets.trust.project", "This project");
  }
}
