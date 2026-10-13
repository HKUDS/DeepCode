/**
 * General — the dsh General page adapted: session defaults first
 * (permissions; the default agent preset joins in the presets section),
 * machine-local appearance next, application maintenance last.
 */

import { useTranslation } from "react-i18next";

import { AppearanceSettings } from "../AppearanceSettings";
import type { SettingsSectionProps } from "../settingsSections";
import { ComposerBehaviorCard } from "./ComposerBehaviorCard";
import { LanguageCard } from "./LanguageCard";
import { DefaultPresetCard } from "./DefaultPresetCard";
import { DiagnosticsCard } from "./DiagnosticsCard";
import { PermissionCard } from "./PermissionCard";
import { ServiceCard } from "./ServiceCard";
import { UpdatesCard } from "./UpdatesCard";
import styles from "../../management/ManagementWorkspace.module.css";

export function GeneralSection(props: SettingsSectionProps) {
  const { runtime, project, settings, busy, scope, onUpdate } = props;
  const { t } = useTranslation();
  return (
    <div className={styles.settingsGrid}>
      <DefaultPresetCard {...props} />
      <PermissionCard
        settings={settings}
        busy={busy}
        scope={scope}
        onUpdate={onUpdate}
      />
      <LanguageCard />
      <AppearanceSettings />
      <ComposerBehaviorCard />
      <ServiceCard runtime={runtime} />
      <UpdatesCard runtime={runtime} />
      <DiagnosticsCard runtime={runtime} project={project} />
      <aside
        className={styles.credits}
        aria-label={t("settings.credits.title", "Visual credits")}
      >
        <span className={styles.creditMark} aria-hidden="true" />
        <p>
          <strong>{t("settings.credits.title", "Visual credits")}</strong>
          <small>
            {t(
              "settings.credits.body",
              "Selected outline accents designed by The Icon Tree from Flaticon. Plugins and Skills empty-state marks from Phosphor Icons (MIT).",
            )}
          </small>
        </p>
      </aside>
    </div>
  );
}
