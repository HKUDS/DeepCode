import { Download, RefreshCw, Rocket } from "lucide-react";
import { useMemo, useState } from "react";

import type {
  ConfigScope,
  JsonObject,
  Project,
  SettingsSnapshot,
} from "../../generated/app-server";
import type { DesktopRuntime } from "../../rpc/contracts";
import type {
  DesktopUpdateInfo,
  DesktopUpdateProgress,
} from "../../rpc/contracts";
import { useDiagnostics } from "./useDiagnostics";
import styles from "../management/ManagementWorkspace.module.css";

interface SettingsPageProps {
  runtime: DesktopRuntime;
  project: Project | null;
  settings: SettingsSnapshot | null;
  busy: boolean;
  onRefresh(): Promise<void>;
  onUpdate(patch: JsonObject, scope: ConfigScope): Promise<void>;
}

interface AgentDraft {
  provider: string;
  defaultModel: string;
  planningModel: string;
  implementationModel: string;
  maxTokens: string;
}

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function numberText(value: unknown, fallback: number): string {
  return typeof value === "number" && Number.isFinite(value)
    ? String(value)
    : String(fallback);
}

function agentDraft(settings: SettingsSnapshot | null): AgentDraft {
  const agents = record(settings?.agents);
  const defaults = record(agents.defaults);
  const planning = record(agents.planning);
  const implementation = record(agents.implementation);
  return {
    provider: text(defaults.provider, "auto"),
    defaultModel: text(defaults.model),
    planningModel: text(planning.model),
    implementationModel: text(implementation.model),
    maxTokens: numberText(defaults.maxTokens, 8192),
  };
}

