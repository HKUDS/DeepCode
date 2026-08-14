import {
  ChevronLeft,
  FlaskConical,
  KeyRound,
  Plus,
  Save,
  Server,
  SlidersHorizontal,
  Trash2,
  X,
} from "lucide-react";
import { useMemo, useState } from "react";

import type {
  ConnectionInfo,
  ProviderTestResult,
  ProviderUpsertParams,
} from "../../generated/app-server";
import { confirmAction } from "../../platform/confirmAction";
import type { ConnectionCatalogController } from "./useConnectionCatalog";
import { ConnectionVerification } from "./ConnectionVerification";
import styles from "./ConnectionSettings.module.css";

interface ConnectionSettingsProps {
  controller: ConnectionCatalogController;
  busy: boolean;
}

interface Draft {
  id: string;
  label: string;
  template: string;
  adapter: "openai_compat" | "anthropic";
  apiBase: string;
  apiKeyEnv: string;
  apiKey: string;
  credentialMode: "key" | "environment";
  clearApiKey: boolean;
  modelCatalog: "auto" | "openrouter" | "openai" | "anthropic" | "manual";
  manualModels: string;
  /** True when the launch environment currently provides this key: it
   * outranks a pasted key, so the form must say so instead of letting a
   * paste silently lose. */
  environmentShadows: boolean;
  shadowingEnvName: string;
}

const emptyDraft: Draft = {
  id: "",
  label: "",
  template: "",
  adapter: "openai_compat",
  apiBase: "",
  apiKeyEnv: "",
  apiKey: "",
  credentialMode: "key",
  clearApiKey: false,
  modelCatalog: "auto",
  manualModels: "",
  environmentShadows: false,
  shadowingEnvName: "",
};

