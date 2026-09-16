import type {
  ManualModelEntry,
  ProviderCompat,
  ProviderProtocol,
  ProviderUpsertParams,
} from "../../generated/app-server";

export interface Draft {
  id: string;
  label: string;
  template: string;
  adapter: "openai_compat" | "anthropic";
  protocol: ProviderProtocol;
  auth: "api_key" | "none" | "oauth";
  compat: ProviderCompat;
  apiBase: string;
  /** Environment-variable reference — the advanced alternative to a
   * stored key (the write-only input is the primary path, dsh style). */
  apiKeyEnv: string;
  apiKey: string;
  clearApiKey: boolean;
  modelCatalog: "auto" | "openrouter" | "openai" | "anthropic" | "manual";
  manualModels: ManualModelEntry[];
  /** True when the launch environment currently provides this key: it
   * outranks a pasted key, so the form must say so instead of letting a
   * paste silently lose. */
  environmentShadows: boolean;
  shadowingEnvName: string;
}

export const emptyDraft: Draft = {
  id: "",
  label: "",
  template: "",
  adapter: "openai_compat",
  protocol: "auto",
  auth: "api_key",
  compat: {},
  apiBase: "",
  apiKeyEnv: "",
  apiKey: "",
  clearApiKey: false,
  modelCatalog: "auto",
  manualModels: [],
  environmentShadows: false,
  shadowingEnvName: "",
};

export function connectionMutation(
  draft: Draft,
): ProviderUpsertParams["connection"] {
  const connection: ProviderUpsertParams["connection"] = {
    id: draft.id.trim().toLowerCase(),
    label: draft.label.trim() || draft.id.trim(),
    template: draft.template,
    adapter: draft.adapter,
    protocol: draft.protocol,
    auth: draft.auth,
    compat: draft.compat,
    apiBase: draft.apiBase.trim() || null,
    apiKeyEnv: draft.apiKeyEnv.trim() || null,
    modelCatalog: draft.modelCatalog,
    manualModels: draft.manualModels
      .map((entry) => ({ ...entry, id: entry.id.trim() }))
      .filter((entry) => entry.id)
      .map((entry) => (hasDeclarations(entry) ? entry : entry.id)),
    enabled: true,
  };
  if (draft.apiKey.trim()) {
    connection.apiKey = draft.apiKey.trim();
  }
  if (draft.clearApiKey) connection.clearApiKey = true;

  return connection;
}

export function hasDeclarations(entry: ManualModelEntry): boolean {
  return (
    entry.label != null ||
    entry.contextWindow != null ||
    entry.maxOutputTokens != null ||
    entry.reasoningEfforts != null ||
    entry.inputModalities != null ||
    entry.toolCalling != null ||
    Object.keys(entry.compat ?? {}).length > 0
  );
}
