import {
  CheckCircle2,
  CircleAlert,
  FlaskConical,
  KeyRound,
  Plus,
  Save,
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
  clearApiKey: boolean;
  modelCatalog: "auto" | "openrouter" | "openai" | "anthropic" | "manual";
  manualModels: string;
}

const emptyDraft: Draft = {
  id: "",
  label: "",
  template: "openrouter",
  adapter: "openai_compat",
  apiBase: "",
  apiKeyEnv: "",
  apiKey: "",
  clearApiKey: false,
  modelCatalog: "auto",
  manualModels: "",
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
    });
  };

  const save = async () => {
    if (!editing?.id.trim()) return;
    setSaving(true);
    try {
      const connection: ProviderUpsertParams["connection"] = {
        id: editing.id.trim().toLocaleLowerCase(),
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
      if (editing.apiKey.trim()) connection.apiKey = editing.apiKey.trim();
      if (editing.clearApiKey) connection.clearApiKey = true;
      await controller.upsert(connection);
      setEditing(null);
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
          <p>LLM access</p>
          <h2 id="connections-title">Connections</h2>
          <span>
            One connection can serve many models. Credentials stay in the
            user-only credential store and are never read back.
          </span>
        </div>
        <button
          type="button"
          className={styles.addButton}
          onClick={() => setEditing({ ...emptyDraft })}
          disabled={busy || saving}
        >
          <Plus size={14} />
          Add connection
        </button>
      </header>

      {controller.error ? (
        <p className={styles.error}>{controller.error}</p>
      ) : null}

      <div className={styles.connectionRail}>
        {connections.map((connection) => {
          const result = testResults[connection.id];
          return (
            <article key={connection.id} className={styles.connection}>
              <span
                className={styles.statusLight}
                data-ready={connection.configured}
                aria-hidden="true"
              />
              <div className={styles.connectionBody}>
                <header>
                  <div>
                    <strong>{connection.label}</strong>
                    <code>{connection.id}</code>
                  </div>
                  <span data-ready={connection.configured}>
                    {connection.configured ? "Ready" : "Needs credential"}
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
                  <div className={styles.testResult} data-ok={result.ok}>
                    {result.ok ? (
                      <CheckCircle2 size={13} />
                    ) : (
                      <CircleAlert size={13} />
                    )}
                    {result.ok
                      ? `${result.modelCount} models · ${result.latencyMs} ms`
                      : result.error}
                  </div>
                ) : null}
              </div>
              <div className={styles.actions}>
                <button
                  type="button"
                  onClick={() => void test(connection.id)}
                  disabled={busy || testingId === connection.id}
                  title="Test connection"
                >
                  <FlaskConical size={13} />
                  {testingId === connection.id ? "Testing…" : "Test"}
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
                    <Trash2 size={13} />
                  </button>
                ) : null}
              </div>
            </article>
          );
        })}
        {controller.loading ? <p className={styles.loading}>Loading connections…</p> : null}
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
                    {connections.some((item) => item.id === editing.id)
                      ? "Edit connection"
                      : "New connection"}
                  </strong>
                  <small>Saved for CLI and Desktop</small>
                </span>
              </div>
              <button
                type="button"
                onClick={() => setEditing(null)}
                aria-label="Close connection editor"
              >
                <X size={15} />
              </button>
            </header>
            <div className={styles.form}>
              {controller.error ? (
                <p className={`${styles.error} ${styles.wide}`}>
                  {controller.error}
                </p>
              ) : null}
              <label>
                Connection ID
                <input
                  value={editing.id}
                  onChange={(event) =>
                    setEditing({ ...editing, id: event.target.value })
                  }
                  placeholder="openrouter-personal"
                  disabled={connections.some((item) => item.id === editing.id)}
                />
              </label>
              <label>
                Display name
                <input
                  value={editing.label}
                  onChange={(event) =>
                    setEditing({ ...editing, label: event.target.value })
                  }
                  placeholder="OpenRouter · Personal"
                />
              </label>
              <label>
                Provider template
                <select
                  value={editing.template}
                  onChange={(event) => {
                    const template = event.target.value;
                    const selected = controller.catalog?.templates.find(
                      (candidate) => candidate.name === template,
                    );
                    setEditing({
                      ...editing,
                      template,
                      adapter:
                        selected?.adapter === "anthropic"
                          ? "anthropic"
                          : "openai_compat",
                      apiBase: editing.apiBase || selected?.defaultApiBase || "",
                    });
                  }}
                >
                  {controller.catalog?.templates.map((template) => (
                    <option key={template.name} value={template.name}>
                      {template.label}
                    </option>
                  ))}
                </select>
              </label>
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
              <label className={styles.wide}>
                API base
                <input
                  value={editing.apiBase}
                  onChange={(event) =>
                    setEditing({ ...editing, apiBase: event.target.value })
                  }
                  placeholder="Use provider default"
                />
              </label>
              <label>
                API key
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
                  placeholder="Leave blank to keep existing"
                  autoComplete="new-password"
                  disabled={editing.clearApiKey}
                />
              </label>
              <label>
                Or environment variable
                <input
                  value={editing.apiKeyEnv}
                  onChange={(event) =>
                    setEditing({ ...editing, apiKeyEnv: event.target.value })
                  }
                  placeholder="OPENROUTER_API_KEY"
                />
              </label>
              <label className={`${styles.credentialAction} ${styles.wide}`}>
                <input
                  type="checkbox"
                  checked={editing.clearApiKey}
                  aria-label="Remove saved API key"
                  onChange={(event) =>
                    setEditing({
                      ...editing,
                      apiKey: event.target.checked ? "" : editing.apiKey,
                      clearApiKey: event.target.checked,
                    })
                  }
                />
                <span>
                  <strong>Remove the saved API key</strong>
                  <small>
                    Environment variables and legacy configuration are not
                    changed.
                  </small>
                </span>
              </label>
              <label>
                Model catalog
                <select
                  value={editing.modelCatalog}
                  onChange={(event) =>
                    setEditing({
                      ...editing,
                      modelCatalog: event.target.value as Draft["modelCatalog"],
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
              <label className={styles.wide}>
                Manual models
                <textarea
                  value={editing.manualModels}
                  onChange={(event) =>
                    setEditing({ ...editing, manualModels: event.target.value })
                  }
                  placeholder={"One model ID per line\nmoonshotai/kimi-k2.5"}
                  rows={3}
                />
              </label>
            </div>
            <footer>
              <span>Changing a connection affects future Turns only.</span>
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
                disabled={saving || !editing.id.trim()}
              >
                <Save size={13} />
                {saving ? "Saving…" : "Save connection"}
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
