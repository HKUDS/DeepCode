import {
  Check,
  ChevronDown,
  Cpu,
  RefreshCw,
  Search,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  ConnectionInfo,
  CatalogModel,
  ModelCatalogResult,
  Project,
  SettingsSnapshot,
  Thread,
} from "../../generated/app-server";
import { Dialog } from "../../components/Dialog";
import type { ClientRuntime } from "../../rpc/contracts";
import { useConnectionCatalog } from "../settings/useConnectionCatalog";
import { effortLabel } from "./effortLabel";
import styles from "./ModelPicker.module.css";

interface ModelPickerProps {
  runtime: ClientRuntime;
  project: Project | null;
  thread: Thread | null;
  settings: SettingsSnapshot | null;
  disabled: boolean;
  onChange(
    connectionId: string | null,
    model: string | null,
    reasoningEffort: string | null,
    contextWindow: number | null,
  ): void;
}

export function ModelPicker({
  runtime,
  project,
  thread,
  settings,
  disabled,
  onChange,
}: ModelPickerProps) {
  const { t } = useTranslation();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const {
    catalog: connectionCatalog,
    models: listModels,
  } = useConnectionCatalog(runtime, project?.id ?? null);
  const [open, setOpen] = useState(false);
  const [connectionId, setConnectionId] = useState("");
  const [modelId, setModelId] = useState("");
  const [reasoningEffort, setReasoningEffort] = useState("auto");
  const [contextWindow, setContextWindow] = useState<number | null>(null);
  const [catalog, setCatalog] = useState<ModelCatalogResult | null>(null);
  const [refreshingModels, setRefreshingModels] = useState(false);
  const [modelFailure, setModelFailure] = useState<{
    connectionId: string;
    message: string;
  } | null>(null);
  const [query, setQuery] = useState("");
  const [manualModel, setManualModel] = useState("");
  const defaults = agentDefaults(settings);
  const effectiveModel = thread?.model ?? defaults.model;
  const effectiveConnection = resolveConnection(
    connectionCatalog?.connections ?? [],
    thread?.connectionId ?? defaults.connection,
    effectiveModel,
  );
  const selectedConnectionId = connectionId || effectiveConnection?.id || "";
  const selectedModel = useMemo(
    () =>
      catalog?.connectionId === selectedConnectionId
        ? catalog.models.find((model) => model.id === modelId) ?? null
        : null,
    [catalog, modelId, selectedConnectionId],
  );
  const effortOptions = useMemo(
    () => reasoningOptions(selectedModel),
    [selectedModel],
  );
  const validReasoningEffort = effortOptions.includes(reasoningEffort)
    ? reasoningEffort
    : "auto";
  const effectiveEffort =
    thread?.reasoningEffort ?? defaults.reasoningEffort ?? "auto";
  const effectiveContextWindow = thread?.contextWindow ?? null;
  const contextOptions = useMemo(
    () =>
      contextWindowOptions(
        selectedModel?.contextWindow ?? null,
        contextWindow,
      ),
    [contextWindow, selectedModel?.contextWindow],
  );

  useEffect(() => {
    if (!open || !selectedConnectionId) return;
    let cancelled = false;
    void listModels(selectedConnectionId)
      .then((result) => {
        if (!cancelled) {
          setCatalog(result);
          setModelFailure(null);
        }
      })
      .catch((cause) => {
        if (!cancelled) {
          setModelFailure({
            connectionId: selectedConnectionId,
            message: cause instanceof Error ? cause.message : String(cause),
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [listModels, open, selectedConnectionId]);

  const models = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return (
      catalog?.connectionId === selectedConnectionId ? catalog.models : []
    )
      .filter(
        (model) =>
          !normalized ||
          model.id.toLocaleLowerCase().includes(normalized) ||
          model.name.toLocaleLowerCase().includes(normalized),
      )
      .slice(0, 120);
  }, [catalog, query, selectedConnectionId]);
  const modelError =
    modelFailure?.connectionId === selectedConnectionId
      ? modelFailure.message
      : null;
  const loadingModels =
    refreshingModels ||
    Boolean(
      open &&
        selectedConnectionId &&
        catalog?.connectionId !== selectedConnectionId &&
        !modelError,
    );

  const effortText = (value: string) => effortLabel(value, t);

  const apply = () => {
    if (!selectedConnectionId || !modelId) return;
    onChange(
      selectedConnectionId,
      modelId,
      validReasoningEffort,
      contextWindow,
    );
    setOpen(false);
    setQuery("");
    setManualModel("");
  };

  const refresh = async () => {
    if (!selectedConnectionId) return;
    setRefreshingModels(true);
    setModelFailure(null);
    try {
      setCatalog(await listModels(selectedConnectionId, true));
    } catch (cause) {
      setModelFailure({
        connectionId: selectedConnectionId,
        message: cause instanceof Error ? cause.message : String(cause),
      });
    } finally {
      setRefreshingModels(false);
    }
  };

  return (
    <div className={styles.picker} ref={rootRef}>
      <button
        type="button"
        className={styles.trigger}
        onClick={() => {
          if (!open && !connectionId && effectiveConnection) {
            setConnectionId(effectiveConnection.id);
          }
          if (!open) {
            setModelId(effectiveModel ?? "");
            setReasoningEffort(effectiveEffort);
            setContextWindow(effectiveContextWindow);
          }
          setOpen((current) => !current);
        }}
        disabled={disabled}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={t("composer.model.label", "Session model")}
        title={t("composer.model.title", "Connection and model for this Session")}
      >
        <Cpu size={12} />
        <span>
          <small>
            {effectiveConnection?.label ??
              t("composer.model.automatic", "Automatic")}
          </small>
          <strong>
            {effectiveModel ||
              t("composer.model.configured", "Configured model")}
          </strong>
          <em>
            {effortText(effectiveEffort)} ·{" "}
            {effectiveContextWindow
              ? t("composer.model.contextCap", "{{tokens}} context cap", {
                  tokens: formatTokens(effectiveContextWindow),
                })
              : t("composer.model.autoContext", "Auto context")}
          </em>
        </span>
        <ChevronDown size={12} />
      </button>

      {open ? (
        <Dialog
          as="section"
          className={styles.menu}
          data-popover=""
          label={t("composer.model.choose", "Choose connection and model")}
          dismissOnPressOutside={rootRef}
          onClose={() => setOpen(false)}
        >
          <header>
            <div>
              <strong>{t("composer.model.heading", "Run future Turns with")}</strong>
              <span>
                {t(
                  "composer.model.historyNote",
                  "History stays in this Session when the model changes.",
                )}
              </span>
            </div>
            <button
              type="button"
              onClick={() => void refresh()}
              disabled={!selectedConnectionId || loadingModels}
              aria-label={t("composer.model.refresh", "Refresh model catalog")}
            >
              <RefreshCw size={14} />
            </button>
          </header>

          <label className={styles.connectionField}>
            {t("composer.model.connection", "Connection")}
            <select
              value={selectedConnectionId}
              onChange={(event) => {
                setConnectionId(event.target.value);
                setModelId("");
                setReasoningEffort("auto");
                setContextWindow(null);
                setModelFailure(null);
                setQuery("");
              }}
            >
              <option value="">
                {t("composer.model.chooseConnection", "Choose a connection")}
              </option>
              {connectionCatalog?.connections.map((connection) => (
                <option
                  key={connection.id}
                  value={connection.id}
                  disabled={!connection.enabled}
                >
                  {connection.label}
                  {connection.configured
                    ? ""
                    : t("composer.model.credentialNeeded", " · credential needed")}
                </option>
              ))}
            </select>
          </label>

          <label className={styles.search}>
            <Search size={14} />
            <span>{t("composer.model.search", "Search models")}</span>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t("composer.model.searchPlaceholder", "Search model IDs")}
              autoFocus
            />
          </label>

          <div className={styles.models} role="listbox">
            {models.map((model) => {
              const selected = model.id === modelId;
              return (
                <button
                  type="button"
                  role="option"
                  aria-selected={selected}
                  key={model.id}
                  onClick={() => {
                    setModelId(model.id);
                    setReasoningEffort("auto");
                    setContextWindow(null);
                  }}
                >
                  <span>
                    <strong>{model.name}</strong>
                    <code>{model.id}</code>
                  </span>
                  <small>
                    {t("composer.model.contextSize", "{{tokens}} context", {
                      tokens: formatTokens(model.contextWindow),
                    })}
                    {model.reasoning
                      ? t("composer.model.thinkingTag", " · Thinking")
                      : ""}
                    {selected ? <Check size={12} /> : null}
                  </small>
                </button>
              );
            })}
            {loadingModels ? (
              <p>{t("composer.model.loading", "Loading model catalog…")}</p>
            ) : null}
            {!loadingModels && !models.length ? (
              <p>
                {modelError ??
                  catalog?.error ??
                  t(
                    "composer.model.noMatches",
                    "No matching models. Enter an exact model ID below.",
                  )}
              </p>
            ) : null}
          </div>

          <section
            className={styles.effort}
            aria-label={t("composer.model.effort", "Thinking effort")}
          >
            <div>
              <strong>{t("composer.model.thinking", "Thinking")}</strong>
              <span>
                {selectedModel?.reasoning
                  ? t(
                      "composer.model.effortApplies",
                      "Applied to future Turns in this Session.",
                    )
                  : t(
                      "composer.model.noEffort",
                      "This model does not publish adjustable Thinking levels.",
                    )}
              </span>
            </div>
            <div
              role="radiogroup"
              aria-label={t("composer.model.effortLevel", "Thinking effort level")}
            >
              {effortOptions.map((effort) => (
                <button
                  type="button"
                  role="radio"
                  aria-checked={validReasoningEffort === effort}
                  key={effort}
                  onClick={() => setReasoningEffort(effort)}
                >
                  {effortText(effort)}
                </button>
              ))}
            </div>
          </section>

          <section
            className={styles.context}
            aria-label={t("composer.model.contextWindow", "Context window")}
          >
            <div>
              <strong>{t("composer.model.context", "Context")}</strong>
              <span>
                {t(
                  "composer.model.contextNote",
                  "Cap future Turns below the model's published window. Lower caps compact history sooner.",
                )}
              </span>
            </div>
            <select
              aria-label={t("composer.model.contextWindowCap", "Context window cap")}
              value={contextWindow ?? ""}
              disabled={!selectedModel}
              onChange={(event) =>
                setContextWindow(
                  event.target.value ? Number(event.target.value) : null,
                )
              }
            >
              <option value="">
                {t("composer.model.automatic", "Automatic")}
                {selectedModel
                  ? ` · ${formatTokens(selectedModel.contextWindow)}`
                  : ""}
              </option>
              {contextOptions.map((value) => (
                <option key={value} value={value}>
                  {formatTokens(value)}
                </option>
              ))}
            </select>
          </section>

          <footer>
            <input
              value={manualModel}
              onChange={(event) => setManualModel(event.target.value)}
              placeholder={t("composer.model.exactId", "Exact model ID")}
              aria-label={t("composer.model.exactId", "Exact model ID")}
            />
            <button
              type="button"
              onClick={() => {
                setModelId(manualModel.trim());
                setReasoningEffort("auto");
                setContextWindow(null);
              }}
              disabled={!selectedConnectionId || !manualModel.trim()}
            >
              {t("composer.model.select", "Select")}
            </button>
            <button
              type="button"
              className={styles.apply}
              onClick={apply}
              disabled={!selectedConnectionId || !modelId}
            >
              {t("composer.model.apply", "Apply")}
            </button>
            <button
              type="button"
              className={styles.reset}
              onClick={() => {
                onChange(null, null, null, null);
                setOpen(false);
              }}
            >
              {t("composer.model.useDefaults", "Use defaults")}
            </button>
          </footer>
        </Dialog>
      ) : null}
    </div>
  );
}

function agentDefaults(
  settings: SettingsSnapshot | null,
): {
  connection: string | null;
  model: string | null;
  reasoningEffort: string | null;
} {
  const defaults = settings?.agents.defaults;
  if (typeof defaults !== "object" || defaults === null || Array.isArray(defaults)) {
    return { connection: null, model: null, reasoningEffort: null };
  }
  const connection =
    typeof defaults.connection === "string" && defaults.connection
      ? defaults.connection
      : typeof defaults.provider === "string" && defaults.provider !== "auto"
        ? defaults.provider
        : null;
  const model =
    typeof defaults.model === "string" && defaults.model ? defaults.model : null;
  const reasoningEffort =
    typeof defaults.reasoningEffort === "string" && defaults.reasoningEffort
      ? defaults.reasoningEffort
      : typeof defaults.reasoning_effort === "string" && defaults.reasoning_effort
        ? defaults.reasoning_effort
        : null;
  return { connection, model, reasoningEffort };
}

/** The efforts a model offers: always "auto", then "none" when it may skip. */
function reasoningOptions(model: CatalogModel | null): string[] {
  const capabilities = model?.reasoning;
  if (!capabilities) return ["auto"];
  return [
    "auto",
    ...(capabilities.mandatory ? [] : ["none"]),
    ...capabilities.supportedEfforts,
  ];
}

function resolveConnection(
  connections: ConnectionInfo[],
  selectedId: string | null,
  model: string | null,
): ConnectionInfo | null {
  if (selectedId) {
    const selected = connections.find((connection) => connection.id === selectedId);
    if (selected) return selected;
  }
  const prefix = model?.split("/", 1)[0]?.toLocaleLowerCase();
  return (
    connections.find(
      (connection) =>
        connection.id === prefix || connection.providerName === prefix,
    ) ??
    connections.find((connection) => connection.configured && connection.enabled) ??
    null
  );
}

function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${Math.round(value / 1_000)}K`;
  return String(value);
}

function contextWindowOptions(
  published: number | null,
  current: number | null,
): number[] {
  if (!published) return [];
  const options = [32_000, 64_000, 128_000, 256_000, 512_000, 1_000_000].filter(
    (value) => value < published,
  );
  if (current && current <= published) options.push(current);
  return [...new Set(options)].sort((left, right) => left - right);
}
