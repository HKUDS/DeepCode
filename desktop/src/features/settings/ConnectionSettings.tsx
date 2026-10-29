import {
  KeyRound,
  Plus,
  Save,
  Server,
  SlidersHorizontal,
  Trash2,
  X,
} from "lucide-react";
import type { TFunction } from "i18next";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  CatalogModel,
  ConfigScope,
  ConnectionInfo,
  ManualModelEntry,
  ProviderTestResult,
} from "../../generated/app-server";
import { Dialog } from "../../components/Dialog";
import { confirmAction } from "../../platform/confirmAction";
import type { ConnectionCatalogController } from "./useConnectionCatalog";
import { ConnectionVerification } from "./ConnectionVerification";
import { ModelListEditor } from "./ModelListEditor";
import { type Draft, emptyDraft, connectionMutation } from "./connectionDraft";
import { ProtocolSettings } from "./ProtocolSettings";
import { ConnectionProbe } from "./ConnectionProbe";
import { ProviderLogin } from "./ProviderLogin";
import styles from "./ConnectionSettings.module.css";

interface ConnectionSettingsProps {
  controller: ConnectionCatalogController;
  busy: boolean;
  /** The dialog's write scope, so this section can say plainly that it
   * does not follow it: connections and credentials are user-scoped by
   * design, and the service refuses to write them into a project. */
  scope: ConfigScope;
}


