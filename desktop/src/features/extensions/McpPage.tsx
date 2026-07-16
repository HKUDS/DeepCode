import { Plus, RefreshCw, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import type {
  ConfigScope,
  JsonObject,
  McpServerInfo,
  Project,
} from "../../generated/app-server";
import { confirmAction } from "../../platform/confirmAction";
import type { DesktopRuntime } from "../../rpc/contracts";
import { useMcpInventory } from "./useMcpInventory";
import styles from "../management/ManagementWorkspace.module.css";

interface McpPageProps {
  runtime: DesktopRuntime;
  project: Project | null;
}

interface McpDraft {
  originalName: string | null;
  name: string;
  scope: ConfigScope;
  transport: McpServerInfo["transport"];
  command: string;
  url: string;
  args: string;
  enabledTools: string;
  timeout: string;
  description: string;
  env: string;
  headers: string;
}

const newDraft: McpDraft = {
  originalName: null,
  name: "",
  scope: "user",
  transport: "stdio",
  command: "",
  url: "",
  args: "",
  enabledTools: "*",
  timeout: "300",
  description: "",
  env: "",
  headers: "",
};

export function McpPage({ runtime, project }: McpPageProps) {
  const mcp = useMcpInventory(runtime, project?.id ?? null);
  const [draft, setDraft] = useState<McpDraft | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const servers = useMemo(
    () => mcp.inventory?.servers ?? [],
    [mcp.inventory?.servers],
  );
  const canWriteProject = project?.trustState === "trusted";

  const selected = useMemo(
    () =>
      servers.find((server) => server.name === draft?.originalName) ?? null,
    [draft?.originalName, servers],
  );

  const edit = (server: McpServerInfo) => {
    setFormError(null);
    setDraft({
      originalName: server.name,
      name: server.name,
      scope: server.source === "project" ? "project" : "user",
      transport: server.transport,
      command: server.command ?? "",
      url: server.url ?? "",
      args: "",
      enabledTools: server.enabledTools.join(", "),
      timeout: String(server.toolTimeout),
      description: server.description ?? "",
      env: "",
      headers: "",
    });
  };

  const save = async () => {
    if (!draft) return;
    setFormError(null);
    try {
      const timeout = Number(draft.timeout);
      if (
        !/^\d+$/.test(draft.timeout.trim()) ||
        !Number.isSafeInteger(timeout) ||
        timeout < 1
      ) {
        throw new Error("Timeout must be a positive whole number.");
      }
      const server: JsonObject = {
        type: draft.transport,
        enabledTools: draft.enabledTools
          .split(",")
          .map((value) => value.trim())
          .filter(Boolean),
        toolTimeout: timeout,
        description: draft.description.trim() || null,
      };
      if (draft.transport === "stdio") {
        server.command = draft.command.trim();
      } else {
        server.url = draft.url.trim();
      }
      if (draft.args.trim()) {
        server.args = draft.args
          .split("\n")
          .map((value) => value.trim())
          .filter(Boolean);
      }
      if (draft.env.trim()) server.env = parseObject(draft.env, "Environment");
      if (draft.headers.trim()) {
        server.headers = parseObject(draft.headers, "Headers");
      }
      const saved = await mcp.upsert(draft.name.trim(), draft.scope, server);
      if (saved) setDraft(null);
    } catch (cause) {
      setFormError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const remove = async (server: McpServerInfo) => {
    const scope: ConfigScope = server.source === "project" ? "project" : "user";
    if (
      !(await confirmAction(
        `Remove ${server.name} from the ${scope} configuration? Inherited entries may become visible again.`,
        {
          confirmLabel: "Remove server",
        },
      ))
    ) {
      return;
    }
    await mcp.remove(server.name, scope);
    if (draft?.name === server.name) setDraft(null);
  };

  return (
    <section className={styles.page} aria-labelledby="mcp-title">
      <header className={styles.pageHeader}>
        <div>
          <p className={styles.eyebrow}>Tool servers</p>
          <h1 id="mcp-title">MCP configuration</h1>
          <p>
            Real user and project MCP entries. “Configured” does not claim a
            live connection; servers are connected by the runtime when used.
          </p>
        </div>
        <div className={styles.headerActions}>
          <button
            className={styles.secondaryButton}
            type="button"
            disabled={mcp.loading}
            onClick={() => void mcp.refresh()}
          >
            <RefreshCw size={14} />
            Refresh
          </button>
          <button
            className={styles.primaryButton}
            type="button"
            onClick={() => {
              setFormError(null);
              setDraft({ ...newDraft });
            }}
          >
            <Plus size={14} />
            Add server
          </button>
        </div>
      </header>

      {project ? (
        <div className={styles.contextBar}>
          <strong>{project.displayName}</strong>
          <span>
            Project overrides: {mcp.inventory?.projectConfigPath ?? "not loaded"}
          </span>
        </div>
      ) : null}
      {mcp.error ? <p className={styles.errorBanner}>{mcp.error}</p> : null}

      <div className={styles.cardList}>
        {servers.map((server) => (
          <article className={styles.card} key={server.name}>
            <header>
              <div>
                <p className={styles.eyebrow}>{server.source} config</p>
                <h2>{server.name}</h2>
              </div>
              <span
                className={styles.badge}
                data-status={server.configurationState}
              >
                {server.configurationState}
              </span>
            </header>
            <p>{server.description ?? server.configurationMessage}</p>
            <code>
              {server.transport === "stdio"
                ? [server.command, ...server.args].filter(Boolean).join(" ")
                : server.url}
            </code>
            <dl className={styles.metadata}>
              <div>
                <dt>Transport</dt>
                <dd>{server.transport}</dd>
              </div>
              <div>
                <dt>Tools</dt>
                <dd>{server.enabledTools.join(", ") || "none"}</dd>
              </div>
              <div>
                <dt>Hidden values</dt>
                <dd>
                  {[...server.envKeys, ...server.headerKeys].join(", ") || "none"}
                </dd>
              </div>
            </dl>
            <footer className={styles.cardActions}>
              <button type="button" onClick={() => edit(server)}>
                Edit
              </button>
              <button
                type="button"
                disabled={
                  server.source === "default" ||
                  (server.source === "project" && !canWriteProject)
                }
                onClick={() => void remove(server)}
              >
                <Trash2 size={13} />
                Remove
              </button>
            </footer>
          </article>
        ))}
        {!servers.length && !mcp.loading ? (
          <p className={styles.emptyCopy}>No MCP servers are configured.</p>
        ) : null}
      </div>

      {draft ? (
        <section className={styles.formCard} aria-label="MCP server editor">
          <header>
            <div>
              <p className={styles.eyebrow}>
                {selected ? "Edit server" : "New server"}
              </p>
              <h2>{selected?.name ?? "MCP server"}</h2>
            </div>
            <button type="button" onClick={() => setDraft(null)}>
              Cancel
            </button>
          </header>
          <div className={styles.formGrid}>
            <label>
              Name
              <input
                value={draft.name}
                disabled={draft.originalName !== null}
                onChange={(event) =>
                  setDraft({ ...draft, name: event.target.value })
                }
              />
            </label>
            <label>
              Scope
              <select
                value={draft.scope}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    scope: event.target.value as ConfigScope,
                  })
                }
              >
                <option value="user">User</option>
                <option value="project" disabled={!project || !canWriteProject}>
                  Project
                </option>
              </select>
            </label>
            <label>
              Transport
              <select
                value={draft.transport}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    transport: event.target.value as McpServerInfo["transport"],
                  })
                }
              >
                <option value="stdio">stdio</option>
                <option value="streamableHttp">Streamable HTTP</option>
                <option value="sse">SSE</option>
              </select>
            </label>
            <label>
              Timeout seconds
              <input
                inputMode="numeric"
                value={draft.timeout}
                onChange={(event) =>
                  setDraft({ ...draft, timeout: event.target.value })
                }
              />
            </label>
            {draft.transport === "stdio" ? (
              <label className={styles.wideField}>
                Command
                <input
                  value={draft.command}
                  onChange={(event) =>
                    setDraft({ ...draft, command: event.target.value })
                  }
                  placeholder="python3"
                />
              </label>
            ) : (
              <label className={styles.wideField}>
                URL
                <input
                  value={draft.url}
                  onChange={(event) =>
                    setDraft({ ...draft, url: event.target.value })
                  }
                  placeholder="https://example.test/mcp"
                />
              </label>
            )}
            <label className={styles.wideField}>
              Replacement arguments, one per line
              <textarea
                value={draft.args}
                onChange={(event) =>
                  setDraft({ ...draft, args: event.target.value })
                }
                placeholder={
                  selected
                    ? "Leave blank to preserve existing arguments"
                    : "server.py"
                }
                rows={3}
              />
            </label>
            <label>
              Enabled tools
              <input
                value={draft.enabledTools}
                onChange={(event) =>
                  setDraft({ ...draft, enabledTools: event.target.value })
                }
                placeholder="*"
              />
            </label>
            <label>
              Description
              <input
                value={draft.description}
                onChange={(event) =>
                  setDraft({ ...draft, description: event.target.value })
                }
              />
            </label>
            <label className={styles.wideField}>
              Environment JSON
              <textarea
                value={draft.env}
                onChange={(event) =>
                  setDraft({ ...draft, env: event.target.value })
                }
                placeholder='Leave blank to preserve hidden values, or {"TOKEN":"${TOKEN}"}'
                rows={3}
              />
            </label>
            <label className={styles.wideField}>
              Headers JSON
              <textarea
                value={draft.headers}
                onChange={(event) =>
                  setDraft({ ...draft, headers: event.target.value })
                }
                placeholder='Leave blank to preserve hidden values'
                rows={3}
              />
            </label>
          </div>
          {formError ? <p className={styles.errorBanner}>{formError}</p> : null}
          <footer className={styles.formActions}>
            <button type="button" onClick={() => setDraft(null)}>
              Cancel
            </button>
            <button
              className={styles.primaryButton}
              type="button"
              disabled={
                mcp.loading ||
                !draft.name.trim() ||
                (draft.scope === "project" && !canWriteProject)
              }
              onClick={() => void save()}
            >
              Save server
            </button>
          </footer>
        </section>
      ) : null}
    </section>
  );
}

function parseObject(text: string, label: string): JsonObject {
  const parsed: unknown = JSON.parse(text);
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error(`${label} must be a JSON object.`);
  }
  return parsed as JsonObject;
}
