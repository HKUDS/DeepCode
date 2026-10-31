import type { TFunction } from "i18next";

import { configScopeLabel } from "../../app/configScope";
import type { SkillInfo } from "../../generated/app-server";

const SOURCE_FOLDERS: Record<SkillInfo["sourceRoot"], string> = {
  agents: ".agents",
  deepcode: ".deepcode",
  claude: ".claude",
  system: "",
};

/**
 * Where a Skill comes from, in words: the settings and folder of a local
 * Skill, DeepCode itself, or the provider that supplies it (by its id).
 */
export function skillOriginLabel(skill: SkillInfo, t: TFunction): string {
  switch (skill.originKind) {
    case "bundled":
      return t("skills.origin.bundled", "Built into DeepCode");
    case "provider":
      return skill.originLabel;
    case "local": {
      const scope =
        skill.scope === "system"
          ? t("skills.origin.system", "System")
          : configScopeLabel(skill.scope, t);
      const folder = SOURCE_FOLDERS[skill.sourceRoot];
      return folder ? `${scope} · ${folder}` : scope;
    }
  }
}

/** A Skill's status, in words. */
export function skillStatusLabel(status: SkillInfo["status"], t: TFunction): string {
  switch (status) {
    case "active":
      return t("skills.status.active", "Active");
    case "shadowed":
      return t("skills.status.shadowed", "Overridden");
    case "disabled":
      return t("skills.status.disabled", "Disabled");
    case "invalid":
      return t("skills.status.invalid", "Invalid");
  }
}
