/**
 * Plugins — the standalone Plugins page embedded as a settings section.
 * The page component stays the single owner of plugin management; the
 * sidebar destination remains available as the second entry point.
 */

import { PluginsPage } from "../../plugins/PluginsPage";
import type { SettingsSectionProps } from "../settingsSections";

export function PluginsSection({ runtime }: SettingsSectionProps) {
  return <PluginsPage runtime={runtime} />;
}
