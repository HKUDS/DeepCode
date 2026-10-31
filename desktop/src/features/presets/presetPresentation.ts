import type { TFunction } from "i18next";

import { configScopeLabel } from "../../app/configScope";
import type { AgentPresetEntry } from "../../generated/app-server";

/** Where an agent preset is defined, in words. */
export function presetTrustLabel(trust: AgentPresetEntry["trust"], t: TFunction): string {
  return trust === "system" ? t("presets.trust.system", "Built in") : configScopeLabel(trust, t);
}
