import { FolderInput, Power, RefreshCw, Trash2 } from "lucide-react";

import type { DesktopRuntime } from "../../rpc/contracts";
import styles from "../management/ManagementWorkspace.module.css";
import { usePluginCatalog } from "./usePluginCatalog";

export function PluginsPage({ runtime }: { runtime: DesktopRuntime }) {
  const catalog = usePluginCatalog(runtime);

  const addPlugin = async () => {
    const path = await runtime.pickDirectory();
    if (path) await catalog.add(path);
  };

  return (
    <section className={styles.page} aria-labelledby="plugins-title">
      <header className={styles.pageHeader}>
        <div>
          <p className={styles.eyebrow}>Local extensions</p>
          <h1 id="plugins-title">Plugins</h1>
          <p>
            Register trusted local Plugin folders. Their Skills join the shared
            catalog and their MCP servers join the shared tool runtime.
          </p>
        </div>
        <div className={styles.formActions}>
          <button
            className={styles.secondaryButton}
            type="button"
            disabled={catalog.loading}
            onClick={() => void catalog.refresh()}
          >
            <RefreshCw size={14} />
            Reload
          </button>
          <button
            className={styles.primaryButton}
            type="button"
            disabled={catalog.loading}
            onClick={() => void addPlugin()}
          >
            <FolderInput size={14} />
            Add local Plugin
          </button>
        </div>
      </header>

      <div className={styles.contextBar}>
        <strong>Agent Plugins 1.0</strong>
        <span>
          Skills and MCP remain independently configurable; a Plugin only
          packages and enables its contributed components.
        </span>
      </div>
      {catalog.error ? <p className={styles.errorBanner}>{catalog.error}</p> : null}
      {catalog.diagnostics.length ? (
        <details className={styles.warningBlock}>
          <summary>{catalog.diagnostics.length} Plugin diagnostic(s)</summary>
          {catalog.diagnostics.map((diagnostic, index) => (
            <p key={`${diagnostic.code}-${index}`}>
              {diagnostic.severity}: {diagnostic.message}
            </p>
          ))}
        </details>
      ) : null}

      <div className={styles.splitView}>
        <div className={styles.listPane}>
          {catalog.plugins.length ? (
            catalog.plugins.map((plugin) => (
              <button
                type="button"
                key={plugin.id}
                data-active={catalog.selected?.id === plugin.id}
                data-status={plugin.status}
                onClick={() => catalog.select(plugin.id)}
              >
                <span className={styles.skillRowMeta}>
                  {plugin.version ?? "unresolved"}
                  <em data-status={plugin.status}>{plugin.status}</em>
                </span>
                <strong>{plugin.name}</strong>
                <small>{plugin.description || plugin.error}</small>
              </button>
            ))
          ) : (
            <p className={styles.emptyCopy}>
              No Plugins registered. Add a trusted folder containing plugin.json
              and an optional fixed skills directory.
            </p>
          )}
        </div>
        <article className={styles.detailPane}>
          {catalog.selected ? (
            <>
              <p className={styles.eyebrow}>Local Plugin</p>
              <h2>{catalog.selected.name}</h2>
              <p>{catalog.selected.description || "Manifest could not be resolved."}</p>
              <div className={styles.skillActions}>
                <span className={styles.badge} data-status={catalog.selected.status}>
                  {catalog.selected.status}
                </span>
                <button
                  type="button"
                  disabled={catalog.loading || catalog.selected.status === "invalid"}
                  onClick={() =>
                    void catalog.setEnabled(
                      catalog.selected!.id,
                      !catalog.selected!.enabled,
                    )
                  }
                >
                  <Power size={13} />
                  {catalog.selected.enabled ? "Disable" : "Enable"}
                </button>
                <button
                  type="button"
                  className={styles.dangerButton}
                  disabled={catalog.loading}
                  onClick={() => {
                    const selected = catalog.selected;
                    if (
                      selected &&
                      window.confirm(
                        `Unregister “${selected.name}”? Its source files will not be deleted.`,
                      )
                    ) {
                      void catalog.remove(selected.id);
                    }
                  }}
                >
                  <Trash2 size={13} />
                  Unregister
                </button>
              </div>
              <dl className={styles.metadata}>
                <div>
                  <dt>Version</dt>
                  <dd>{catalog.selected.version ?? "Unavailable"}</dd>
                </div>
                <div>
                  <dt>Source</dt>
                  <dd>{catalog.selected.path}</dd>
                </div>
                <div>
                  <dt>Manifest</dt>
                  <dd>{catalog.selected.manifestPath}</dd>
                </div>
                <div>
                  <dt>Schema</dt>
                  <dd>{catalog.selected.schema ?? "Manifest unresolved"}</dd>
                </div>
                <div>
                  <dt>Components</dt>
                  <dd>
                    {catalog.selected.components
                      .map(
                        (component) =>
                          `${component.kind}: ${component.status}${
                            component.itemCount === null
                              ? ""
                              : ` (${component.itemCount})`
                          }`,
                      )
                      .join(", ") || "No components discovered"}
                  </dd>
                </div>
              </dl>
              {catalog.selected.error ? (
                <p className={styles.errorBanner}>{catalog.selected.error}</p>
              ) : null}
              {catalog.selected.diagnostics.length ? (
                <details className={styles.warningBlock}>
                  <summary>Package diagnostics</summary>
                  {catalog.selected.diagnostics.map((diagnostic, index) => (
                    <p key={`${diagnostic.code}-${index}`}>
                      {diagnostic.severity}: {diagnostic.message}
                    </p>
                  ))}
                </details>
              ) : null}
            </>
          ) : (
            <p className={styles.emptyCopy}>
              Select a Plugin to inspect its manifest and activation state.
            </p>
          )}
        </article>
      </div>
    </section>
  );
}
