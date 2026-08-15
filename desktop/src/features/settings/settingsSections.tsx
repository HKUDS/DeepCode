/**
 * Settings section registry — the dialog shell renders whatever is listed
 * here and owns no section knowledge of its own (the dsh composition rule,
 * sized for a single desktop app: an array, not a plugin slot system).
 * Adding a section is one entry + one component; the shell never changes.
 */

import type { ComponentType } from "react";
import type { LucideIcon } from "lucide-react";
import { Bot, Database, Settings as SettingsIcon, SlidersHorizontal } from "lucide-react";

import type {
  ConfigScope,
  JsonObject,
  Project,
  SettingsSnapshot,
} from "../../generated/app-server";
import type { DesktopRuntime } from "../../rpc/contracts";
import { GeneralSection } from "./sections/GeneralSection";
import { ModelsSection } from "./sections/ModelsSection";
import { PluginsSection } from "./sections/PluginsSection";
import { PresetsSection } from "./sections/PresetsSection";

export type SettingsSectionId = "general" | "models" | "plugins" | "agent-presets";

/** Everything a section may need; each uses the subset it cares about. */
export interface SettingsSectionProps {
  runtime: DesktopRuntime;
  project: Project | null;
  settings: SettingsSnapshot | null;
  busy: boolean;
  /** The dialog-level "write changes to" choice, already trust-checked. */
  scope: ConfigScope;
  onRefresh(): Promise<void>;
  onUpdate(
    patch: JsonObject,
    scope: ConfigScope,
    riskAcknowledged?: boolean,
  ): Promise<void>;
}

export interface SettingsSection {
  id: SettingsSectionId;
  order: number;
  /** i18n key; `label` is the English source of truth (the defaultValue). */
  labelKey: string;
  label: string;
  icon: LucideIcon;
  component: ComponentType<SettingsSectionProps>;
}

const SECTIONS: readonly SettingsSection[] = [
  {
    id: "general",
    order: 0,
    labelKey: "settings.section.general",
    label: "General",
    icon: SettingsIcon,
    component: GeneralSection,
  },
  {
    id: "models",
    order: 10,
    labelKey: "settings.section.models",
    label: "Models",
    icon: Database,
    component: ModelsSection,
  },
  {
    id: "plugins",
    order: 15,
    labelKey: "settings.section.plugins",
    label: "Plugins",
    icon: SlidersHorizontal,
    component: PluginsSection,
  },
  {
    id: "agent-presets",
    order: 20,
    labelKey: "settings.section.presets",
    label: "Agent presets",
    icon: Bot,
    component: PresetsSection,
  },
];

export const SETTINGS_SECTIONS: readonly SettingsSection[] = [...SECTIONS].sort(
  (left, right) => left.order - right.order,
);