export function SettingsPage({
  runtime,
  project,
  settings,
  busy,
  onRefresh,
  onUpdate,
}: SettingsPageProps) {
  const [scope, setScope] = useState<ConfigScope>("user");
  const [agentOverrides, setAgentOverrides] = useState<Partial<AgentDraft>>({});
  const [apiKeys, setApiKeys] = useState<Record<string, string>>({});
  const [providerBases, setProviderBases] = useState<Record<string, string>>({});
  const security = record(settings?.security);
  const [permissionModeOverride, setPermissionModeOverride] = useState<
    string | null
  >(null);
  const [sandboxOverride, setSandboxOverride] = useState<boolean | null>(null);
  const [exportingDiagnostics, setExportingDiagnostics] = useState(false);
  const [diagnosticsExportPath, setDiagnosticsExportPath] = useState<
    string | null
  >(null);
  const [diagnosticsExportError, setDiagnosticsExportError] = useState<
    string | null
  >(null);
  const [updateInfo, setUpdateInfo] = useState<DesktopUpdateInfo | null>(null);
  const [updateState, setUpdateState] = useState<
    "idle" | "checking" | "current" | "available" | "installing"
  >("idle");
  const [updateProgress, setUpdateProgress] =
    useState<DesktopUpdateProgress | null>(null);
  const [updateError, setUpdateError] = useState<string | null>(null);
  const diagnostics = useDiagnostics(runtime, project?.id ?? null);
  const canWriteProject = project?.trustState === "trusted";
  const effectiveScope =
    scope === "project" && canWriteProject ? "project" : "user";
  const agents = { ...agentDraft(settings), ...agentOverrides };
  const permissionMode =
    permissionModeOverride ?? text(security.permissionMode, "full_auto");
  const sandbox =
    sandboxOverride ??
    (typeof security.sandbox === "boolean" ? security.sandbox : true);
  const maxTokens = Number(agents.maxTokens);
  const maxTokensValid =
    /^\d+$/.test(agents.maxTokens.trim()) &&
    Number.isSafeInteger(maxTokens) &&
    maxTokens > 0;

  const models = settings?.models ?? [];
  const providerNames = useMemo(
    () => settings?.providers.map((provider) => provider.name) ?? [],
    [settings?.providers],
  );
  const providers = useMemo(() => {
    const activeProvider = agents.provider;
    return [...(settings?.providers ?? [])].sort((left, right) => {
      const leftRank = providerRank(left, activeProvider);
      const rightRank = providerRank(right, activeProvider);
      return leftRank - rightRank || left.label.localeCompare(right.label);
    });
  }, [agents.provider, settings?.providers]);

  const saveAgents = async () => {
    if (!maxTokensValid) return;
    await onUpdate(
      {
        agents: {
          defaults: {
            provider: agents.provider,
            model: agents.defaultModel,
            maxTokens,
          },
          planning: { model: agents.planningModel || null },
          implementation: { model: agents.implementationModel || null },
        },
      },
      effectiveScope,
    );
    setAgentOverrides({});
  };

  const saveSecurity = async () => {
    await onUpdate(
      {
        security: {
          permissionMode,
          sandbox,
        },
      },
      effectiveScope,
    );
    setPermissionModeOverride(null);
    setSandboxOverride(null);
  };

  const saveProvider = async (name: string) => {
    const apiKey = apiKeys[name]?.trim();
    const apiBase = providerBases[name]?.trim();
    const patch: JsonObject = {};
    if (apiKey) patch.apiKey = apiKey;
    if (apiBase) patch.apiBase = apiBase;
    if (!Object.keys(patch).length) return;
    await onUpdate({ providers: { [name]: patch } }, effectiveScope);
    setApiKeys((current) => ({ ...current, [name]: "" }));
    setProviderBases((current) => ({ ...current, [name]: "" }));
  };

  const refresh = async () => {
    await onRefresh();
    setAgentOverrides({});
    setPermissionModeOverride(null);
    setSandboxOverride(null);
  };

  const exportDiagnostics = async () => {
    if (!diagnostics.diagnostics) return;
    setExportingDiagnostics(true);
    setDiagnosticsExportPath(null);
    setDiagnosticsExportError(null);
    try {
      const path = await runtime.exportDiagnostics(diagnostics.diagnostics);
      if (path) setDiagnosticsExportPath(path);
    } catch (cause) {
      setDiagnosticsExportError(
        cause instanceof Error ? cause.message : String(cause),
      );
    } finally {
      setExportingDiagnostics(false);
    }
  };

  const checkForUpdate = async () => {
    setUpdateState("checking");
    setUpdateInfo(null);
    setUpdateProgress(null);
    setUpdateError(null);
    try {
      const update = await runtime.checkForUpdate();
      setUpdateInfo(update);
      setUpdateState(update ? "available" : "current");
    } catch (cause) {
      setUpdateState("idle");
      setUpdateError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const installUpdate = async () => {
    setUpdateState("installing");
    setUpdateProgress(null);
    setUpdateError(null);
    try {
      await runtime.installUpdate(setUpdateProgress);
    } catch (cause) {
      setUpdateState("available");
      setUpdateError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  return (
    <section className={styles.page} aria-labelledby="settings-title">
      <header className={styles.pageHeader}>
        <div>
          <p className={styles.eyebrow}>Runtime configuration</p>
          <h1 id="settings-title">Settings</h1>
          <p>
            User settings apply everywhere. Trusted project settings override
            them only inside the selected project.
          </p>
        </div>
        <button
          className={styles.secondaryButton}
          type="button"
          disabled={busy}
          onClick={() => void refresh()}
        >
          <RefreshCw size={14} />
          Reload
        </button>
      </header>

      <div className={styles.scopeBar}>
        <label>
          Write changes to
          <select
            value={effectiveScope}
            onChange={(event) => setScope(event.target.value as ConfigScope)}
          >
            <option value="user">User config</option>
            <option value="project" disabled={!project || !canWriteProject}>
              Selected project
            </option>
          </select>
        </label>
        <span>
          {effectiveScope === "project"
            ? project?.canonicalPath
            : settings?.configPath ?? "DeepCode user config"}
        </span>
      </div>

      <div className={styles.settingsGrid}>
        <section className={styles.formCard}>
          <header>
            <div>
              <p className={styles.eyebrow}>Agent defaults</p>
              <h2>Models</h2>
            </div>
          </header>
          <div className={styles.formGrid}>
            <label>
              Provider
              <select
                value={agents.provider}
                onChange={(event) =>
                  setAgentOverrides((current) => ({
                    ...current,
                    provider: event.target.value,
                  }))
                }
              >
                <option value="auto">Auto</option>
                {providerNames.map((name) => (
                  <option value={name} key={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Max output tokens
              <input
                inputMode="numeric"
                value={agents.maxTokens}
                onChange={(event) =>
                  setAgentOverrides((current) => ({
                    ...current,
                    maxTokens: event.target.value,
                  }))
                }
                aria-invalid={!maxTokensValid}
              />
            </label>
            <ModelField
              label="Default model"
              value={agents.defaultModel}
              models={models.map((model) => model.id)}
              onChange={(defaultModel) =>
                setAgentOverrides((current) => ({
                  ...current,
                  defaultModel,
                }))
              }
            />
            <ModelField
              label="Planning override"
              value={agents.planningModel}
              models={models.map((model) => model.id)}
              allowEmpty
              onChange={(planningModel) =>
                setAgentOverrides((current) => ({
                  ...current,
                  planningModel,
                }))
              }
            />
            <ModelField
              label="Implementation override"
              value={agents.implementationModel}
              models={models.map((model) => model.id)}
              allowEmpty
              onChange={(implementationModel) =>
                setAgentOverrides((current) => ({
                  ...current,
                  implementationModel,
                }))
              }
            />
          </div>
          <footer className={styles.formActions}>
            <span>Idle Sessions reload this configuration on their next Turn.</span>
            <button
              className={styles.primaryButton}
              type="button"
              disabled={busy || !agents.defaultModel || !maxTokensValid}
              onClick={() => void saveAgents()}
            >
              Save model settings
            </button>
          </footer>
        </section>

        <section className={styles.formCard}>
          <header>
            <div>
              <p className={styles.eyebrow}>Safety policy</p>
              <h2>Permissions</h2>
            </div>
          </header>
          <div className={styles.formGrid}>
            <label>
              Permission mode
              <select
                value={permissionMode}
                onChange={(event) =>
                  setPermissionModeOverride(event.target.value)
                }
              >
                <option value="default">Ask before sensitive tools</option>
                <option value="plan">Plan / read-only</option>
                <option value="full_auto">Full auto</option>
              </select>
            </label>
            <label className={styles.checkboxField}>
              <input
                type="checkbox"
                checked={sandbox}
                onChange={(event) => setSandboxOverride(event.target.checked)}
              />
              Enable command sandbox
            </label>
          </div>
          <p className={styles.note}>
            Sensitive-path denials remain non-overridable. Project changes require
            a trusted folder.
          </p>
          <footer className={styles.formActions}>
            <span>
              {settings?.permissionModeExplicit
                ? "An explicit permission mode is configured."
                : "Desktop currently applies its approval-first client default."}
            </span>
            <button
              className={styles.primaryButton}
              type="button"
              disabled={busy}
              onClick={() => void saveSecurity()}
            >
              Save safety settings
            </button>
          </footer>
        </section>
      </div>

      <section className={styles.section}>
        <header className={styles.sectionHeader}>
          <div>
            <p className={styles.eyebrow}>Signed release channel</p>
            <h2>Application updates</h2>
          </div>
          <div className={styles.headerActions}>
            {updateInfo ? (
              <button
                className={styles.primaryButton}
                type="button"
                disabled={updateState === "installing"}
                onClick={() => void installUpdate()}
              >
                <Download size={14} />
                {updateState === "installing"
                  ? updateProgressLabel(updateProgress)
                  : `Install ${updateInfo.version}`}
              </button>
            ) : null}
            <button
              className={styles.secondaryButton}
              type="button"
              disabled={
                updateState === "checking" || updateState === "installing"
              }
              onClick={() => void checkForUpdate()}
            >
              <Rocket size={14} />
              {updateState === "checking" ? "Checking…" : "Check for updates"}
            </button>
          </div>
        </header>
        {updateError ? (
          <p className={styles.errorBanner}>{updateError}</p>
        ) : null}
        <p className={styles.note}>
          {updateStatusMessage(updateState, updateInfo, updateProgress)}
        </p>
        {updateInfo?.body ? <p>{updateInfo.body}</p> : null}
      </section>

      <section className={styles.section}>
        <header className={styles.sectionHeader}>
          <div>
            <p className={styles.eyebrow}>Credentials</p>
            <h2>Providers</h2>
          </div>
        </header>
        <div className={styles.cardList}>
          {providers.map((provider) => (
            <article className={styles.card} key={provider.name}>
              <header>
                <div>
                  <p className={styles.eyebrow}>{provider.name}</p>
                  <h2>{provider.label}</h2>
                </div>
                <span
                  className={styles.badge}
                  data-status={providerBadgeStatus(provider.credentialSource)}
                >
                  {providerCredentialLabel(provider.credentialSource)}
                </span>
              </header>
              <p>{provider.apiBase ?? "Provider default endpoint"}</p>
              <div className={styles.inlineForm}>
                {!provider.local ? (
                  <input
                    type="password"
                    value={apiKeys[provider.name] ?? ""}
                    onChange={(event) =>
                      setApiKeys((current) => ({
                        ...current,
                        [provider.name]: event.target.value,
                      }))
                    }
                    placeholder="New API key (never read back)"
                    autoComplete="off"
                  />
                ) : null}
                <input
                  value={providerBases[provider.name] ?? ""}
                  onChange={(event) =>
                    setProviderBases((current) => ({
                      ...current,
                      [provider.name]: event.target.value,
                    }))
                  }
                  placeholder="Optional API base"
                />
                <button
                  type="button"
                  disabled={
                    busy ||
                    (!apiKeys[provider.name]?.trim() &&
                      !providerBases[provider.name]?.trim())
                  }
                  onClick={() => void saveProvider(provider.name)}
                >
                  Save
                </button>
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className={styles.section}>
        <header className={styles.sectionHeader}>
          <div>
            <p className={styles.eyebrow}>Troubleshooting</p>
            <h2>Diagnostics</h2>
          </div>
          <div className={styles.headerActions}>
            <button
              className={styles.secondaryButton}
              type="button"
              disabled={!diagnostics.diagnostics || exportingDiagnostics}
              onClick={() => void exportDiagnostics()}
            >
              <Download size={14} />
              {exportingDiagnostics ? "Exporting…" : "Export report"}
            </button>
            <button
              className={styles.secondaryButton}
              type="button"
              disabled={diagnostics.loading}
              onClick={() => void diagnostics.refresh()}
            >
              <RefreshCw size={14} />
              Run checks
            </button>
          </div>
        </header>
        {diagnostics.error ? (
          <p className={styles.errorBanner}>{diagnostics.error}</p>
        ) : null}
        {diagnosticsExportError ? (
          <p className={styles.errorBanner}>{diagnosticsExportError}</p>
        ) : null}
        {diagnosticsExportPath ? (
          <p className={styles.note}>
            Sanitized diagnostics saved to {diagnosticsExportPath}
          </p>
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
                label="Project config"
                value={diagnostics.diagnostics.projectConfigPath ?? "No project selected"}
              />
            </dl>
          </>
        ) : null}
      </section>

      <aside className={styles.credits} aria-label="Visual credits">
        <span className={styles.creditMark} aria-hidden="true" />
        <p>
          <strong>Visual credits</strong>
          <small>
            Selected outline accents designed by The Icon Tree from Flaticon.
          </small>
        </p>
      </aside>
    </section>
  );
}

function updateProgressLabel(progress: DesktopUpdateProgress | null): string {
  if (!progress || progress.phase === "started") return "Preparing…";
  if (progress.phase === "finished") return "Installing…";
  if (!progress.totalBytes) return "Downloading…";
  const percentage = Math.min(
    100,
    Math.round((progress.downloadedBytes / progress.totalBytes) * 100),
  );
  return `Downloading ${percentage}%`;
}

function providerRank(
  provider: SettingsSnapshot["providers"][number],
  activeProvider: string,
): number {
  if (provider.name === activeProvider) return 0;
  if (
    provider.credentialSource === "config" ||
    provider.credentialSource === "environment"
  ) {
    return 1;
  }
  if (provider.credentialSource === "not_required") return 2;
  return 3;
}

function providerBadgeStatus(
  source: SettingsSnapshot["providers"][number]["credentialSource"],
): "configured" | "neutral" | "invalid" {
  if (source === "config" || source === "environment") return "configured";
  if (source === "not_required") return "neutral";
  return "invalid";
}

function providerCredentialLabel(
  source: SettingsSnapshot["providers"][number]["credentialSource"],
): string {
  if (source === "config") return "Configured";
  if (source === "environment") return "Environment";
  if (source === "not_required") return "No key needed";
  return "Missing key";
}

function updateStatusMessage(
  state: "idle" | "checking" | "current" | "available" | "installing",
  update: DesktopUpdateInfo | null,
  progress: DesktopUpdateProgress | null,
): string {
  if (state === "checking") return "Checking the configured signed release channel.";
  if (state === "current") return "This installation is up to date.";
  if (state === "available" && update) {
    return `DeepCode ${update.version} is available. The package signature is verified before installation.`;
  }
  if (state === "installing") return updateProgressLabel(progress);
  return "Updates are checked only when requested. Development builds may not configure a release channel.";
}

function ModelField({
  label,
  value,
  models,
  allowEmpty = false,
  onChange,
}: {
  label: string;
  value: string;
  models: string[];
  allowEmpty?: boolean;
  onChange(value: string): void;
}) {
  const options = value && !models.includes(value) ? [value, ...models] : models;
  return (
    <label>
      {label}
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {allowEmpty ? <option value="">Use default</option> : null}
        {options.map((model) => (
          <option value={model} key={model}>
            {model}
          </option>
        ))}
      </select>
    </label>
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
