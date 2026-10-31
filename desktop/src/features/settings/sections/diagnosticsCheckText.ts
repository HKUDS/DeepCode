import type { TFunction } from "i18next";

import type { DiagnosticsCheck } from "../../../generated/app-server";

/**
 * A diagnostics check's name and result, in words. The service's checks have
 * fixed ids (core/application/diagnostics_service.py); what is specific to
 * this machine, a configuration error or git's path, is kept as reported.
 */
export function diagnosticsCheckText(
  check: DiagnosticsCheck,
  t: TFunction,
): [label: string, detail: string] {
  const ok = check.status === "ok";
  switch (check.id) {
    case "config":
      return [
        t("diagnostics.config", "Configuration"),
        ok ? t("diagnostics.configOk", "The configuration is valid.") : check.detail,
      ];
    case "database":
      return [
        t("diagnostics.database", "Database"),
        ok
          ? t("diagnostics.databaseOk", "The database integrity check passed.")
          : t("diagnostics.databaseFailed", "The database integrity check failed."),
      ];
    case "sessions":
      return [
        t("diagnostics.sessions", "Session history"),
        ok
          ? t("diagnostics.sessionsOk", "The Session history folder is writable.")
          : t("diagnostics.sessionsReadOnly", "The Session history folder is not writable."),
      ];
    case "git":
      return [
        "Git",
        ok ? check.detail : t("diagnostics.gitMissing", "Git is not on the PATH."),
      ];
    default:
      return [check.label, check.detail];
  }
}
