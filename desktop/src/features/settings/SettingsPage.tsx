import { Download, RefreshCw, Rocket } from "lucide-react";
import { useId, useState } from "react";

import type {
  ConfigScope,
  ExecutionAccessPreset,
  JsonObject,
  Project,
  SettingsSnapshot,
} from "../../generated/app-server";
import {
  ACCESS_PRESET_OPTIONS,
  settingsDefaultAccessLabel,
} from "../../app/accessPreset";
import { confirmAction } from "../../platform/confirmAction";
import type { DesktopRuntime } from "../../rpc/contracts";
import type {
  DesktopUpdateInfo,
  DesktopUpdateProgress,
} from "../../rpc/contracts";
import { useDiagnostics } from "./useDiagnostics";
import { ConnectionSettings } from "./ConnectionSettings";
import { useConnectionCatalog } from "./useConnectionCatalog";
import styles from "../management/ManagementWorkspace.module.css";

interface SettingsPageProps {
  runtime: DesktopRuntime;
  project: Project | null;
  settings: SettingsSnapshot | null;
  busy: boolean;
  onRefresh(): Promise<void>;
  onUpdate(
    patch: JsonObject,
    scope: ConfigScope,
    riskAcknowledged?: boolean,
  ): Promise<void>;
}

interface AgentDraft {
  defaultConnection: string;
  defaultModel: string;
  planningConnection: string;
  planningModel: string;
  implementationConnection: string;
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
    defaultConnection: text(
      defaults.connection,
      text(defaults.provider) === "auto" ? "" : text(defaults.provider),
    ),
    defaultModel: text(defaults.model),
    planningConnection: text(planning.connection),
    planningModel: text(planning.model),
    implementationConnection: text(implementation.connection),
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
  const [accessPresetDraft, setAccessPresetDraft] = useState<
    ExecutionAccessPreset | null | undefined
  >(undefined);
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
  const connections = useConnectionCatalog(runtime, project?.id ?? null);
  const canWriteProject = project?.trustState === "trusted";
  const effectiveScope =
    scope === "project" && canWriteProject ? "project" : "user";
  const agents = { ...agentDraft(settings), ...agentOverrides };
  const configuredAccessPreset =
    effectiveScope === "project"
      ? settings?.projectAccessPreset ?? null
      : settings?.userAccessPreset ?? null;
  const accessPreset =
    accessPresetDraft === undefined
      ? configuredAccessPreset
      : accessPresetDraft;
  const maxTokens = Number(agents.maxTokens);
  const maxTokensValid =
    /^\d+$/.test(agents.maxTokens.trim()) &&
    Number.isSafeInteger(maxTokens) &&
    maxTokens > 0;

  const models = settings?.models ?? [];
  const connectionOptions = connections.catalog?.connections ?? [];

  const saveAgents = async () => {
    if (!maxTokensValid) return;
    await onUpdate(
      {
        agents: {
          defaults: {
            connection: agents.defaultConnection || null,
            provider: "auto",
            model: agents.defaultModel,
            maxTokens,
          },
          planning: {
            connection: agents.planningConnection || null,
            model: agents.planningModel || null,
          },
          implementation: {
            connection: agents.implementationConnection || null,
            model: agents.implementationModel || null,
          },
        },
      },
      effectiveScope,
    );
    setAgentOverrides({});
  };

  const saveSecurity = async () => {
    if (
      accessPreset === "full_access" &&
      !(await confirmAction(
        "Full access becomes the default for Sessions without an override. " +
          "Tools may run without approval and outside the workspace sandbox.",
        {
          title: "Use Full access by default?",
          kind: "warning",
          confirmLabel: "Save Full access default",
          cancelLabel: "Cancel",
        },
      ))
    ) {
      return;
    }
    await onUpdate(
      {
        security: {
          accessPreset,
        },
      },
      effectiveScope,
      accessPreset === "full_access",
    );
    setAccessPresetDraft(undefined);
  };

  const refresh = async () => {
    await onRefresh();
    setAgentOverrides({});
    setAccessPresetDraft(undefined);
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
            onChange={(event) => {
              setScope(event.target.value as ConfigScope);
              setAccessPresetDraft(undefined);
            }}
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
              Default connection
              <select
                value={agents.defaultConnection}
                onChange={(event) =>
                  setAgentOverrides((current) => ({
                    ...current,
                    defaultConnection: event.target.value,
                  }))
                }
              >
                <option value="">Auto-select</option>
                {connectionOptions.map((connection) => (
                  <option value={connection.id} key={connection.id}>
                    {connection.label}
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
            <label>
              Planning connection
              <select
                value={agents.planningConnection}
                onChange={(event) =>
                  setAgentOverrides((current) => ({
                    ...current,
                    planningConnection: event.target.value,
                  }))
                }
              >
                <option value="">Use default</option>
                {connectionOptions.map((connection) => (
                  <option value={connection.id} key={connection.id}>
                    {connection.label}
                  </option>
                ))}
              </select>
            </label>
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
            <label>
              Implementation connection
              <select
                value={agents.implementationConnection}
                onChange={(event) =>
                  setAgentOverrides((current) => ({
                    ...current,
                    implementationConnection: event.target.value,
                  }))
                }
              >
                <option value="">Use default</option>
                {connectionOptions.map((connection) => (
                  <option value={connection.id} key={connection.id}>
                    {connection.label}
                  </option>
                ))}
              </select>
            </label>
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
              Default Session access
              <select
                value={accessPreset ?? ""}
                onChange={(event) => {
                  const value = event.target.value;
                  setAccessPresetDraft(
                    value ? (value as ExecutionAccessPreset) : null,
                  );
                }}
              >
                <option value="">
                  {effectiveScope === "project"
                    ? "Inherit user default"
                    : `Use resolved fallback · ${settingsDefaultAccessLabel(settings)}`}
                </option>
                {ACCESS_PRESET_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <p className={styles.note}>
            Effective default: {settingsDefaultAccessLabel(settings)} · source: {" "}
            {settings?.resolvedDefaultSecuritySource.replaceAll("_", " ") ??
              "loading"}
            . {" "}
            Ask and Read only retain the workspace sandbox and protected-path
            guards. Full access removes those execution boundaries after
            confirmation. Low-level compatibility settings are available only
            through advanced configuration.
          </p>
          <footer className={styles.formActions}>
            <span>
              Sessions with their own access selection keep that override. New and
              inherited Sessions use this default.
            </span>
            <button
              className={styles.primaryButton}
              type="button"
              disabled={busy || accessPresetDraft === undefined}
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

      <ConnectionSettings controller={connections} busy={busy} />

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
  const listId = useId();
  return (
    <label>
      {label}
      <input
        list={listId}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={allowEmpty ? "Use default" : "Provider model ID"}
      />
      <datalist id={listId}>
        {models.map((model) => (
          <option value={model} key={model} />
        ))}
      </datalist>
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
