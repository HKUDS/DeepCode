import type { TFunction } from "i18next";

import type { ConfigScope } from "../generated/app-server";

/** A configuration scope, in words: the same two names on every page. */
export function configScopeLabel(scope: ConfigScope, t: TFunction): string {
  return scope === "project"
    ? t("scope.project", "This project")
    : t("scope.user", "User settings");
}
