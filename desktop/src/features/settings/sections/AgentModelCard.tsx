/**
 * Default agent model + advanced phase routing — the config-file half of the
 * Models section. Extracted from the old SettingsPage unchanged in behavior:
 * writes `agents.defaults/planning/implementation` through `settings/update`
 * and verifies through `provider/test`.
 */

import { FlaskConical } from "lucide-react";
import { useEffect, useId, useState } from "react";

import type {
  ConfigScope,
  JsonObject,
  ModelCatalogResult,
  ProviderTestResult,
  SettingsSnapshot,
} from "../../../generated/app-server";
import { ConnectionVerification } from "../ConnectionVerification";
import type { ConnectionCatalogController } from "../useConnectionCatalog";
import styles from "../../management/ManagementWorkspace.module.css";

interface AgentDraft {
  defaultConnection: string;
  defaultModel: string;
  defaultReasoningEffort: string;
  planningConnection: string;
  planningModel: string;
  implementationConnection: string;
  implementationModel: string;
  maxTokens: string;
}

interface AgentModelCardProps {
  settings: SettingsSnapshot | null;
  busy: boolean;
  scope: ConfigScope;
  connections: ConnectionCatalogController;
  onUpdate(patch: JsonObject, scope: ConfigScope): Promise<void>;
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
    defaultReasoningEffort: text(defaults.reasoningEffort),
    planningConnection: text(planning.connection),
    planningModel: text(planning.model),
    implementationConnection: text(implementation.connection),
    implementationModel: text(implementation.model),
    maxTokens: numberText(defaults.maxTokens, 8192),
  };
}