export function ConnectionSettings({
  controller,
  busy,
}: ConnectionSettingsProps) {
  const [editing, setEditing] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<
    Record<string, ProviderTestResult>
  >({});
  const connections = useMemo(
    () =>
      [...(controller.catalog?.connections ?? [])].sort(
        (left, right) =>
          Number(right.configured) - Number(left.configured) ||
          left.label.localeCompare(right.label),
      ),
    [controller.catalog?.connections],
  );
  const selectedTemplate = controller.catalog?.templates.find(
    (template) => template.name === editing?.template,
  );
  const editingExisting = Boolean(
    editing &&
    connections.some(
      (connection) =>
        connection.id === editing.id &&
        (connection.configured || connection.explicit),
    ),
  );
  const endpointRequired = Boolean(selectedTemplate?.requiresApiBase);

  const beginEdit = (connection: ConnectionInfo) => {
    setEditing({
      id: connection.id,
      label: connection.label,
      template: connection.providerName,
      adapter: connection.adapter,
      apiBase: connection.apiBase ?? "",
      apiKeyEnv: connection.apiKeyEnv ?? "",
      apiKey: "",
      credentialMode: connection.apiKeyEnv ? "environment" : "key",
      clearApiKey: false,
      modelCatalog: connection.modelCatalog,
      manualModels: connection.manualModels.join("\n"),
      environmentShadows: connection.credentialSource === "environment",
      shadowingEnvName: connection.apiKeyEnv ?? "",
    });
  };

  const chooseTemplate = (templateName: string) => {
    const template = controller.catalog?.templates.find(
      (candidate) => candidate.name === templateName,
    );
    if (!template) return;
    const builtin = connections.find(
      (connection) => connection.id === template.name && !connection.configured,
    );
    const id = builtin?.id ?? nextConnectionId(template.name, connections);
    setEditing((current) => ({
      ...(current ?? emptyDraft),
      id,
      label: template.label,
      template: template.name,
      adapter: template.adapter === "anthropic" ? "anthropic" : "openai_compat",
      apiBase: template.defaultApiBase ?? "",
      modelCatalog: "auto",
      manualModels: "",
    }));
  };

  const save = async () => {
    if (!editing?.id.trim()) return;
    setSaving(true);
    try {
      const connectionId = editing.id.trim().toLocaleLowerCase();
      const connection: ProviderUpsertParams["connection"] = {
        id: connectionId,
        label: editing.label.trim() || editing.id.trim(),
        template: editing.template,
        adapter: editing.adapter,
        apiBase: editing.apiBase.trim() || null,
        apiKeyEnv:
          editing.credentialMode === "environment"
            ? editing.apiKeyEnv.trim() || null
            : null,
        modelCatalog: editing.modelCatalog,
        manualModels: editing.manualModels
          .split(/\r?\n|,/)
          .map((value) => value.trim())
          .filter(Boolean),
        enabled: true,
      };
      if (editing.credentialMode === "key" && editing.apiKey.trim()) {
        connection.apiKey = editing.apiKey.trim();
      }
      if (editing.clearApiKey) connection.clearApiKey = true;
      await controller.upsert(connection);
      setEditing(null);
      setTestingId(connectionId);
      try {
        const result = await controller.test(connectionId);
        setTestResults((current) => ({ ...current, [connectionId]: result }));
      } finally {
        setTestingId(null);
      }
    } catch {
      // The shared controller owns the sanitized user-facing error.
    } finally {
      setSaving(false);
    }
  };

  const test = async (connectionId: string) => {
    setTestingId(connectionId);
    try {
      const result = await controller.test(connectionId);
      setTestResults((current) => ({ ...current, [connectionId]: result }));
    } catch {
      // The shared controller owns the sanitized user-facing error.
    } finally {
      setTestingId(null);
    }
  };

  const remove = async (connection: ConnectionInfo) => {
    if (
      !(await confirmAction(
        `Remove “${connection.label}”? Its saved API key will also be deleted. Existing Session history is kept.`,
        {
          title: "Remove LLM connection",
          confirmLabel: "Remove connection",
          kind: "warning",
        },
      ))
    ) {
      return;
    }
    try {
      await controller.remove(connection.id);
    } catch {
      // The shared controller owns the sanitized user-facing error.
    }
  };

  return (
    <section className={styles.section} aria-labelledby="connections-title">
      <header className={styles.heading}>
        <div>
          <p>Step 1 · Connect a provider</p>
          <h2 id="connections-title">AI providers</h2>
          <span>
            Add the service that supplies your models. DeepCode keeps API keys
            in user-private storage and shares the connection with CLI and
            Desktop.
          </span>
        </div>
        <button
          type="button"
          className={styles.addButton}
          onClick={() => setEditing({ ...emptyDraft })}
          disabled={busy || saving}
        >
          <Plus size={14} />
          Add provider
        </button>
      </header>

      {controller.error ? (
        <p className={styles.error}>{controller.error}</p>
      ) : null}

      <div className={styles.connectionRail}>
        {connections.filter(isManagedConnection).map((connection) => {
          const result = testResults[connection.id];
          return (
            <article key={connection.id} className={styles.connection}>
              <span
                className={styles.statusLight}
                data-status={
                  result?.status ??
                  (connection.configured ? "configured" : "error")
                }
                aria-hidden="true"
              />
              <div className={styles.connectionBody}>
                <header>
                  <div>
                    <strong>{connection.label}</strong>
                    <code>{connection.id}</code>
                  </div>
                  <span data-status={result?.status ?? "configured"}>
                    {connectionStatus(connection, result)}
                  </span>
                </header>
                <p>
                  {connection.apiBase ?? "Provider default endpoint"}
                  <small>
                    {connection.adapter.replace("_", " ")} ·{" "}
                    {credentialLabel(connection)}
                  </small>
                </p>
                {result ? (
                  <ConnectionVerification result={result} compact />
                ) : null}
              </div>
              <div className={styles.actions}>
                <button
                  type="button"
                  onClick={() => void test(connection.id)}
                  disabled={busy || testingId === connection.id}
                  title="Check credential and model catalog"
                >
                  <FlaskConical size={14} />
                  {testingId === connection.id ? "Checking…" : "Check"}
                </button>
                <button
                  type="button"
                  onClick={() => beginEdit(connection)}
                  disabled={busy}
                >
                  Edit
                </button>
                {connection.explicit ? (
                  <button
                    type="button"
                    className={styles.removeButton}
                    onClick={() => void remove(connection)}
                    disabled={busy}
                    title="Remove connection"
                  >
                    <Trash2 size={14} />
                  </button>
                ) : null}
              </div>
            </article>
          );
        })}
        {!controller.loading && !connections.some(isManagedConnection) ? (
          <div className={styles.emptyState}>
            <Server size={18} />
            <strong>No provider connected</strong>
            <span>Add an API provider or a local model service to begin.</span>
            <button type="button" onClick={() => setEditing({ ...emptyDraft })}>
              <Plus size={14} /> Add provider
            </button>
          </div>
        ) : null}
        {controller.loading ? (
          <p className={styles.loading}>Loading connections…</p>
        ) : null}
      </div>

      {editing ? (
        <div className={styles.editorBackdrop} role="presentation">
          <section
            className={styles.editor}
            role="dialog"
            aria-modal="true"
            aria-labelledby="connection-editor-title"
          >
            <header>
              <div>
                <KeyRound size={16} />
                <span>
                  <strong id="connection-editor-title">
                    {editingExisting ? "Edit provider" : "Connect a provider"}
                  </strong>
                  <small>One setup works in CLI and Desktop</small>
                </span>
              </div>
              <button
                type="button"
                onClick={() => setEditing(null)}
                aria-label="Close connection editor"
              >
                <X size={16} />
              </button>
            </header>
            {!editing.template ? (
              <div className={styles.providerPicker}>
                <div>
                  <strong>Choose where your models come from</strong>
                  <span>
                    Provider defaults fill in the correct endpoint and protocol.
                  </span>
                </div>
                <div className={styles.providerGrid}>
                  {controller.catalog?.templates.map((template) => (
                    <button
                      type="button"
                      key={template.name}
                      onClick={() => chooseTemplate(template.name)}
                    >
                      <Server size={16} />
                      <span>
                        <strong>{template.label}</strong>
                        <small>
                          {template.local ? "Runs locally" : "Cloud provider"}
                        </small>
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className={styles.form}>
                {controller.error ? (
                  <p className={`${styles.error} ${styles.wide}`}>
                    {controller.error}
                  </p>
                ) : null}
                <div className={`${styles.providerSummary} ${styles.wide}`}>
                  <Server size={18} />
                  <span>
                    <strong>{selectedTemplate?.label ?? editing.label}</strong>
                    <small>
                      {selectedTemplate?.local
                        ? "Local model service"
                        : "API provider"}
                    </small>
                  </span>
                  {!editingExisting ? (
                    <button
                      type="button"
                      onClick={() => setEditing({ ...emptyDraft })}
                    >
                      <ChevronLeft size={12} /> Change
                    </button>
                  ) : null}
                </div>
                <label className={styles.wide}>
                  Display name
                  <input
                    value={editing.label}
                    onChange={(event) =>
                      setEditing({ ...editing, label: event.target.value })
                    }
                    placeholder={selectedTemplate?.label ?? "My provider"}
                  />
                </label>
                {endpointRequired ? (
                  <label className={styles.wide}>
                    API endpoint
                    <input
                      value={editing.apiBase}
                      onChange={(event) =>
                        setEditing({
                          ...editing,
                          apiBase: event.target.value,
                        })
                      }
                      placeholder="https://llm.example.com/v1"
                      aria-required="true"
                    />
                    <small>
                      This provider has no universal endpoint. Enter the base
                      URL of your server.
                    </small>
                  </label>
                ) : null}
                {!selectedTemplate?.local ? (
                  <fieldset className={`${styles.credentials} ${styles.wide}`}>
                    <legend>Credential</legend>
                    <div className={styles.credentialTabs}>
                      <button
                        type="button"
                        data-active={editing.credentialMode === "key"}
                        onClick={() =>
                          setEditing({ ...editing, credentialMode: "key" })
                        }
                      >
                        Paste API key
                      </button>
                      <button
                        type="button"
                        data-active={editing.credentialMode === "environment"}
                        onClick={() =>
                          setEditing({
                            ...editing,
                            credentialMode: "environment",
                          })
                        }
                      >
                        Environment variable
                      </button>
                    </div>
                    {editing.credentialMode === "key" &&
                    editing.environmentShadows ? (
                      <p className={styles.credentialShadowNote} role="note">
                        {editing.shadowingEnvName
                          ? `The launch environment variable ${editing.shadowingEnvName} currently provides this key and takes precedence. `
                          : "A launch environment variable currently provides this key and takes precedence. "}
                        A pasted key is saved but will not take effect until
                        that variable is unset.
                      </p>
                    ) : null}
                    {editing.credentialMode === "key" ? (
                      <input
                        type="password"
                        value={editing.apiKey}
                        onChange={(event) =>
                          setEditing({
                            ...editing,
                            apiKey: event.target.value,
                            clearApiKey: false,
                          })
                        }
                        placeholder={
                          editingExisting
                            ? "Leave blank to keep the saved key"
                            : "Paste API key"
                        }
                        autoComplete="new-password"
                        disabled={editing.clearApiKey}
                        aria-label="API key"
                      />
                    ) : (
                      <input
                        value={editing.apiKeyEnv}
                        onChange={(event) =>
                          setEditing({
                            ...editing,
                            apiKeyEnv: event.target.value,
                          })
                        }
                        placeholder={
                          selectedTemplate?.apiKeyEnv ?? "PROVIDER_API_KEY"
                        }
                        aria-label="API key environment variable"
                      />
                    )}
                    <small>
                      Stored keys are private and never returned to the app UI.
                    </small>
                  </fieldset>
                ) : null}
                <details className={`${styles.advanced} ${styles.wide}`}>
                  <summary>
                    <SlidersHorizontal size={14} /> Advanced connection settings
                  </summary>
                  <div>
                    <label>
                      Connection ID
                      <input
                        value={editing.id}
                        onChange={(event) =>
                          setEditing({ ...editing, id: event.target.value })
                        }
                        placeholder="provider-personal"
                        disabled={editingExisting}
                      />
                    </label>
                    {!endpointRequired ? (
                      <label>
                        API base
                        <input
                          value={editing.apiBase}
                          onChange={(event) =>
                            setEditing({
                              ...editing,
                              apiBase: event.target.value,
                            })
                          }
                          placeholder="Use provider default"
                        />
                      </label>
                    ) : null}
                    <label>
                      Adapter
                      <select
                        value={editing.adapter}
                        onChange={(event) =>
                          setEditing({
                            ...editing,
                            adapter: event.target.value as Draft["adapter"],
                          })
                        }
                      >
                        <option value="openai_compat">OpenAI compatible</option>
                        <option value="anthropic">Anthropic native</option>
                      </select>
                    </label>
                    <label>
                      Model catalog
                      <select
                        value={editing.modelCatalog}
                        onChange={(event) =>
                          setEditing({
                            ...editing,
                            modelCatalog: event.target
                              .value as Draft["modelCatalog"],
                          })
                        }
                      >
                        <option value="auto">Automatic</option>
                        <option value="openrouter">OpenRouter</option>
                        <option value="openai">OpenAI compatible</option>
                        <option value="anthropic">Anthropic</option>
                        <option value="manual">Manual list</option>
                      </select>
                    </label>
                    {editing.modelCatalog === "manual" ? (
                      <label className={styles.wide}>
                        Manual models
                        <textarea
                          value={editing.manualModels}
                          onChange={(event) =>
                            setEditing({
                              ...editing,
                              manualModels: event.target.value,
                            })
                          }
                          placeholder={
                            "One model ID per line\nmoonshotai/kimi-k2.5"
                          }
                          rows={3}
                        />
                      </label>
                    ) : null}
                    {editingExisting ? (
                      <label
                        className={`${styles.credentialAction} ${styles.wide}`}
                      >
                        <input
                          type="checkbox"
                          checked={editing.clearApiKey}
                          aria-label="Remove saved API key"
                          onChange={(event) =>
                            setEditing({
                              ...editing,
                              apiKey: event.target.checked
                                ? ""
                                : editing.apiKey,
                              clearApiKey: event.target.checked,
                            })
                          }
                        />
                        <span>
                          <strong>Remove the saved API key</strong>
                          <small>
                            Environment and legacy credentials are unchanged.
                          </small>
                        </span>
                      </label>
                    ) : null}
                  </div>
                </details>
              </div>
            )}
            <footer>
              <span>
                Saving checks credentials and model discovery. It does not send
                project content.
              </span>
              <button
                type="button"
                onClick={() => setEditing(null)}
                disabled={saving}
              >
                Cancel
              </button>
              <button
                type="button"
                className={styles.saveButton}
                onClick={() => void save()}
                disabled={
                  saving ||
                  !editing.id.trim() ||
                  !editing.template ||
                  (endpointRequired && !editing.apiBase.trim())
                }
              >
                <Save size={14} />
                {saving ? "Saving…" : "Save and check"}
              </button>
            </footer>
          </section>
        </div>
      ) : null}
    </section>
  );
}

function credentialLabel(connection: ConnectionInfo): string {
  switch (connection.credentialSource) {
    case "environment":
      return `environment${connection.apiKeyEnv ? ` · ${connection.apiKeyEnv}` : ""}`;
    case "credential_store":
      return "credential store";
    case "legacy_config":
      return "legacy config";
    case "not_required":
      return "no key required";
    default:
      return "no key";
  }
}

function connectionStatus(
  connection: ConnectionInfo,
  result: ProviderTestResult | undefined,
): string {
  if (result?.status === "ready") return "Model verified";
  if (result?.status === "connected") return "Catalog connected";
  if (result?.status === "limited") return "Model check needed";
  if (result?.status === "error") return "Needs attention";
  return connection.configured ? "Credential saved" : "Needs credential";
}

function isManagedConnection(connection: ConnectionInfo): boolean {
  return connection.explicit || (connection.configured && !connection.local);
}

function nextConnectionId(
  template: string,
  connections: ConnectionInfo[],
): string {
  const existing = new Set(connections.map((connection) => connection.id));
  if (!existing.has(template)) return template;
  let suffix = 2;
  while (existing.has(`${template}-${suffix}`)) suffix += 1;
  return `${template}-${suffix}`;
}
