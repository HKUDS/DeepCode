/**
 * Interface language — machine-local (dsh's `locale` row). English strings
 * are the source of truth at every call site; picking a language applies
 * immediately through i18next with no reload.
 */

import { useTranslation } from "react-i18next";

import { LOCALES, setLocale, type Locale } from "../../../app/i18n";
import styles from "../../management/ManagementWorkspace.module.css";

export function LanguageCard() {
  const { t, i18n } = useTranslation();
  return (
    <section className={styles.formCard}>
      <header>
        <div>
          <p className={styles.eyebrow}>
            {t("settings.language.eyebrow", "Interface language")}
          </p>
          <h2>{t("settings.language.title", "Language")}</h2>
        </div>
      </header>
      <div className={styles.formGrid}>
        <label>
          {t("settings.language.label", "Interface language")}
          <select
            value={
              LOCALES.some((locale) => locale.value === i18n.language)
                ? i18n.language
                : "en"
            }
            onChange={(event) => setLocale(event.target.value as Locale)}
          >
            {LOCALES.map((locale) => (
              <option key={locale.value} value={locale.value}>
                {locale.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className={styles.note}>
        {t(
          "settings.language.note",
          "Applies immediately and is saved on this machine only. English " +
            "text is the source of truth for every string.",
        )}
      </p>
    </section>
  );
}
