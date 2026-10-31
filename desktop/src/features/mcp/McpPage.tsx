import {
  Cable,
  CirclePower,
  ExternalLink,
  LogIn,
  LogOut,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";
import {
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";

import type {
  ConfigScope,
  JsonObject,
  McpServerInfo,
  Project,
} from "../../generated/app-server";
import { configScopeLabel } from "../../app/configScope";
import { confirmAction } from "../../platform/confirmAction";
import type { ClientRuntime } from "../../rpc/contracts";
import styles from "../management/ManagementWorkspace.module.css";
import {
  mcpApprovalLabel,
  mcpAuthLabel,
  mcpCategoryLabel,
  mcpConfigurationLabel,
  mcpConfigurationText,
  mcpRuntimeText,
  mcpSourceLabel,
  mcpStatus,
  mcpStatusLabel,
  mcpTransportLabel,
} from "./mcpPresentation";
import { useMcpCatalog } from "./useMcpCatalog";

type Transport = "stdio" | "sse" | "streamableHttp";

interface Draft {
  name: string;
  scope: ConfigScope;
  transport: Transport;
  auth: "none" | "oauth";
  command: string;
  args: string;
  cwd: string;
  url: string;
  envVars: string;
  requiredEnvVars: string;
  credentialEnv: string;
  headerEnv: string;
  urlParamEnv: string;
  enabledTools: string;
  disabledTools: string;
  approvalMode: "auto" | "prompt" | "writes" | "approve";
  startupTimeout: string;
  toolTimeout: string;
  required: boolean;
}

interface Feedback {
  tone: "success" | "error";
  message: string;
}

const EMPTY_DRAFT: Draft = {
  name: "",
  scope: "user",
  transport: "stdio",
  auth: "none",
  command: "",
  args: "",
  cwd: "",
  url: "",
  envVars: "",
  requiredEnvVars: "",
  credentialEnv: "",
  headerEnv: "",
  urlParamEnv: "",
  enabledTools: "",
  disabledTools: "",
  approvalMode: "writes",
  startupTimeout: "10",
  toolTimeout: "60",
  required: false,
};

export function McpPage({
  runtime,
  project,
}: {
  runtime: ClientRuntime;
  project: Project | null;
}) {
  const { t } = useTranslation();
  const catalog = useMcpCatalog(runtime, project);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [busy, setBusy] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [authorizationUrl, setAuthorizationUrl] = useState<string | null>(null);
  const servers = catalog.inventory?.servers ?? [];
  const presets = catalog.presets?.presets ?? [];
  const canUseProjectScope = project?.trustState === "trusted";
  const editing = servers.find(
    (server) => server.name === draft.name && server.source === draft.scope,
  );

  const perform = async (key: string, operation: () => Promise<void>) => {
    setBusy(key);
    setFeedback(null);
    setAuthorizationUrl(null);
    try {
      await operation();
    } catch (cause) {
      setFeedback({
        tone: "error",
        message: cause instanceof Error ? cause.message : String(cause),
      });
    } finally {
      setBusy(null);
    }
  };

  const save = async () => {
    const name = draft.name.trim();
    if (!name) return;
    // Maps are sent whole, and the service replaces them: a line removed
    // here is removed from the saved server.
    const server: JsonObject = {
      type: draft.transport,
      enabled: editing?.enabled ?? false,
      required: draft.required,
      startupTimeoutSeconds: numberValue(draft.startupTimeout, 10),
      toolTimeoutSeconds: numberValue(draft.toolTimeout, 60),
      approvalMode: draft.approvalMode,
      disabledTools: csv(draft.disabledTools),
      // The form shows these when editing, so a blank one clears the saved
      // value: the service merges the patch, and a missing key keeps it.
      enabledTools: draft.enabledTools.trim() ? csv(draft.enabledTools) : null,
      ...(draft.transport === "stdio"
        ? {
            command: draft.command.trim(),
            args: lines(draft.args),
            cwd: draft.cwd.trim() || null,
            envVars: csv(draft.envVars),
            requiredEnvVars: csv(draft.requiredEnvVars),
            credentialEnv: credentialMap(draft.credentialEnv),
          }
        : {
            url: draft.url.trim(),
            auth: draft.auth === "oauth" ? "oauth" : null,
            envHttpHeaders: stringMap(draft.headerEnv),
            envUrlParams: stringMap(draft.urlParamEnv),
          }),
    };
    await perform(`save:${name}`, async () => {
      await catalog.upsert(name, draft.scope, server);
      setDraft(EMPTY_DRAFT);
      setFeedback({
        tone: "success",
        message: t(
          "mcp.feedback.saved",
          "Saved {{name}}. No server process was started. Test it before enabling.",
          { name },
        ),
      });
    });
  };

  const edit = (server: McpServerInfo) => {
    setDraft({
      ...EMPTY_DRAFT,
      name: server.name,
      scope: server.source === "project" ? "project" : "user",
      transport: server.transport,
      auth: server.auth === "oauth" ? "oauth" : "none",
      command: server.command ?? "",
      args: server.args.join("\n"),
      cwd: server.cwd ?? "",
      url: server.url ?? "",
      envVars: server.forwardedEnvKeys.join(", "),
      requiredEnvVars: server.requiredEnvKeys.join(", "),
      credentialEnv: Object.entries(server.credentialEnv)
        .map(([name, reference]) => `${name}=${reference.replace(/^provider:/, "")}`)
        .join("\n"),
      headerEnv: mapLines(server.envHttpHeaders),
      urlParamEnv: mapLines(server.envUrlParams),
      enabledTools: server.enabledTools?.join(", ") ?? "",
      disabledTools: server.disabledTools.join(", "),
      // "approve" adds no checks, exactly like "auto" (core/harness/permissions.py);
      // the editor offers the one name for both.
      approvalMode: server.approvalMode === "approve" ? "auto" : server.approvalMode,
      startupTimeout: String(server.startupTimeoutSeconds),
      toolTimeout: String(server.toolTimeoutSeconds),
      required: server.required,
    });
  };

  return (
    <section className={styles.page} aria-labelledby="mcp-title">
      <header className={styles.pageHeader}>
        <div>
          <p className={styles.eyebrow}>
            {t("mcp.eyebrow", "Agent capabilities")}
          </p>
          <h1 id="mcp-title">{t("mcp.title", "MCP Servers")}</h1>
          <p>
            {t(
              "mcp.description",
              "Add an MCP server, test the connection, then enable it. Enabled tools become available to the coding agent automatically when a task needs them.",
            )}
          </p>
        </div>
        <button
          className={styles.secondaryButton}
          type="button"
          disabled={catalog.loading}
          onClick={() => void catalog.refresh()}
        >
          <RefreshCw size={14} /> {t("mcp.refresh", "Refresh")}
        </button>
      </header>

      {project ? (
        <div className={styles.contextBar}>
          <strong>{project.displayName}</strong>
          <span>{project.canonicalPath}</span>
        </div>
      ) : null}
      {catalog.error ? <p className={styles.errorBanner}>{catalog.error}</p> : null}
      {feedback ? (
        <p
          aria-live="polite"
          className={feedback.tone === "error" ? styles.errorBanner : styles.successBanner}
        >
          {feedback.message}
        </p>
      ) : null}
      {authorizationUrl ? (
        <p>
          {t("mcp.browserDidNotOpen", "Browser did not open?")}{" "}
          <a href={authorizationUrl} target="_blank" rel="noreferrer">
            {t("mcp.continueAuthorization", "Continue authorization")}{" "}
            <ExternalLink size={12} />
          </a>
        </p>
      ) : null}

      <article className={styles.formCard}>
        <header>
          <div>
            <p className={styles.eyebrow}>
              {t("mcp.configured.eyebrow", "Configured servers")}
            </p>
            <h2>{t("mcp.configured.title", "Available to DeepCode")}</h2>
          </div>
          <span>
            {t("mcp.configured.total", "{{total}} configured", {
              total: servers.length,
            })}
          </span>
        </header>
        <div className={styles.cardList}>
          {servers.length ? servers.map((server) => (
            <article className={styles.card} key={`${server.source}:${server.id}`}>
            <header>
              <div>
                <p className={styles.eyebrow}>{mcpSourceLabel(server.source, t)} · {mcpTransportLabel(server.transport, t)}</p>
                <h2>{server.name}</h2>
              </div>
              <span className={styles.badge} data-status={mcpStatus(server)}>
                {mcpStatusLabel(server, t)}
              </span>
            </header>
            <p>{mcpConfigurationText(server, t)}</p>
            <dl className={styles.metadata}>
              <div><dt>{t("mcp.server.endpoint", "Endpoint")}</dt><dd>{server.command ?? server.url}</dd></div>
              <div><dt>{t("mcp.server.configuration", "Configuration")}</dt><dd>{mcpConfigurationLabel(server.configurationState, t)}</dd></div>
              <div><dt>{t("mcp.server.authentication", "Authentication")}</dt><dd>{mcpAuthLabel(server.authState, t)}</dd></div>
              <div><dt>{t("mcp.server.lastCheck", "Last check")}</dt><dd>{mcpRuntimeText(server, t)}</dd></div>
              <div><dt>{t("mcp.server.capabilities", "Capabilities")}</dt><dd>{capabilitySummary(t, server.toolCount, server.resourceCount, server.promptCount)}</dd></div>
              <div><dt>{t("mcp.server.approval", "Approval")}</dt><dd>{mcpApprovalLabel(server.approvalMode, t)}</dd></div>
              {server.missingEnvKeys.length ? (
                <div><dt>{t("mcp.server.missingEnvironment", "Missing environment")}</dt><dd>{server.missingEnvKeys.join(", ")}</dd></div>
              ) : null}
              {server.policyKey ? <div><dt>{t("mcp.server.pluginPolicy", "Plugin policy")}</dt><dd>{server.policyKey}</dd></div> : null}
            </dl>
            <div className={styles.cardActions}>
              <button
                type="button"
                disabled={busy !== null || server.configurationState === "invalid" || server.configurationState === "blocked"}
                onClick={() => void perform(`test:${server.id}`, async () => {
                  const result = await catalog.probe(server.id);
                  setFeedback({
                    tone: result.ok ? "success" : "error",
                    message: result.ok
                      ? t(
                          "mcp.feedback.testPassed",
                          "{{name}} passed its connection test: {{summary}}. The test connection is now closed.",
                          {
                            name: server.name,
                            summary: capabilitySummary(t, result.toolCount, result.resourceCount, result.promptCount),
                          },
                        )
                      : t(
                          "mcp.feedback.testFailed",
                          "{{name}} failed its connection test: {{error}}",
                          { name: server.name, error: result.error },
                        ),
                  });
                })}
              >
                <Play size={14} /> {t("mcp.testConnection", "Test connection")}
              </button>
              {server.auth === "oauth" ? (
                server.authState === "authenticated" ? (
                  <button type="button" disabled={busy !== null} onClick={() => void perform(`logout:${server.id}`, async () => {
                    await catalog.logout(server.id);
                    setFeedback({
                      tone: "success",
                      message: t("mcp.feedback.signedOut", "Signed out of {{name}}.", { name: server.name }),
                    });
                  })}>
                    <LogOut size={14} /> {t("mcp.logout", "Logout")}
                  </button>
                ) : (
                  <button type="button" disabled={busy !== null || server.authState === "authorizing"} onClick={() => void perform(`login:${server.id}`, async () => {
                    const flow = await catalog.startOAuth(server.id);
                    setAuthorizationUrl(flow.authorizationUrl);
                    setFeedback({
                      tone: "success",
                      message: t(
                        "mcp.feedback.authorizationStarted",
                        "Authorization started for {{name}}. Return here after approving access.",
                        { name: server.name },
                      ),
                    });
                  })}>
                    <LogIn size={14} /> {t("mcp.authenticate", "Authenticate")}
                  </button>
                )
              ) : null}
              {server.source !== "plugin" ? (
                <>
                  <button type="button" disabled={busy !== null} onClick={() => void perform(`toggle:${server.id}`, async () => {
                    await catalog.setEnabled(server.id, !server.enabled);
                    setFeedback({
                      tone: "success",
                      message: server.enabled
                        ? t(
                            "mcp.feedback.disabled",
                            "Disabled {{name}}. It will not be offered to the agent on new turns.",
                            { name: server.name },
                          )
                        : t(
                            "mcp.feedback.enabled",
                            "Enabled {{name}}. The agent can now call its tools when a task needs them.",
                            { name: server.name },
                          ),
                    });
                  })}>
                    <CirclePower size={14} />{" "}
                    {server.enabled ? t("mcp.disable", "Disable") : t("mcp.enable", "Enable")}
                  </button>
                  <button type="button" onClick={() => edit(server)}><Pencil size={14} /> {t("mcp.edit", "Edit")}</button>
                  <button type="button" disabled={busy !== null || server.configurationState === "blocked"} onClick={() => {
                    void (async () => {
                      if (!(await confirmAction(
                        t("mcp.removeConfirm", "Remove MCP server “{{name}}”?", { name: server.name }),
                        { destructive: true, confirmLabel: t("mcp.remove", "Remove") },
                      ))) return;
                      await perform(`remove:${server.id}`, async () => {
                        await catalog.remove(server.name, server.source === "project" ? "project" : "user");
                        setFeedback({
                          tone: "success",
                          message: t("mcp.feedback.removed", "Removed {{name}}.", { name: server.name }),
                        });
                      });
                    })();
                  }}><Trash2 size={14} /> {t("mcp.remove", "Remove")}</button>
                </>
              ) : null}
            </div>
          </article>
        )) : (
          <article className={styles.card}>
            <Cable size={18} />
            <h2>{t("mcp.empty.title", "No MCP servers configured")}</h2>
            <p>
              {t(
                "mcp.empty.hint",
                "Add a reviewed template or configure a custom server below.",
              )}
            </p>
          </article>
        )}
        </div>
      </article>

      <article className={styles.formCard}>
        <header>
          <div>
            <p className={styles.eyebrow}>
              {t("mcp.templates.eyebrow", "Bundled catalog")}
            </p>
            <h2>{t("mcp.templates.title", "Reviewed MCP templates")}</h2>
            <p className={styles.cardDescription}>
              {t(
                "mcp.templates.description",
                "Adding a template saves configuration only. It does not download a package, start a process, or enable tools.",
              )}
            </p>
          </div>
          <span>
            {t("mcp.templates.total", "{{total}} templates", {
              total: presets.length,
            })}
          </span>
        </header>
        <div className={styles.cardList}>
          {presets.map((preset) => (
            <article className={styles.card} key={preset.id}>
              <header>
                <div>
                  <p className={styles.eyebrow}>{mcpCategoryLabel(preset.category, t)} · {mcpTransportLabel(preset.transport, t)}</p>
                  <h2>{preset.displayName}</h2>
                </div>
                <span className={styles.badge} data-status={preset.configured ? "configured" : "available"}>
                  {preset.configured
                    ? t("mcp.templates.configured", "configured")
                    : t("mcp.templates.available", "available")}
                </span>
              </header>
              <p>{preset.description}</p>
              <p>{preset.requires}{preset.note ? ` · ${preset.note}` : ""}</p>
              {preset.missingEnvironment.length ? (
                <p>
                  {t("mcp.templates.setBeforeTesting", "Set before testing: {{names}}", {
                    names: preset.missingEnvironment.join(", "),
                  })}
                </p>
              ) : null}
              <div className={styles.cardActions}>
                <a href={preset.docsUrl} target="_blank" rel="noreferrer">
                  {t("mcp.templates.docs", "Docs")} <ExternalLink size={12} />
                </a>
                <button
                  type="button"
                  disabled={preset.configured || busy !== null}
                  onClick={() => void perform(`preset:${preset.id}`, async () => {
                    await catalog.addPreset(preset.id);
                    setFeedback({
                      tone: "success",
                      message: t(
                        "mcp.feedback.presetAdded",
                        "Added {{name}} in disabled state. No package was downloaded and no process was started. Test it before enabling.",
                        { name: preset.displayName },
                      ),
                    });
                  })}
                >
                  <Plus size={14} /> {t("mcp.templates.add", "Add server")}
                </button>
              </div>
            </article>
          ))}
        </div>
      </article>

      <article className={styles.formCard}>
        <header>
          <div>
            <p className={styles.eyebrow}>
              {editing
                ? t("mcp.form.editEyebrow", "Edit server")
                : t("mcp.form.customEyebrow", "Custom server")}
            </p>
            <h2>{editing?.name ?? t("mcp.form.title", "Connect an MCP server")}</h2>
          </div>
          {editing ? <button type="button" onClick={() => setDraft(EMPTY_DRAFT)}>{t("mcp.form.cancel", "Cancel")}</button> : null}
        </header>
        <div className={styles.formGrid}>
          <Field label={t("mcp.form.name", "Name")}><input value={draft.name} onChange={(event) => update(setDraft, "name", event.target.value)} /></Field>
          <Field label={t("mcp.form.scope", "Scope")}><select value={draft.scope} onChange={(event) => update(setDraft, "scope", event.target.value as ConfigScope)}><option value="user">{configScopeLabel("user", t)}</option><option value="project" disabled={!canUseProjectScope}>{configScopeLabel("project", t)}</option></select></Field>
          <Field label={t("mcp.form.transport", "Transport")}><select value={draft.transport} onChange={(event) => update(setDraft, "transport", event.target.value as Transport)}><option value="stdio">{mcpTransportLabel("stdio", t)}</option><option value="streamableHttp">{mcpTransportLabel("streamableHttp", t)}</option><option value="sse">{mcpTransportLabel("sse", t)}</option></select></Field>
          <Field label={t("mcp.form.approval", "Approval")}><select value={draft.approvalMode} onChange={(event) => update(setDraft, "approvalMode", event.target.value as Draft["approvalMode"])}><option value="writes">{mcpApprovalLabel("writes", t)}</option><option value="prompt">{mcpApprovalLabel("prompt", t)}</option><option value="auto">{mcpApprovalLabel("auto", t)}</option></select></Field>
          {draft.transport === "stdio" ? (
            <>
              <Field label={t("mcp.form.command", "Command")} wide><input value={draft.command} onChange={(event) => update(setDraft, "command", event.target.value)} placeholder={t("mcp.form.commandPlaceholder", "Executable or absolute path")} /></Field>
              <Field label={t("mcp.form.arguments", "Arguments")} wide><textarea rows={3} value={draft.args} onChange={(event) => update(setDraft, "args", event.target.value)} placeholder={t("mcp.form.argumentsPlaceholder", "One argument per line")} /></Field>
              <Field label={t("mcp.form.cwd", "Working directory")}><input value={draft.cwd} onChange={(event) => update(setDraft, "cwd", event.target.value)} /></Field>
              <Field label={t("mcp.form.forwardEnvironment", "Forward environment")}><input value={draft.envVars} onChange={(event) => update(setDraft, "envVars", event.target.value)} placeholder="OPTIONAL_NAME" /></Field>
              <Field label={t("mcp.form.requiredEnvironment", "Required environment")}><input value={draft.requiredEnvVars} onChange={(event) => update(setDraft, "requiredEnvVars", event.target.value)} placeholder="REQUIRED_API_KEY" /></Field>
              <Field label={t("mcp.form.credentialBindings", "Credential bindings")} wide><textarea rows={2} value={draft.credentialEnv} onChange={(event) => update(setDraft, "credentialEnv", event.target.value)} placeholder="API_KEY=openrouter" /></Field>
            </>
          ) : (
            <>
              <Field label="URL" wide><input value={draft.url} onChange={(event) => update(setDraft, "url", event.target.value)} placeholder="https://example.com/mcp" /></Field>
              <Field label={t("mcp.form.authentication", "Authentication")}><select value={draft.auth} onChange={(event) => update(setDraft, "auth", event.target.value as Draft["auth"])}><option value="none">{t("mcp.form.authNone", "None / configured headers")}</option><option value="oauth">OAuth</option></select></Field>
              <Field label={t("mcp.form.headerEnv", "Headers from environment")} wide><textarea rows={2} value={draft.headerEnv} onChange={(event) => update(setDraft, "headerEnv", event.target.value)} placeholder="Authorization=MY_HEADER_ENV" /></Field>
              <Field label={t("mcp.form.urlParamEnv", "URL parameters from environment")} wide><textarea rows={2} value={draft.urlParamEnv} onChange={(event) => update(setDraft, "urlParamEnv", event.target.value)} placeholder="apiKey=MY_API_KEY" /></Field>
            </>
          )}
          <Field label={t("mcp.form.enabledTools", "Enabled tools")}><input value={draft.enabledTools} onChange={(event) => update(setDraft, "enabledTools", event.target.value)} placeholder={t("mcp.form.enabledToolsPlaceholder", "Blank means all")} /></Field>
          <Field label={t("mcp.form.disabledTools", "Disabled tools")}><input value={draft.disabledTools} onChange={(event) => update(setDraft, "disabledTools", event.target.value)} /></Field>
          <Field label={t("mcp.form.startupTimeout", "Startup timeout (seconds)")}><input type="number" min="1" value={draft.startupTimeout} onChange={(event) => update(setDraft, "startupTimeout", event.target.value)} /></Field>
          <Field label={t("mcp.form.toolTimeout", "Tool timeout (seconds)")}><input type="number" min="1" value={draft.toolTimeout} onChange={(event) => update(setDraft, "toolTimeout", event.target.value)} /></Field>
          <label className={styles.checkboxField}><input type="checkbox" checked={draft.required} onChange={(event) => update(setDraft, "required", event.target.checked)} /> {t("mcp.form.required", "Required for agent startup")}</label>
        </div>
        <div className={styles.formActions}>
          <span>
            {t(
              "mcp.form.note",
              "New servers stay disabled. Saving never starts a process.",
            )}
          </span>
          <button className={styles.primaryButton} type="button" disabled={busy !== null || !draft.name.trim()} onClick={() => void save()}><Plus size={14} /> {editing ? t("mcp.form.saveChanges", "Save changes") : t("mcp.form.add", "Add custom server")}</button>
        </div>
      </article>
    </section>
  );
}

function Field({ label, wide = false, children }: { label: string; wide?: boolean; children: ReactNode }) {
  return <label className={wide ? styles.wideField : undefined}>{label}{children}</label>;
}

function update<K extends keyof Draft>(setDraft: Dispatch<SetStateAction<Draft>>, key: K, value: Draft[K]) {
  setDraft((current) => ({ ...current, [key]: value }));
}

function csv(value: string): string[] {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

function lines(value: string): string[] {
  return value.split("\n").map((item) => item.trim()).filter(Boolean);
}

function credentialMap(value: string): JsonObject {
  const result: JsonObject = {};
  for (const line of lines(value)) {
    const [name, connection] = line.split("=", 2).map((item) => item.trim());
    if (name && connection) result[name] = { credentialRef: `provider:${connection}` };
  }
  return result;
}

function mapLines(value: Record<string, string>): string {
  return Object.entries(value)
    .map(([name, source]) => `${name}=${source}`)
    .join("\n");
}

function stringMap(value: string): JsonObject {
  const result: JsonObject = {};
  for (const line of lines(value)) {
    const [name, source] = line.split("=", 2).map((item) => item.trim());
    if (name && source) result[name] = source;
  }
  return result;
}

function numberValue(value: string, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function capabilitySummary(
  t: TFunction,
  toolCount: number,
  resourceCount: number,
  promptCount: number,
): string {
  return t(
    "mcp.capabilitySummary",
    "{{tools}} tools · {{resources}} resources · {{prompts}} prompts",
    { tools: toolCount, resources: resourceCount, prompts: promptCount },
  );
}
