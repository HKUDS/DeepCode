import {
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
  CatalogModel,
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
  /** Environment-variable reference — the advanced alternative to a
   * stored key (the write-only input is the primary path, dsh style). */
  apiKeyEnv: string;
  apiKey: string;
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
  // The live model listing fetched for the connection being edited, plus
  // the ids picked for adoption. Fetched results are offered, never
  // auto-written (the dsh rule): only "Add selected" touches the draft.
  // State is keyed by its owning editor id and derived at render time, so
  // switching editors needs no synchronous reset inside an effect and a
  // late-landing listing can never bleed into another connection's editor.
  const [modelFetchState, setModelFetchState] = useState<{
    editorId: string;
    loading: boolean;
    error: string | null;
    models: CatalogModel[] | null;
  } | null>(null);
  const [pickedState, setPickedState] = useState<{
    editorId: string;
    picked: ReadonlySet<string>;
  } | null>(null);
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
        apiKeyEnv: editing.apiKeyEnv.trim() || null,
        modelCatalog: editing.modelCatalog,
        manualModels: editing.manualModels
          .split(/\r?\n|,/)
          .map((value) => value.trim())
          .filter(Boolean),
        enabled: true,
      };
      if (editing.apiKey.trim()) {
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

  const editingId = editing?.id ?? null;
  const modelFetch =
    modelFetchState?.editorId === editingId
      ? modelFetchState
      : { loading: false, error: null, models: null };
  const pickedModels: ReadonlySet<string> =
    pickedState?.editorId === editingId ? pickedState.picked : new Set();
  const setPickedModels = (picked: ReadonlySet<string>) => {
    if (editingId !== null) setPickedState({ editorId: editingId, picked });
  };

  const manualModelIds = useMemo(
    () =>
      new Set(
        (editing?.manualModels ?? "")
          .split(/\r?\n|,/)
          .map((value) => value.trim())
          .filter(Boolean),
      ),
    [editing?.manualModels],
  );

  const fetchModels = async () => {
    if (!editing || editingId === null) return;
    setModelFetchState({
      editorId: editingId,
      loading: true,
      error: null,
      models: null,
    });
    try {
      const result = await controller.models(
        editing.id.trim().toLocaleLowerCase(),
        true,
      );
      setModelFetchState({
        editorId: editingId,
        loading: false,
        error:
          result.models.length === 0
            ? (result.error ??
              "The provider listed no models. Add them by hand.")
            : null,
        models: result.models,
      });
    } catch (cause) {
      setModelFetchState({
        editorId: editingId,
        loading: false,
        error: cause instanceof Error ? cause.message : String(cause),
        models: null,
      });
    }
  };

  const adoptPickedModels = () => {
    if (!editing || pickedModels.size === 0) return;
    const merged = [
      ...manualModelIds,
      ...[...pickedModels].filter((id) => !manualModelIds.has(id)),
    ];
    setEditing({
      ...editing,
      // A hand-picked list REPLACES the provider catalog for this
      // connection (the dsh rule), so adoption switches to manual mode.
      modelCatalog: "manual",
      manualModels: merged.join("\n"),
    });
    setPickedModels(new Set());
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
        <div className={styles.addActions}>
          <label className={styles.addSelect}>
            <select
              aria-label="Add provider"
              value=""
              disabled={busy || saving}
              onChange={(event) => {
                if (!event.target.value) return;
                setEditing({ ...emptyDraft });
                chooseTemplate(event.target.value);
                event.target.value = "";
              }}
            >
              <option value="">Add provider…</option>
              {controller.catalog?.templates
                .filter((template) => template.name !== "custom")
                .map((template) => (
                  <option key={template.name} value={template.name}>
                    {template.label} · {template.local ? "local" : "cloud"}
                  </option>
                ))}
            </select>
          </label>
          <button
            type="button"
            className={styles.addButton}
            onClick={() => {
              setEditing({ ...emptyDraft });
              chooseTemplate("custom");
            }}
            disabled={busy || saving}
            title="Declare an OpenAI-compatible or Anthropic endpoint DeepCode does not ship"
          >
            <Plus size={14} />
            Add a custom provider
          </button>
        </div>
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
                  <small>
                    <i
                      className={styles.credentialDot}
                      data-configured={connection.configured || undefined}
                      role="img"
                      aria-label={
                        connection.configured
                          ? "Credential configured"
                          : "Credential missing"
                      }
                    />
                    {credentialLabel(connection)} ·{" "}
                    {modelFaceLabel(connection, result)}
                  </small>
                </p>
                {result ? (
                  <ConnectionVerification result={result} compact />
                ) : testingId === connection.id ? (
                  <p className={styles.loading}>Checking…</p>
                ) : null}
              </div>
              <div className={styles.actions}>
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
            <span>
              Pick a provider from “Add provider…” above, or declare a custom
              endpoint, to begin.
            </span>
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
            {editing.template ? (
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
                    {editing.environmentShadows ? (
                      <p className={styles.credentialShadowNote} role="note">
                        {editing.shadowingEnvName
                          ? `The launch environment variable ${editing.shadowingEnvName} currently provides this key and takes precedence. `
                          : "A launch environment variable currently provides this key and takes precedence. "}
                        Unset it in the launching shell to manage the key
                        here.
                      </p>
                    ) : null}
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
                        editing.environmentShadows
                          ? "Provided by the launch environment (read-only)"
                          : editingExisting
                            ? "Leave blank to keep the saved key"
                            : "Paste API key"
                      }
                      autoComplete="new-password"
                      disabled={
                        editing.clearApiKey || editing.environmentShadows
                      }
                      aria-label="API key"
                    />
                    <small>
                      Write-only: stored keys are private and never returned to
                      the app UI. Referencing an environment variable instead
                      is available under Advanced.
                    </small>
                  </fieldset>
                ) : null}
                {editing.template ? (
                  <fieldset className={`${styles.modelsField} ${styles.wide}`}>
                    <legend>Models</legend>
                    {editingExisting ? (
                      <div className={styles.modelFetchRow}>
                        <button
                          type="button"
                          onClick={() => void fetchModels()}
                          disabled={modelFetch.loading || busy}
                        >
                          {modelFetch.loading
                            ? "Fetching…"
                            : "Fetch models from provider"}
                        </button>
                        <small>
                          Lists what this connection's endpoint actually
                          serves; picks become its manual model list.
                        </small>
                      </div>
                    ) : (
                      <small>
                        Save the connection first, then fetch its live model
                        list here.
                      </small>
                    )}
                    {modelFetch.error ? (
                      <p className={styles.modelFetchError} role="alert">
                        {modelFetch.error}
                      </p>
                    ) : null}
                    {modelFetch.models && modelFetch.models.length > 0 ? (
                      <>
                        <ul
                          className={styles.modelPicker}
                          aria-label="Discovered models"
                        >
                          {modelFetch.models.map((model) => {
                            const listed = manualModelIds.has(model.id);
                            const picked = pickedModels.has(model.id);
                            return (
                              <li key={model.id}>
                                <label data-listed={listed || undefined}>
                                  <input
                                    type="checkbox"
                                    checked={listed || picked}
                                    disabled={listed}
                                    onChange={() => {
                                      const next = new Set(pickedModels);
                                      if (picked) next.delete(model.id);
                                      else next.add(model.id);
                                      setPickedModels(next);
                                    }}
                                  />
                                  <span>
                                    <strong>{model.name || model.id}</strong>
                                    <small>
                                      {model.id}
                                      {model.contextWindow
                                        ? ` · ${Math.round(model.contextWindow / 1000)}K context`
                                        : ""}
                                      {listed ? " · already listed" : ""}
                                    </small>
                                  </span>
                                </label>
                              </li>
                            );
                          })}
                        </ul>
                        <button
                          type="button"
                          className={styles.adoptButton}
                          onClick={adoptPickedModels}
                          disabled={pickedModels.size === 0}
                        >
                          Add {pickedModels.size || "selected"} model
                          {pickedModels.size === 1 ? "" : "s"} to this
                          connection
                        </button>
                      </>
                    ) : null}
                    {manualModelIds.size > 0 ? (
                      <small>
                        Manual list: {manualModelIds.size} model
                        {manualModelIds.size === 1 ? "" : "s"} (editable under
                        Advanced).
                      </small>
                    ) : null}
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
                    {!selectedTemplate?.local ? (
                      <label>
                        API key environment variable
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
                        />
                        <small>
                          Reference a launch-environment variable instead of a
                          stored key. Leave blank to use the pasted key.
                        </small>
                      </label>
                    ) : null}
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
            ) : null}
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

function modelFaceLabel(
  connection: ConnectionInfo,
  result: ProviderTestResult | undefined,
): string {
  if (connection.manualModels.length > 0) {
    return `manual · ${connection.manualModels.length} model${
      connection.manualModels.length === 1 ? "" : "s"
    }`;
  }
  if (result?.modelCount) return `catalog · ${result.modelCount} models`;
  return connection.modelCatalog === "auto"
    ? "provider catalog"
    : `${connection.modelCatalog} catalog`;
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