export function ConnectionSettings({
  controller,
  busy,
  scope,
}: ConnectionSettingsProps) {
  const { t } = useTranslation();
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
      protocol: connection.protocol ?? "auto",
      auth: connection.auth ?? "api_key",
      compat: connection.compat ?? {},
      apiBase: connection.apiBase ?? "",
      apiKeyEnv: connection.apiKeyEnv ?? "",
      apiKey: "",
      clearApiKey: false,
      modelCatalog: connection.modelCatalog,
      manualModels: (connection.manualModelEntries ?? []).map((entry) => ({
        ...entry,
      })),
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
      protocol: "auto",
      auth: template.local ? "none" : "api_key",
      compat: {},
      apiKey: "",
      apiKeyEnv: "",
      clearApiKey: false,
      environmentShadows: false,
      shadowingEnvName: "",
      modelCatalog: "auto",
      manualModels: [],
    }));
  };

  const save = async () => {
    if (!editing?.id.trim()) return;
    setSaving(true);
    try {
      const connectionId = editing.id.trim().toLocaleLowerCase();
      const connection = connectionMutation(editing);
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
        (editing?.manualModels ?? [])
          .map((entry) => entry.id.trim())
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
      // Probe THE FORM AS SHOWN (dsh's rule): an unsaved base URL or a key
      // typed but not yet stored takes part; nothing is written.
      const result = await controller.discover({ connection: connectionMutation(editing) });
      setModelFetchState({
        editorId: editingId,
        loading: false,
        error:
          result.models.length === 0
            ? (result.error ??
              t(
                "settings.connections.noModelsListed",
                "The provider listed no models. Add them by hand.",
              ))
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
    const discovered = new Map(
      (modelFetch.models ?? []).map((model) => [model.id, model]),
    );
    const added = [...pickedModels]
      .filter((id) => !manualModelIds.has(id))
      .map((id): ManualModelEntry => {
        const model = discovered.get(id);
        // Candidates adopt WITH what discovery learned (display name and
        // capacities), so the declaration is born accurate; already-listed
        // rows are untouched so a hand-corrected capacity never regresses.
        return {
          id,
          ...(model && model.name && model.name !== id
            ? { label: model.name }
            : {}),
          ...(model?.contextWindow ? { contextWindow: model.contextWindow } : {}),
          ...(model?.maxOutputTokens
            ? { maxOutputTokens: model.maxOutputTokens }
            : {}),
        };
      });
    setEditing({
      ...editing,
      // A hand-picked list REPLACES the provider catalog for this
      // connection (the dsh rule), so adoption switches to manual mode.
      modelCatalog: "manual",
      manualModels: [...editing.manualModels, ...added],
    });
    setPickedModels(new Set());
  };

  const remove = async (connection: ConnectionInfo) => {
    if (
      !(await confirmAction(
        t(
          "settings.connections.removeConfirm",
          "Remove “{{label}}”? Its saved API key will also be deleted. Existing Session history is kept.",
          { label: connection.label },
        ),
        {
          title: t("settings.connections.removeTitle", "Remove LLM connection"),
          confirmLabel: t(
            "settings.connections.removeConnection",
            "Remove connection",
          ),
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
          <p>{t("settings.connections.step", "Step 1 · Connect a provider")}</p>
          <h2 id="connections-title">
            {t("settings.connections.title", "AI providers")}
          </h2>
          <span>
            {t(
              "settings.connections.description",
              "Add the service that supplies your models. DeepCode keeps API keys in user-private storage and shares the connection with CLI and Desktop.",
            )}
          </span>
          {scope === "project" ? (
            <span className={styles.scopeNote} role="note">
              {t(
                "settings.connections.userScoped",
                "Connections and credentials are always user-scoped: they are shared with the CLI and every project, so this section ignores the “Selected project” write scope.",
              )}
            </span>
          ) : null}
        </div>
        <div className={styles.addActions}>
          <label className={styles.addSelect}>
            <select
              aria-label={t("settings.connections.addProvider", "Add provider")}
              value=""
              disabled={busy || saving}
              onChange={(event) => {
                if (!event.target.value) return;
                setEditing({ ...emptyDraft });
                chooseTemplate(event.target.value);
                event.target.value = "";
              }}
            >
              <option value="">
                {t("settings.connections.addProviderOption", "Add provider…")}
              </option>
              {controller.catalog?.templates
                .filter((template) => template.name !== "custom")
                .map((template) => (
                  <option key={template.name} value={template.name}>
                    {template.label} ·{" "}
                    {template.local
                      ? t("settings.connections.local", "local")
                      : t("settings.connections.cloud", "cloud")}
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
            title={t(
              "settings.connections.customTooltip",
              "Declare an OpenAI-compatible or Anthropic endpoint DeepCode does not ship",
            )}
          >
            <Plus size={14} />
            {t("settings.connections.addCustom", "Add a custom provider")}
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
                    {connectionStatus(connection, result, t)}
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
                          ? t(
                              "settings.connections.credentialConfigured",
                              "Credential configured",
                            )
                          : t(
                              "settings.connections.credentialMissing",
                              "Credential missing",
                            )
                      }
                    />
                    {credentialLabel(connection, t)} ·{" "}
                    {modelFaceLabel(connection, result, t)}
                  </small>
                </p>
                {connection.auth === "oauth" ? <ProviderLogin connection={connection} controller={controller} /> : null}
                {result ? (
                  <ConnectionVerification result={result} compact />
                ) : testingId === connection.id ? (
                  <p className={styles.loading}>
                    {t("settings.connections.checking", "Checking…")}
                  </p>
                ) : null}
              </div>
              <div className={styles.actions}>
                <button
                  type="button"
                  onClick={() => beginEdit(connection)}
                  disabled={busy}
                >
                  {t("settings.connections.edit", "Edit")}
                </button>
                {connection.explicit ? (
                  <button
                    type="button"
                    className={styles.removeButton}
                    onClick={() => void remove(connection)}
                    disabled={busy}
                    title={t(
                      "settings.connections.removeConnection",
                      "Remove connection",
                    )}
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
            <strong>
              {t("settings.connections.emptyTitle", "No provider connected")}
            </strong>
            <span>
              {t(
                "settings.connections.emptyBody",
                "Pick a provider from “Add provider…” above, or declare a custom endpoint, to begin.",
              )}
            </span>
          </div>
        ) : null}
        {controller.loading ? (
          <p className={styles.loading}>
            {t("settings.connections.loading", "Loading connections…")}
          </p>
        ) : null}
      </div>

      {editing ? (
        // While the provider editor is open it owns Escape — closing the
        // form, not the settings dialog behind it. The draft can hold an API
        // key, so the backdrop never dismisses it.
        <Dialog
          as="section"
          className={styles.editor}
          backdropClassName={styles.editorBackdrop}
          labelledBy="connection-editor-title"
          busy={saving}
          onClose={() => setEditing(null)}
        >
          <header>
            <div>
              <KeyRound size={16} />
              <span>
                <strong id="connection-editor-title">
                  {editingExisting
                    ? t("settings.connections.editProvider", "Edit provider")
                    : t("settings.connections.connectProvider", "Connect a provider")}
                </strong>
                <small>
                  {t(
                    "settings.connections.oneSetup",
                    "One setup works in CLI and Desktop",
                  )}
                </small>
              </span>
            </div>
            <button
              type="button"
              onClick={() => setEditing(null)}
              aria-label={t(
                "settings.connections.closeEditor",
                "Close connection editor",
              )}
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
                      ? t("settings.connections.localService", "Local model service")
                      : t("settings.connections.apiProvider", "API provider")}
                  </small>
                </span>
              </div>
              <label className={styles.wide}>
                {t("settings.connections.displayName", "Display name")}
                <input
                  value={editing.label}
                  onChange={(event) =>
                    setEditing({ ...editing, label: event.target.value })
                  }
                  placeholder={
                    selectedTemplate?.label ??
                    t("settings.connections.myProvider", "My provider")
                  }
                />
              </label>
              {endpointRequired ? (
                <label className={styles.wide}>
                  {t("settings.connections.endpoint", "API endpoint")}
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
                    {t(
                      "settings.connections.endpointNote",
                      "This provider has no universal endpoint. Enter the base URL of your server.",
                    )}
                  </small>
                </label>
              ) : null}
              <ProtocolSettings draft={editing} onChange={setEditing} />
              {editing.auth === "api_key" ? (
                <fieldset className={`${styles.credentials} ${styles.wide}`}>
                  <legend>
                    {t("settings.connections.credential", "Credential")}
                  </legend>
                  {editing.environmentShadows ? (
                    <p className={styles.credentialShadowNote} role="note">
                      {editing.shadowingEnvName
                        ? t(
                            "settings.connections.shadowedBy",
                            "The launch environment variable {{name}} currently provides this key and takes precedence. ",
                            { name: editing.shadowingEnvName },
                          )
                        : t(
                            "settings.connections.shadowed",
                            "A launch environment variable currently provides this key and takes precedence. ",
                          )}
                      {t(
                        "settings.connections.unsetToManage",
                        "Unset it in the launching shell to manage the key here.",
                      )}
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
                        ? t(
                            "settings.connections.keyFromEnvironment",
                            "Provided by the launch environment (read-only)",
                          )
                        : editingExisting
                          ? t(
                              "settings.connections.keepSavedKey",
                              "Leave blank to keep the saved key",
                            )
                          : t("settings.connections.pasteKey", "Paste API key")
                    }
                    autoComplete="new-password"
                    disabled={
                      editing.clearApiKey || editing.environmentShadows
                    }
                    aria-label={t("settings.connections.apiKey", "API key")}
                  />
                  <small>
                    {t(
                      "settings.connections.writeOnly",
                      "Write-only: stored keys are private and never returned to the app UI. Referencing an environment variable instead is available under Advanced.",
                    )}
                  </small>
                </fieldset>
              ) : null}
              {editing.template ? (
                <fieldset className={`${styles.modelsField} ${styles.wide}`}>
                  <legend>{t("settings.connections.models", "Models")}</legend>
                  <div className={styles.modelFetchRow}>
                    <button
                      type="button"
                      onClick={() => void fetchModels()}
                      disabled={modelFetch.loading || busy}
                    >
                      {modelFetch.loading
                        ? t("settings.connections.fetching", "Fetching…")
                        : t(
                            "settings.connections.fetchModels",
                            "Fetch models from provider",
                          )}
                    </button>
                    <small>
                      {t(
                        "settings.connections.fetchNote",
                        "Probes the endpoint as filled in above — an unsaved URL or key works; picks become the manual model list.",
                      )}
                    </small>
                  </div>
                  {modelFetch.error ? (
                    <p className={styles.modelFetchError} role="alert">
                      {modelFetch.error}
                    </p>
                  ) : null}
                  {modelFetch.models && modelFetch.models.length > 0 ? (
                    <>
                      <ul
                        className={styles.modelPicker}
                        aria-label={t(
                          "settings.connections.discovered",
                          "Discovered models",
                        )}
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
                                      ? t(
                                          "settings.connections.contextWindow",
                                          " · {{size}}K context",
                                          {
                                            size: Math.round(
                                              model.contextWindow / 1000,
                                            ),
                                          },
                                        )
                                      : ""}
                                    {listed
                                      ? t(
                                          "settings.connections.alreadyListed",
                                          " · already listed",
                                        )
                                      : ""}
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
                        {pickedModels.size === 0
                          ? t(
                              "settings.connections.addSelected",
                              "Add selected models to this connection",
                            )
                          : pickedModels.size === 1
                            ? t(
                                "settings.connections.addOne",
                                "Add 1 model to this connection",
                              )
                            : t(
                                "settings.connections.addMany",
                                "Add {{total}} models to this connection",
                                { total: pickedModels.size },
                              )}
                      </button>
                    </>
                  ) : null}
                  <ModelListEditor
                    protocol={editing.protocol}
                    entries={editing.manualModels}
                    onChange={(manualModels) =>
                      setEditing({ ...editing, manualModels })
                    }
                  />
                  <small>
                    {t(
                      "settings.connections.reasoningNote",
                      "Per-model reasoning declarations (`reasoningEfforts`) live in the configuration file — Open configuration file edits them directly.",
                    )}
                  </small>
                </fieldset>
              ) : null}
              <ConnectionProbe key={editing.id} connection={connectionMutation(editing)} controller={controller} />
              <details className={`${styles.advanced} ${styles.wide}`}>
                <summary>
                  <SlidersHorizontal size={14} />{" "}
                  {t("settings.connections.advanced", "Advanced connection settings")}
                </summary>
                <div>
                  <label>
                    {t("settings.connections.connectionId", "Connection ID")}
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
                      {t(
                        "settings.connections.apiKeyEnv",
                        "API key environment variable",
                      )}
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
                        {t(
                          "settings.connections.apiKeyEnvNote",
                          "Reference a launch-environment variable instead of a stored key. Leave blank to use the pasted key.",
                        )}
                      </small>
                    </label>
                  ) : null}
                  {!endpointRequired ? (
                    <label>
                      {t("settings.connections.apiBase", "API base")}
                      <input
                        value={editing.apiBase}
                        onChange={(event) =>
                          setEditing({
                            ...editing,
                            apiBase: event.target.value,
                          })
                        }
                        placeholder={t(
                          "settings.connections.providerDefault",
                          "Use provider default",
                        )}
                      />
                    </label>
                  ) : null}
                  <label>
                    {t("settings.connections.adapter", "Adapter")}
                    <select
                      value={editing.adapter}
                      onChange={(event) =>
                        setEditing({
                          ...editing,
                          adapter: event.target.value as Draft["adapter"],
                        })
                      }
                    >
                      <option value="openai_compat">
                        {t("settings.connections.openaiCompatible", "OpenAI compatible")}
                      </option>
                      <option value="anthropic">
                        {t("settings.connections.anthropicNative", "Anthropic native")}
                      </option>
                    </select>
                  </label>
                  <label>
                    {t("settings.connections.modelCatalog", "Model catalog")}
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
                      <option value="auto">
                        {t("settings.connections.catalogAuto", "Automatic")}
                      </option>
                      <option value="openrouter">OpenRouter</option>
                      <option value="openai">
                        {t("settings.connections.openaiCompatible", "OpenAI compatible")}
                      </option>
                      <option value="anthropic">Anthropic</option>
                      <option value="manual">
                        {t("settings.connections.catalogManual", "Manual list")}
                      </option>
                    </select>
                  </label>

                  {editingExisting ? (
                    <label
                      className={`${styles.credentialAction} ${styles.wide}`}
                    >
                      <input
                        type="checkbox"
                        checked={editing.clearApiKey}
                        aria-label={t(
                          "settings.connections.removeSavedKey",
                          "Remove saved API key",
                        )}
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
                        <strong>
                          {t(
                            "settings.connections.removeSavedKeyTitle",
                            "Remove the saved API key",
                          )}
                        </strong>
                        <small>
                          {t(
                            "settings.connections.removeSavedKeyNote",
                            "Environment and legacy credentials are unchanged.",
                          )}
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
              {t(
                "settings.connections.saveNote",
                "Saving checks credentials and model discovery. It does not send project content.",
              )}
            </span>
            <button
              type="button"
              onClick={() => setEditing(null)}
              disabled={saving}
            >
              {t("settings.connections.cancel", "Cancel")}
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
              {saving
                ? t("settings.connections.saving", "Saving…")
                : t("settings.connections.saveCheck", "Save and check")}
            </button>
          </footer>
        </Dialog>
      ) : null}
    </section>
  );
}


function modelFaceLabel(
  connection: ConnectionInfo,
  result: ProviderTestResult | undefined,
  t: TFunction,
): string {
  if (connection.manualModels.length > 0) {
    return connection.manualModels.length === 1
      ? t("settings.connections.manualOne", "manual · 1 model")
      : t("settings.connections.manualMany", "manual · {{total}} models", {
          total: connection.manualModels.length,
        });
  }
  if (result?.modelCount) {
    return t("settings.connections.catalogCount", "catalog · {{total}} models", {
      total: result.modelCount,
    });
  }
  return connection.modelCatalog === "auto"
    ? t("settings.connections.providerCatalog", "provider catalog")
    : t("settings.connections.namedCatalog", "{{name}} catalog", {
        name: connection.modelCatalog,
      });
}

function credentialLabel(connection: ConnectionInfo, t: TFunction): string {
  switch (connection.credentialSource) {
    case "environment":
      return connection.apiKeyEnv
        ? t("settings.credential.environmentNamed", "environment · {{name}}", {
            name: connection.apiKeyEnv,
          })
        : t("settings.credential.environment", "environment");
    case "credential_store":
      return t("settings.credential.store", "credential store");
    case "legacy_config":
      return t("settings.credential.legacy", "legacy config");
    case "not_required":
      return t("settings.credential.notRequired", "no key required");
    case "oauth":
      return t("settings.credential.oauth", "signed-in account");
    default:
      return t("settings.credential.none", "no key");
  }
}

function connectionStatus(
  connection: ConnectionInfo,
  result: ProviderTestResult | undefined,
  t: TFunction,
): string {
  if (result?.status === "ready")
    return t("settings.connectionStatus.ready", "Model verified");
  if (result?.status === "connected")
    return t("settings.connectionStatus.connected", "Catalog connected");
  if (result?.status === "limited")
    return t("settings.connectionStatus.limited", "Model check needed");
  if (result?.status === "error")
    return t("settings.connectionStatus.error", "Needs attention");
  if (connection.auth === "none")
    return t("settings.connectionStatus.noAuth", "No authentication required");
  if (connection.auth === "oauth")
    return connection.configured
      ? t("settings.connectionStatus.signedIn", "Signed in")
      : t("settings.connectionStatus.signInRequired", "Sign-in required");
  return connection.configured
    ? t("settings.connectionStatus.saved", "Credential saved")
    : t("settings.connectionStatus.needsCredential", "Needs credential");
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
