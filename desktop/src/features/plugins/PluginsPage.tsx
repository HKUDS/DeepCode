import { FolderInput, Power, RefreshCw, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { confirmAction } from "../../platform/confirmAction";
import type { ClientRuntime } from "../../rpc/contracts";
import styles from "../management/ManagementWorkspace.module.css";
import { pluginComponentLabel, pluginStatusLabel } from "./pluginPresentation";
import { usePluginCatalog } from "./usePluginCatalog";

export function PluginsPage({ runtime }: { runtime: ClientRuntime }) {
  const { t } = useTranslation();
  const catalog = usePluginCatalog(runtime);

  const addPlugin = async () => {
    const path = await runtime.pickDirectory();
    if (path) await catalog.add(path);
  };

  return (
    <section className={styles.page} aria-labelledby="plugins-title">
      <header className={styles.pageHeader}>
        <div>
          <p className={styles.eyebrow}>
            {t("plugins.eyebrow", "Local extensions")}
          </p>
          <h1 id="plugins-title">{t("plugins.title", "Plugins")}</h1>
          <p>
            {t(
              "plugins.description",
              "Register trusted local Plugin folders. Their Skills join the shared catalog and their MCP servers join the shared tool runtime.",
            )}
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
            {t("plugins.reload", "Reload")}
          </button>
          <button
            className={styles.primaryButton}
            type="button"
            disabled={catalog.loading}
            onClick={() => void addPlugin()}
          >
            <FolderInput size={14} />
            {t("plugins.add", "Add local Plugin")}
          </button>
        </div>
      </header>

      <div className={styles.contextBar}>
        <strong>Agent Plugins 1.0</strong>
        <span>
          {t(
            "plugins.contextNote",
            "Skills and MCP remain independently configurable; a Plugin only packages and enables its contributed components.",
          )}
        </span>
      </div>
      {catalog.error ? <p className={styles.errorBanner}>{catalog.error}</p> : null}
      {catalog.diagnostics.length ? (
        <details className={styles.warningBlock}>
          <summary>
            {t("plugins.diagnosticsSummary", "{{total}} Plugin diagnostic(s)", {
              total: catalog.diagnostics.length,
            })}
          </summary>
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
                  {plugin.version ?? t("plugins.unresolved", "unresolved")}
                  <em data-status={plugin.status}>{pluginStatusLabel(plugin.status, t)}</em>
                </span>
                <strong>{plugin.name}</strong>
                <small>{plugin.description || plugin.error}</small>
              </button>
            ))
          ) : (
            <p className={styles.emptyCopy}>
              {t(
                "plugins.empty",
                "No Plugins registered. Add a trusted folder containing plugin.json and an optional fixed skills directory.",
              )}
            </p>
          )}
        </div>
        <article className={styles.detailPane}>
          {catalog.selected ? (
            <>
              <p className={styles.eyebrow}>
                {t("plugins.detail.eyebrow", "Local Plugin")}
              </p>
              <h2>{catalog.selected.name}</h2>
              <p>
                {catalog.selected.description ||
                  t("plugins.detail.unresolved", "Manifest could not be resolved.")}
              </p>
              <div className={styles.skillActions}>
                <span className={styles.badge} data-status={catalog.selected.status}>
                  {pluginStatusLabel(catalog.selected.status, t)}
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
                  <Power size={14} />
                  {catalog.selected.enabled
                    ? t("plugins.disable", "Disable")
                    : t("plugins.enable", "Enable")}
                </button>
                <button
                  type="button"
                  className={styles.dangerButton}
                  disabled={catalog.loading}
                  onClick={() => {
                    const selected = catalog.selected;
                    if (!selected) return;
                    void (async () => {
                      if (
                        await confirmAction(
                          t(
                            "plugins.unregisterConfirm",
                            "Unregister “{{name}}”? Its source files will not be deleted.",
                            { name: selected.name },
                          ),
                          {
                            destructive: true,
                            confirmLabel: t("plugins.unregister", "Unregister"),
                          },
                        )
                      ) {
                        await catalog.remove(selected.id);
                      }
                    })();
                  }}
                >
                  <Trash2 size={14} />
                  {t("plugins.unregister", "Unregister")}
                </button>
              </div>
              <dl className={styles.metadata}>
                <div>
                  <dt>{t("plugins.detail.version", "Version")}</dt>
                  <dd>
                    {catalog.selected.version ??
                      t("plugins.detail.versionUnavailable", "Unavailable")}
                  </dd>
                </div>
                <div>
                  <dt>{t("plugins.detail.source", "Source")}</dt>
                  <dd>{catalog.selected.path}</dd>
                </div>
                <div>
                  <dt>{t("plugins.detail.manifest", "Manifest")}</dt>
                  <dd>{catalog.selected.manifestPath}</dd>
                </div>
                <div>
                  <dt>{t("plugins.detail.schema", "Schema")}</dt>
                  <dd>
                    {catalog.selected.schema ??
                      t("plugins.detail.schemaUnresolved", "Manifest unresolved")}
                  </dd>
                </div>
                <div>
                  <dt>{t("plugins.detail.components", "Components")}</dt>
                  <dd>
                    {catalog.selected.components
                      .map(
                        (component) =>
                          `${pluginComponentLabel(component, t)}${
                            component.itemCount === null
                              ? ""
                              : ` (${component.itemCount})`
                          }`,
                      )
                      .join(", ") ||
                      t("plugins.detail.noComponents", "No components discovered")}
                  </dd>
                </div>
              </dl>
              {catalog.selected.error ? (
                <p className={styles.errorBanner}>{catalog.selected.error}</p>
              ) : null}
              {catalog.selected.diagnostics.length ? (
                <details className={styles.warningBlock}>
                  <summary>
                    {t("plugins.detail.packageDiagnostics", "Package diagnostics")}
                  </summary>
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
              {t(
                "plugins.selectHint",
                "Select a Plugin to inspect its manifest and activation state.",
              )}
            </p>
          )}
        </article>
      </div>
    </section>
  );
}