export function AgentModelCard({
  settings,
  busy,
  scope,
  connections,
  onUpdate,
}: AgentModelCardProps) {
  const [agentOverrides, setAgentOverrides] = useState<Partial<AgentDraft>>({});
  const [verifyingModel, setVerifyingModel] = useState(false);
  const [modelVerification, setModelVerification] =
    useState<ProviderTestResult | null>(null);

  const agents = { ...agentDraft(settings), ...agentOverrides };
  const maxTokens = Number(agents.maxTokens);
  const maxTokensValid =
    /^\d+$/.test(agents.maxTokens.trim()) &&
    Number.isSafeInteger(maxTokens) &&
    maxTokens > 0;

  const models = settings?.models ?? [];
  const connectionOptions = connections.catalog?.connections ?? [];
  const selectableConnections = connectionOptions.filter(
    (connection) =>
      connection.enabled &&
      (connection.id === agents.defaultConnection ||
        (connection.configured && (!connection.local || connection.explicit))),
  );
  const verificationConnection = resolveConnectionForModel(
    connectionOptions,
    agents.defaultConnection,
    agents.defaultModel,
  );

  const updateAgents = (patch: Partial<AgentDraft>) => {
    setAgentOverrides((current) => ({ ...current, ...patch }));
    setModelVerification(null);
  };

  const saveAgents = async (): Promise<boolean> => {
    if (!maxTokensValid) return false;
    await onUpdate(
      {
        agents: {
          defaults: {
            connection: agents.defaultConnection || null,
            provider: "auto",
            model: agents.defaultModel,
            reasoningEffort: agents.defaultReasoningEffort || null,
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
      scope,
    );
    setAgentOverrides({});
    return true;
  };

  const verifyDefaultModel = async () => {
    if (!verificationConnection || !agents.defaultModel.trim()) return;
    setVerifyingModel(true);
    setModelVerification(null);
    try {
      if (!(await saveAgents())) return;
      setModelVerification(
        await connections.test(
          verificationConnection.id,
          agents.defaultModel.trim(),
        ),
      );
    } catch {
      // The shared connection controller exposes the sanitized product error.
    } finally {
      setVerifyingModel(false);
    }
  };

  return (
    <section className={`${styles.formCard} ${styles.fullWidthCard}`}>
      <header>
        <div>
          <p className={styles.eyebrow}>Default route</p>
          <h2>Agent model</h2>
          <p className={styles.cardDescription}>
            New Sessions inherit this model. A Session can switch models later
            without losing its conversation history.
          </p>
        </div>
      </header>
      {!selectableConnections.length ? (
        <p className={styles.warningBlock}>
          Connect a provider above before choosing an Agent model.
        </p>
      ) : null}
      <div className={styles.formGrid}>
        <label>
          Provider connection
          <select
            value={agents.defaultConnection}
            onChange={(event) =>
              updateAgents({
                defaultConnection: event.target.value,
                defaultModel: "",
              })
            }
          >
            <option value="">Choose automatically</option>
            {selectableConnections.map((connection) => (
              <option value={connection.id} key={connection.id}>
                {connection.label}
              </option>
            ))}
          </select>
        </label>
        <ModelField
          label="Model"
          connectionId={verificationConnection?.id ?? ""}
          value={agents.defaultModel}
          fallbackModels={models.map((model) => model.id)}
          listModels={connections.models}
          onChange={(defaultModel) =>
            updateAgents({ defaultModel, defaultReasoningEffort: "" })
          }
        />
        <EffortField
          connectionId={verificationConnection?.id ?? ""}
          modelId={agents.defaultModel}
          value={agents.defaultReasoningEffort}
          listModels={connections.models}
          onChange={(defaultReasoningEffort) =>
            updateAgents({ defaultReasoningEffort })
          }
        />
        <label>
          Max output tokens
          <input
            inputMode="numeric"
            value={agents.maxTokens}
            onChange={(event) => updateAgents({ maxTokens: event.target.value })}
            aria-invalid={!maxTokensValid}
          />
        </label>
      </div>
      <details className={styles.advancedSettings}>
        <summary>Advanced phase routing</summary>
        <p>
          Optional phase models inherit the Agent model when left empty.
          Paper2Code uses Planning first, then Coding & implementation.
          Ordinary Code Sessions without a Session override use Coding &
          implementation.
        </p>
        <div className={styles.formGrid}>
          <label>
            Planning connection
            <select
              value={agents.planningConnection}
              onChange={(event) =>
                updateAgents({
                  planningConnection: event.target.value,
                  planningModel: "",
                })
              }
            >
              <option value="">Use Agent default</option>
              {selectableConnections.map((connection) => (
                <option value={connection.id} key={connection.id}>
                  {connection.label}
                </option>
              ))}
            </select>
          </label>
          <ModelField
            label="Planning model"
            connectionId={
              agents.planningConnection || verificationConnection?.id || ""
            }
            value={agents.planningModel}
            fallbackModels={models.map((model) => model.id)}
            listModels={connections.models}
            allowEmpty
            onChange={(planningModel) => updateAgents({ planningModel })}
          />
          <label>
            Coding & implementation connection
            <select
              value={agents.implementationConnection}
              onChange={(event) =>
                updateAgents({
                  implementationConnection: event.target.value,
                  implementationModel: "",
                })
              }
            >
              <option value="">Use Agent default</option>
              {selectableConnections.map((connection) => (
                <option value={connection.id} key={connection.id}>
                  {connection.label}
                </option>
              ))}
            </select>
          </label>
          <ModelField
            label="Coding & implementation model"
            connectionId={
              agents.implementationConnection ||
              verificationConnection?.id ||
              ""
            }
            value={agents.implementationModel}
            fallbackModels={models.map((model) => model.id)}
            listModels={connections.models}
            allowEmpty
            onChange={(implementationModel) =>
              updateAgents({ implementationModel })
            }
          />
        </div>
      </details>
      {modelVerification ? (
        <div className={styles.verificationBlock}>
          <ConnectionVerification result={modelVerification} />
        </div>
      ) : null}
      <footer className={styles.formActions}>
        <span>
          Verification sends only a tiny “reply OK” request. No repository or
          Session content is included.
        </span>
        <button
          className={styles.secondaryButton}
          type="button"
          disabled={busy || !agents.defaultModel || !maxTokensValid}
          onClick={() => void saveAgents()}
        >
          Save defaults
        </button>
        <button
          className={styles.primaryButton}
          type="button"
          disabled={
            busy ||
            verifyingModel ||
            !verificationConnection ||
            !agents.defaultModel ||
            !maxTokensValid
          }
          onClick={() => void verifyDefaultModel()}
        >
          <FlaskConical size={14} />
          {verifyingModel ? "Verifying…" : "Save and verify model"}
        </button>
      </footer>
    </section>
  );
}

interface ModelCatalogState {
  catalog: ModelCatalogResult | null;
  loading: boolean;
  failed: boolean;
}

/** Load one connection's model catalog, keyed so a late response for a
 * previously selected connection can never bleed into the current one. */
function useModelCatalog(
  connectionId: string,
  listModels: ConnectionCatalogController["models"],
): ModelCatalogState {
  const [state, setState] = useState<{
    connectionId: string;
    catalog: ModelCatalogResult | null;
    failed: boolean;
  } | null>(null);

  useEffect(() => {
    if (!connectionId) return;
    let cancelled = false;
    void listModels(connectionId)
      .then((result) => {
        if (!cancelled) {
          setState({ connectionId, catalog: result, failed: false });
        }
      })
      .catch(() => {
        if (!cancelled) {
          setState({ connectionId, catalog: null, failed: true });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [connectionId, listModels]);

  return {
    catalog: state?.connectionId === connectionId ? state.catalog : null,
    loading: Boolean(connectionId && state?.connectionId !== connectionId),
    failed: state?.connectionId === connectionId ? state.failed : false,
  };
}

/**
 * Reasoning effort for the default route — the dsh pairing: effort belongs
 * to the MODEL, so the options are that model's published levels plus the
 * universal auto/off, and a model without published controls offers no
 * invented ladder.
 */
function EffortField({
  connectionId,
  modelId,
  value,
  listModels,
  onChange,
}: {
  connectionId: string;
  modelId: string;
  value: string;
  listModels: ConnectionCatalogController["models"];
  onChange(value: string): void;
}) {
  const { catalog } = useModelCatalog(connectionId, listModels);
  const model = catalog?.models.find(
    (candidate) => candidate.id === modelId.trim(),
  );
  const reasoning = model?.reasoning ?? null;
  const efforts = reasoning?.supportedEfforts ?? [];
  const options = [
    { value: "", label: "Inherit provider default" },
    { value: "auto", label: "auto · model decides" },
    ...(reasoning?.mandatory ? [] : [{ value: "none", label: "none · off" }]),
    ...efforts.map((effort) => ({ value: effort, label: effort })),
  ];
  // A stored value the catalog no longer advertises stays selectable so the
  // row shows the truth instead of silently blanking (advisory catalogs).
  if (value && !options.some((option) => option.value === value)) {
    options.push({ value, label: `${value} · not advertised` });
  }
  return (
    <label>
      Reasoning effort
      <select
        aria-label="Reasoning effort"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <small>
        {model
          ? efforts.length
            ? `Published levels: ${efforts.join(", ")}`
            : "This model publishes no named effort levels."
          : "Pick a cataloged model to see its published levels."}
      </small>
    </label>
  );
}

function ModelField({
  label,
  connectionId,
  value,
  fallbackModels,
  listModels,
  allowEmpty = false,
  onChange,
}: {
  label: string;
  connectionId: string;
  value: string;
  fallbackModels: string[];
  listModels: ConnectionCatalogController["models"];
  allowEmpty?: boolean;
  onChange(value: string): void;
}) {
  const listId = useId();
  const { catalog, loading, failed } = useModelCatalog(
    connectionId,
    listModels,
  );
  const models =
    catalog?.connectionId === connectionId
      ? catalog.models.map((model) => model.id)
      : fallbackModels;
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
      <small>
        {loading
          ? "Loading models…"
          : failed
            ? "Catalog unavailable · enter an exact model ID"
            : catalog?.stale
              ? "Using the last available model list"
              : connectionId
                ? `${models.length} models available · exact IDs are also accepted`
                : "Choose a connection to load its models"}
      </small>
    </label>
  );
}

function resolveConnectionForModel(
  connections: NonNullable<
    ConnectionCatalogController["catalog"]
  >["connections"],
  selectedId: string,
  model: string,
) {
  if (selectedId) {
    const selected = connections.find(
      (connection) => connection.id === selectedId && connection.enabled,
    );
    if (selected) return selected;
  }
  const prefix = model.split("/", 1)[0]?.toLocaleLowerCase();
  return (
    connections.find(
      (connection) =>
        connection.enabled &&
        connection.configured &&
        (connection.id === prefix || connection.providerName === prefix),
    ) ??
    connections.find(
      (connection) => connection.enabled && connection.configured,
    ) ??
    null
  );
}
