import {
  Check,
  ChevronDown,
  Cpu,
  RefreshCw,
  Search,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import type {
  ConnectionInfo,
  ModelCatalogResult,
  Project,
  SettingsSnapshot,
  Thread,
} from "../../generated/app-server";
import type { DesktopRuntime } from "../../rpc/contracts";
import { useConnectionCatalog } from "../settings/useConnectionCatalog";
import styles from "./ModelPicker.module.css";

interface ModelPickerProps {
  runtime: DesktopRuntime;
  project: Project | null;
  thread: Thread | null;
  settings: SettingsSnapshot | null;
  disabled: boolean;
  onChange(connectionId: string | null, model: string | null): void;
}

export function ModelPicker({
  runtime,
  project,
  thread,
  settings,
  disabled,
  onChange,
}: ModelPickerProps) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const {
    catalog: connectionCatalog,
    models: listModels,
  } = useConnectionCatalog(runtime, project?.id ?? null);
  const [open, setOpen] = useState(false);
  const [connectionId, setConnectionId] = useState("");
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

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

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

  const choose = (model: string) => {
    onChange(selectedConnectionId || null, model);
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
          setOpen((current) => !current);
        }}
        disabled={disabled}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label="Session model"
        title="Connection and model for this Session"
      >
        <Cpu size={12} />
        <span>
          <small>{effectiveConnection?.label ?? "Automatic"}</small>
          <strong>{effectiveModel || "Configured model"}</strong>
        </span>
        <ChevronDown size={11} />
      </button>

      {open ? (
        <section
          className={styles.menu}
          role="dialog"
          aria-label="Choose connection and model"
        >
          <header>
            <div>
              <strong>Run future Turns with</strong>
              <span>History stays in this Session when the model changes.</span>
            </div>
            <button
              type="button"
              onClick={() => void refresh()}
              disabled={!selectedConnectionId || loadingModels}
              aria-label="Refresh model catalog"
            >
              <RefreshCw size={13} />
            </button>
          </header>

          <label className={styles.connectionField}>
            Connection
            <select
              value={selectedConnectionId}
              onChange={(event) => {
                setConnectionId(event.target.value);
                setModelFailure(null);
                setQuery("");
              }}
            >
              <option value="">Choose a connection</option>
              {connectionCatalog?.connections.map((connection) => (
                <option
                  key={connection.id}
                  value={connection.id}
                  disabled={!connection.enabled}
                >
                  {connection.label}
                  {connection.configured ? "" : " · credential needed"}
                </option>
              ))}
            </select>
          </label>

          <label className={styles.search}>
            <Search size={13} />
            <span>Search models</span>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search model IDs"
              autoFocus
            />
          </label>

          <div className={styles.models} role="listbox">
            {models.map((model) => {
              const selected =
                selectedConnectionId === effectiveConnection?.id &&
                model.id === effectiveModel;
              return (
                <button
                  type="button"
                  role="option"
                  aria-selected={selected}
                  key={model.id}
                  onClick={() => choose(model.id)}
                >
                  <span>
                    <strong>{model.name}</strong>
                    <code>{model.id}</code>
                  </span>
                  <small>
                    {formatTokens(model.contextWindow)} context
                    {selected ? <Check size={12} /> : null}
                  </small>
                </button>
              );
            })}
            {loadingModels ? <p>Loading model catalog…</p> : null}
            {!loadingModels && !models.length ? (
              <p>
                {modelError ??
                  catalog?.error ??
                  "No matching models. Enter an exact model ID below."}
              </p>
            ) : null}
          </div>

          <footer>
            <input
              value={manualModel}
              onChange={(event) => setManualModel(event.target.value)}
              placeholder="Exact model ID"
              aria-label="Exact model ID"
            />
            <button
              type="button"
              onClick={() => choose(manualModel.trim())}
              disabled={!selectedConnectionId || !manualModel.trim()}
            >
              Use model
            </button>
            <button
              type="button"
              className={styles.reset}
              onClick={() => {
                onChange(null, null);
                setOpen(false);
              }}
            >
              Use defaults
            </button>
          </footer>
        </section>
      ) : null}
    </div>
  );
}

function agentDefaults(
  settings: SettingsSnapshot | null,
): { connection: string | null; model: string | null } {
  const defaults = settings?.agents.defaults;
  if (typeof defaults !== "object" || defaults === null || Array.isArray(defaults)) {
    return { connection: null, model: null };
  }
  const connection =
    typeof defaults.connection === "string" && defaults.connection
      ? defaults.connection
      : typeof defaults.provider === "string" && defaults.provider !== "auto"
        ? defaults.provider
        : null;
  const model =
    typeof defaults.model === "string" && defaults.model ? defaults.model : null;
  return { connection, model };
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
