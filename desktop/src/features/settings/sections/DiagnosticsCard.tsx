/** Diagnostics — extracted from the old SettingsPage unchanged. */

import { Download, RefreshCw } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { Project } from "../../../generated/app-server";
import type { DesktopRuntime } from "../../../rpc/contracts";
import { useDiagnostics } from "../useDiagnostics";
import styles from "../../management/ManagementWorkspace.module.css";

export function DiagnosticsCard({
  runtime,
  project,
}: {
  runtime: DesktopRuntime;
  project: Project | null;
}) {
  const { t } = useTranslation();
  const diagnostics = useDiagnostics(runtime, project?.id ?? null);
  const [exporting, setExporting] = useState(false);
  const [exportPath, setExportPath] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);

  const exportDiagnostics = async () => {
    if (!diagnostics.diagnostics) return;
    setExporting(true);
    setExportPath(null);
    setExportError(null);
    try {
      const path = await runtime.exportDiagnostics(diagnostics.diagnostics);
      if (path) setExportPath(path);
    } catch (cause) {
      setExportError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setExporting(false);
    }
  };

  return (
    <section className={styles.section}>
      <header className={styles.sectionHeader}>
        <div>
          <p className={styles.eyebrow}>{t("settings.diagnostics.eyebrow", "Troubleshooting")}</p>
          <h2>{t("settings.diagnostics.title", "Diagnostics")}</h2>
        </div>
        <div className={styles.headerActions}>
          <button
            className={styles.secondaryButton}
            type="button"
            disabled={!diagnostics.diagnostics || exporting}
            onClick={() => void exportDiagnostics()}
          >
            <Download size={14} />
            {exporting ? t("settings.diagnostics.exporting", "Exporting…") : t("settings.diagnostics.export", "Export report")}
          </button>
          <button
            className={styles.secondaryButton}
            type="button"
            disabled={diagnostics.loading}
            onClick={() => void diagnostics.refresh()}
          >
            <RefreshCw size={14} />
            {t("settings.diagnostics.runChecks", "Run checks")}
          </button>
        </div>
      </header>
      {diagnostics.error ? (
        <p className={styles.errorBanner}>{diagnostics.error}</p>
      ) : null}
      {exportError ? <p className={styles.errorBanner}>{exportError}</p> : null}
      {exportPath ? (
        <p className={styles.note}>{t("settings.diagnostics.savedTo", "Sanitized diagnostics saved to {{path}}", { path: exportPath })}</p>
      ) : null}
      {diagnostics.diagnostics ? (
        <>
          <div className={styles.checkList}>
            {diagnostics.diagnostics.checks.map((check) => (
              <article key={check.id} data-status={check.status}>
                <span />
                <div>
                  <strong>{check.label}</strong>
                  <p>{check.detail}</p>
                </div>
              </article>
            ))}
          </div>
          <dl className={styles.diagnosticGrid}>
            <Diagnostic label="App" value={diagnostics.diagnostics.appVersion} />
            <Diagnostic
              label="Python"
              value={`${diagnostics.diagnostics.pythonVersion} · ${diagnostics.diagnostics.architecture}`}
            />
            <Diagnostic
              label="Sessions"
              value={`${diagnostics.diagnostics.sessionCount} · ${diagnostics.diagnostics.sessionStorePath}`}
            />
            <Diagnostic
              label="Desktop DB"
              value={`schema ${diagnostics.diagnostics.databaseSchemaVersion} · ${diagnostics.diagnostics.databasePath}`}
            />
            <Diagnostic
              label="Platform"
              value={diagnostics.diagnostics.platform}
            />
            <Diagnostic
              label="Automations"
              value={String(diagnostics.diagnostics.automationCount)}
            />
            <Diagnostic
              label={t("settings.diagnostics.noProject", "Project config")}
              value={
                diagnostics.diagnostics.projectConfigPath ??
                t("settings.diagnostics.noProject", "No project selected")
              }
            />
          </dl>
        </>
      ) : null}
    </section>
  );
}

function Diagnostic({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
