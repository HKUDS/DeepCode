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

import type {
  ConfigScope,
  JsonObject,
  McpServerInfo,
  Project,
} from "../../generated/app-server";
import type { DesktopRuntime } from "../../rpc/contracts";
import styles from "../management/ManagementWorkspace.module.css";
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
  runtime: DesktopRuntime;
  project: Project | null;
}) {
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
    const server: JsonObject = {
      type: draft.transport,
      enabled: editing?.enabled ?? false,
      required: draft.required,
      startupTimeoutSeconds: numberValue(draft.startupTimeout, 10),
      toolTimeoutSeconds: numberValue(draft.toolTimeout, 60),
      approvalMode: draft.approvalMode,
      disabledTools: csv(draft.disabledTools),
      ...(draft.enabledTools.trim()
        ? { enabledTools: csv(draft.enabledTools) }
        : {}),
      ...(draft.transport === "stdio"
        ? {
            command: draft.command.trim(),
            args: lines(draft.args),
            ...(draft.cwd.trim() ? { cwd: draft.cwd.trim() } : {}),
            envVars: csv(draft.envVars),
            requiredEnvVars: csv(draft.requiredEnvVars),
            ...(draft.credentialEnv.trim()
              ? { credentialEnv: credentialMap(draft.credentialEnv) }
              : {}),
          }
        : {
            url: draft.url.trim(),
            auth: draft.auth === "oauth" ? "oauth" : null,
            ...(draft.headerEnv.trim()
              ? { envHttpHeaders: stringMap(draft.headerEnv) }
              : {}),
            ...(draft.urlParamEnv.trim()
              ? { envUrlParams: stringMap(draft.urlParamEnv) }
              : {}),
          }),
    };
    await perform(`save:${name}`, async () => {
      await catalog.upsert(name, draft.scope, server);
      setDraft(EMPTY_DRAFT);
      setFeedback({
        tone: "success",
        message: `Saved ${name}. No server process was started. Test it before enabling.`,
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
      credentialEnv: server.credentialEnvKeys.map((name) => `${name}=`).join("\n"),
      enabledTools: server.enabledTools?.join(", ") ?? "",
      disabledTools: server.disabledTools.join(", "),
      approvalMode: server.approvalMode,
      startupTimeout: String(server.startupTimeoutSeconds),
      toolTimeout: String(server.toolTimeoutSeconds),
      required: server.required,
    });
  };

  return (
    <section className={styles.page}>
      <header className={styles.pageHeader}>
        <div>
          <p className={styles.eyebrow}>Agent capabilities</p>
          <h1>MCP Servers</h1>
          <p>
            Add an MCP server, test the connection, then enable it. Enabled
            tools become available to the coding agent automatically when a
            task needs them.
          </p>
        </div>
        <button
          className={styles.secondaryButton}
          type="button"
          disabled={catalog.loading}
          onClick={() => void catalog.refresh()}
        >
          <RefreshCw size={14} /> Refresh
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
          Browser did not open?{" "}
          <a href={authorizationUrl} target="_blank" rel="noreferrer">
            Continue authorization <ExternalLink size={12} />
          </a>
        </p>
      ) : null}

      <article className={styles.formCard}>
        <header>
          <div>
            <p className={styles.eyebrow}>Configured servers</p>
            <h2>Available to DeepCode</h2>
          </div>
          <span>{servers.length} configured</span>
        </header>
        <div className={styles.cardList}>
          {servers.length ? servers.map((server) => (
            <article className={styles.card} key={`${server.source}:${server.id}`}>
            <header>
              <div>
                <p className={styles.eyebrow}>{server.source} · {server.transport}</p>
                <h2>{server.name}</h2>
              </div>
              <span className={styles.badge} data-status={serverStatus(server)}>
                {serverStatus(server)}
              </span>
            </header>
            <p>{server.configurationMessage}</p>
            <dl className={styles.metadata}>
              <div><dt>Endpoint</dt><dd>{server.command ?? server.url}</dd></div>
              <div><dt>Configuration</dt><dd>{server.configurationState}</dd></div>
              <div><dt>Authentication</dt><dd>{server.authState}</dd></div>
              <div><dt>Last check</dt><dd>{server.runtimeMessage}</dd></div>
              <div><dt>Capabilities</dt><dd>{capabilitySummary(server.toolCount, server.resourceCount, server.promptCount)}</dd></div>
              <div><dt>Approval</dt><dd>{server.approvalMode}</dd></div>
              {server.missingEnvKeys.length ? (
                <div><dt>Missing environment</dt><dd>{server.missingEnvKeys.join(", ")}</dd></div>
              ) : null}
              {server.policyKey ? <div><dt>Plugin policy</dt><dd>{server.policyKey}</dd></div> : null}
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
                      ? `${server.name} passed its connection test: ${capabilitySummary(result.toolCount, result.resourceCount, result.promptCount)}. The test connection is now closed.`
                      : `${server.name} failed its connection test: ${result.error}`,
                  });
                })}
              >
                <Play size={13} /> Test connection
              </button>
              {server.auth === "oauth" ? (
                server.authState === "authenticated" ? (
                  <button type="button" disabled={busy !== null} onClick={() => void perform(`logout:${server.id}`, async () => {
                    await catalog.logout(server.id);
                    setFeedback({ tone: "success", message: `Signed out of ${server.name}.` });
                  })}>
                    <LogOut size={13} /> Logout
                  </button>
                ) : (
                  <button type="button" disabled={busy !== null || server.authState === "authorizing"} onClick={() => void perform(`login:${server.id}`, async () => {
                    const flow = await catalog.startOAuth(server.id);
                    setAuthorizationUrl(flow.authorizationUrl);
                    setFeedback({ tone: "success", message: `Authorization started for ${server.name}. Return here after approving access.` });
                  })}>
                    <LogIn size={13} /> Authenticate
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
                        ? `Disabled ${server.name}. It will not be offered to the agent on new turns.`
                        : `Enabled ${server.name}. The agent can now call its tools when a task needs them.`,
                    });
                  })}>
                    <CirclePower size={13} /> {server.enabled ? "Disable" : "Enable"}
                  </button>
                  <button type="button" onClick={() => edit(server)}><Pencil size={13} /> Edit</button>
                  <button type="button" disabled={busy !== null || server.configurationState === "blocked"} onClick={() => {
                    if (window.confirm(`Remove MCP server “${server.name}”?`)) {
                      void perform(`remove:${server.id}`, async () => {
                        await catalog.remove(server.name, server.source === "project" ? "project" : "user");
                        setFeedback({ tone: "success", message: `Removed ${server.name}.` });
                      });
                    }
                  }}><Trash2 size={13} /> Remove</button>
                </>
              ) : null}
            </div>
          </article>
        )) : (
          <article className={styles.card}>
            <Cable size={18} />
            <h2>No MCP servers configured</h2>
            <p>Add a reviewed template or configure a custom server below.</p>
          </article>
        )}
        </div>
      </article>

      <article className={styles.formCard}>
        <header>
          <div>
            <p className={styles.eyebrow}>Bundled catalog</p>
            <h2>Reviewed MCP templates</h2>
            <p className={styles.cardDescription}>
              Adding a template saves configuration only. It does not download
              a package, start a process, or enable tools.
            </p>
          </div>
          <span>{presets.length} templates</span>
        </header>
        <div className={styles.cardList}>
          {presets.map((preset) => (
            <article className={styles.card} key={preset.id}>
              <header>
                <div>
                  <p className={styles.eyebrow}>{preset.category} · {preset.transport}</p>
                  <h2>{preset.displayName}</h2>
                </div>
                <span className={styles.badge} data-status={preset.configured ? "configured" : "available"}>
                  {preset.configured ? "configured" : "available"}
                </span>
              </header>
              <p>{preset.description}</p>
              <p>{preset.requires}{preset.note ? ` · ${preset.note}` : ""}</p>
              {preset.missingEnvironment.length ? (
                <p>Set before testing: {preset.missingEnvironment.join(", ")}</p>
              ) : null}
              <div className={styles.cardActions}>
                <a href={preset.docsUrl} target="_blank" rel="noreferrer">
                  Docs <ExternalLink size={12} />
                </a>
                <button
                  type="button"
                  disabled={preset.configured || busy !== null}
                  onClick={() => void perform(`preset:${preset.id}`, async () => {
                    await catalog.addPreset(preset.id);
                    setFeedback({
                      tone: "success",
                      message: `Added ${preset.displayName} in disabled state. No package was downloaded and no process was started. Test it before enabling.`,
                    });
                  })}
                >
                  <Plus size={13} /> Add server
                </button>
              </div>
            </article>
          ))}
        </div>
      </article>

      <article className={styles.formCard}>
        <header>
          <div>
            <p className={styles.eyebrow}>{editing ? "Edit server" : "Custom server"}</p>
            <h2>{editing?.name ?? "Connect an MCP server"}</h2>
          </div>
          {editing ? <button type="button" onClick={() => setDraft(EMPTY_DRAFT)}>Cancel</button> : null}
        </header>
        <div className={styles.formGrid}>
          <Field label="Name"><input value={draft.name} onChange={(event) => update(setDraft, "name", event.target.value)} /></Field>
          <Field label="Scope"><select value={draft.scope} onChange={(event) => update(setDraft, "scope", event.target.value as ConfigScope)}><option value="user">User</option><option value="project" disabled={!canUseProjectScope}>This project</option></select></Field>
          <Field label="Transport"><select value={draft.transport} onChange={(event) => update(setDraft, "transport", event.target.value as Transport)}><option value="stdio">Local stdio</option><option value="streamableHttp">Streamable HTTP</option><option value="sse">SSE</option></select></Field>
          <Field label="Approval"><select value={draft.approvalMode} onChange={(event) => update(setDraft, "approvalMode", event.target.value as Draft["approvalMode"])}><option value="writes">Ask for writes</option><option value="prompt">Always ask</option><option value="auto">Use global policy</option><option value="approve">Approved when globally allowed</option></select></Field>
          {draft.transport === "stdio" ? (
            <>
              <Field label="Command" wide><input value={draft.command} onChange={(event) => update(setDraft, "command", event.target.value)} placeholder="Executable or absolute path" /></Field>
              <Field label="Arguments" wide><textarea rows={3} value={draft.args} onChange={(event) => update(setDraft, "args", event.target.value)} placeholder="One argument per line" /></Field>
              <Field label="Working directory"><input value={draft.cwd} onChange={(event) => update(setDraft, "cwd", event.target.value)} /></Field>
              <Field label="Forward environment"><input value={draft.envVars} onChange={(event) => update(setDraft, "envVars", event.target.value)} placeholder="OPTIONAL_NAME" /></Field>
              <Field label="Required environment"><input value={draft.requiredEnvVars} onChange={(event) => update(setDraft, "requiredEnvVars", event.target.value)} placeholder="REQUIRED_API_KEY" /></Field>
              <Field label="Credential bindings" wide><textarea rows={2} value={draft.credentialEnv} onChange={(event) => update(setDraft, "credentialEnv", event.target.value)} placeholder="API_KEY=openrouter" /></Field>
            </>
          ) : (
            <>
              <Field label="URL" wide><input value={draft.url} onChange={(event) => update(setDraft, "url", event.target.value)} placeholder="https://example.com/mcp" /></Field>
              <Field label="Authentication"><select value={draft.auth} onChange={(event) => update(setDraft, "auth", event.target.value as Draft["auth"])}><option value="none">None / configured headers</option><option value="oauth">OAuth</option></select></Field>
              <Field label="Headers from environment" wide><textarea rows={2} value={draft.headerEnv} onChange={(event) => update(setDraft, "headerEnv", event.target.value)} placeholder="Authorization=MY_HEADER_ENV" /></Field>
              <Field label="URL parameters from environment" wide><textarea rows={2} value={draft.urlParamEnv} onChange={(event) => update(setDraft, "urlParamEnv", event.target.value)} placeholder="apiKey=MY_API_KEY" /></Field>
            </>
          )}
          <Field label="Enabled tools"><input value={draft.enabledTools} onChange={(event) => update(setDraft, "enabledTools", event.target.value)} placeholder="Blank means all" /></Field>
          <Field label="Disabled tools"><input value={draft.disabledTools} onChange={(event) => update(setDraft, "disabledTools", event.target.value)} /></Field>
          <Field label="Startup timeout (seconds)"><input type="number" min="1" value={draft.startupTimeout} onChange={(event) => update(setDraft, "startupTimeout", event.target.value)} /></Field>
          <Field label="Tool timeout (seconds)"><input type="number" min="1" value={draft.toolTimeout} onChange={(event) => update(setDraft, "toolTimeout", event.target.value)} /></Field>
          <label className={styles.checkboxField}><input type="checkbox" checked={draft.required} onChange={(event) => update(setDraft, "required", event.target.checked)} /> Required for agent startup</label>
        </div>
        <div className={styles.formActions}>
          <span>New servers stay disabled. Saving never starts a process.</span>
          <button className={styles.primaryButton} type="button" disabled={busy !== null || !draft.name.trim()} onClick={() => void save()}><Plus size={14} /> {editing ? "Save changes" : "Add custom server"}</button>
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

function serverStatus(server: McpServerInfo): string {
  if (!server.enabled) return "disabled";
  return server.runtimeState;
}

function capabilitySummary(
  toolCount: number,
  resourceCount: number,
  promptCount: number,
): string {
  return `${toolCount} tools · ${resourceCount} resources · ${promptCount} prompts`;
}
