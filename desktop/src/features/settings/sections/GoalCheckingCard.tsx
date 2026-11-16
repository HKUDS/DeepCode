/**
 * How a Goal's completion claim is checked — `goal.completionVerifier`.
 * A Goal's confirmed checks run in every mode; this picks what else runs.
 */

import { useTranslation } from "react-i18next";

import type {
  ConfigScope,
  JsonObject,
  SettingsSnapshot,
} from "../../../generated/app-server";
import styles from "../../management/ManagementWorkspace.module.css";

type Verifier = "off" | "rule" | "reviewer";

const OPTIONS: { value: Verifier; label: string; note: string }[] = [
  {
    value: "off",
    label: "Off: the agent decides",
    note: "The agent's own audit decides when a Goal is complete.",
  },
  {
    value: "rule",
    label: "Rule check",
    note: "A claim is turned back while the latest check failed or nothing was done in that Turn.",
  },
  {
    value: "reviewer",
    label: "Independent review",
    note: "After the rule check, a second agent with a read-only view checks every requirement and re-runs the checks. A review takes a few minutes and its tokens count toward the Goal.",
  },
];

interface GoalCheckingCardProps {
  settings: SettingsSnapshot | null;
  busy: boolean;
  scope: ConfigScope;
  onUpdate(patch: JsonObject, scope: ConfigScope): Promise<boolean>;
}

export function GoalCheckingCard({
  settings,
  busy,
  scope,
  onUpdate,
}: GoalCheckingCardProps) {
  const { t } = useTranslation();
  const current = settings?.goal?.completionVerifier ?? "off";
  const selected = OPTIONS.find((option) => option.value === current) ?? OPTIONS[0];
  return (
    <section className={styles.formCard}>
      <header>
        <div>
          <h2>{t("settings.goalChecking.title", "Goal completion checks")}</h2>
        </div>
      </header>
      <div className={styles.formGrid}>
        <label>
          {t("settings.goalChecking.label", "Before a Goal is recorded complete")}
          <select
            value={current}
            disabled={busy || !settings}
            onChange={(event) =>
              void onUpdate(
                { goal: { completionVerifier: event.target.value as Verifier } },
                scope,
              )
            }
          >
            {OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {t(`settings.goalChecking.${option.value}`, option.label)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className={styles.note}>
        {t(`settings.goalChecking.${selected.value}Note`, selected.note)}{" "}
        {t(
          "settings.goalChecking.checksNote",
          "Checks you confirm for a Goal always run: DeepCode runs their commands itself before accepting completion.",
        )}
      </p>
    </section>
  );
}
