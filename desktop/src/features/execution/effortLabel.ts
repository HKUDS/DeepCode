import type { TFunction } from "i18next";

/** A reasoning effort, in words: "auto", "none" and the provider levels. */
export function effortLabel(value: string, t: TFunction): string {
  switch (value) {
    case "auto":
      return t("composer.model.effortAuto", "Auto");
    case "none":
      return t("composer.model.effortOff", "Off");
    case "minimal":
      return t("effort.minimal", "Minimal");
    case "low":
      return t("effort.low", "Low");
    case "medium":
      return t("effort.medium", "Medium");
    case "high":
      return t("effort.high", "High");
    case "xhigh":
      return t("effort.xhigh", "Extra high");
    case "max":
      return t("effort.max", "Max");
    default:
      // A level DeepCode does not know yet is shown as the provider names it.
      return value.charAt(0).toLocaleUpperCase() + value.slice(1);
  }
}
