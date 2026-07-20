import {
  FolderInput,
  Power,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { useState } from "react";

import type { ConfigScope, Project } from "../../generated/app-server";
import type { DesktopRuntime } from "../../rpc/contracts";
import { MarkdownContent } from "../thread/MarkdownContent";
import { useExtensionCatalog } from "./useExtensionCatalog";
import styles from "../management/ManagementWorkspace.module.css";

interface ExtensionsPageProps {
  runtime: DesktopRuntime;
  project: Project | null;
}

export function ExtensionsPage({ runtime, project }: ExtensionsPageProps) {
  const catalog = useExtensionCatalog(runtime, project?.id ?? null);
  const [tab, setTab] = useState<"skills" | "hooks">("skills");
  const [scope, setScope] = useState<ConfigScope>("project");

  const importSkill = async () => {
    const path = await runtime.pickDirectory();
    if (path) await catalog.importSkill(path, scope);
  };

  return (
    <section className={styles.page} aria-labelledby="extensions-title">
      <header className={styles.pageHeader}>
        <div>
          <p className={styles.eyebrow}>Agent capabilities</p>
          <h1 id="extensions-title">Skills &amp; Hooks</h1>
          <p>
            Skills are reusable instructions you can attach to a turn. Project and
            user entries use the same backend in Desktop and CLI.
          </p>
        </div>
        <div className={styles.formActions}>
          <label className={styles.compactSelect}>
            <span>Store changes in</span>
            <select
              aria-label="Skill configuration scope"
              title="Controls the import destination and enablement policy layer"
              value={scope}
              onChange={(event) => setScope(event.target.value as ConfigScope)}
            >
              <option value="project">This project</option>
              <option value="user">User settings</option>
            </select>
          </label>
          <button
            className={styles.secondaryButton}
            type="button"
            disabled={!project || catalog.loading}
            onClick={() => void catalog.refresh()}
          >
            <RefreshCw size={14} />
            Reload
          </button>
          <button
            className={styles.primaryButton}
            type="button"
            disabled={!project || catalog.loading}
            onClick={() => void importSkill()}
          >
            <FolderInput size={14} />
            Import folder
          </button>
        </div>
      </header>

      {!project ? (
        <EmptyProject />
      ) : (
        <>
          <div className={styles.contextBar}>
            <strong>{project.displayName}</strong>
            <span>{project.canonicalPath}</span>
          </div>
          <div className={styles.tabs} role="tablist" aria-label="Extension type">
            <button
              type="button"
              role="tab"
              aria-selected={tab === "skills"}
              onClick={() => setTab("skills")}
            >
              Skills {catalog.skills.length}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "hooks"}
              onClick={() => setTab("hooks")}
            >
              Hooks {catalog.hooks.length}
            </button>
          </div>
          {catalog.error ? (
            <p className={styles.errorBanner}>{catalog.error}</p>
          ) : null}
          {catalog.warnings.length ? (
            <details className={styles.warningBlock}>
              <summary>{catalog.warnings.length} discovery warning(s)</summary>
              {catalog.warnings.map((warning, index) => (
                <p key={`${warning}-${index}`}>{warning}</p>
              ))}
            </details>
          ) : null}
          {tab === "skills" ? (
            <div className={styles.splitView}>
              <div className={styles.listPane}>
                {catalog.skills.length ? (
                  catalog.skills.map((skill) => (
                    <button
                      type="button"
                      key={skill.id}
                      data-active={catalog.selectedSkill?.id === skill.id}
                      data-status={skill.status}
                      onClick={() => void catalog.selectSkill(skill.id)}
                    >
                      <span className={styles.skillRowMeta}>
                        {skill.source.replace(":", " · ")}
                        <em data-status={skill.status}>{skill.status}</em>
                      </span>
                      <strong>{skill.name || "Invalid Skill"}</strong>
                      <small>{skill.description || skill.error}</small>
                    </button>
                  ))
                ) : (
                  <p className={styles.emptyCopy}>
                    No Skills yet. Import a folder containing a valid SKILL.md, or
                    add one under .deepcode/skills.
                  </p>
                )}
              </div>
              <article className={styles.detailPane}>
                {catalog.selectedSkill ? (
                  <>
                    <p className={styles.eyebrow}>
                      {catalog.selectedSkill.source.replace(":", " · ")}
                    </p>
                    <h2>{catalog.selectedSkill.name}</h2>
                    <p>{catalog.selectedSkill.description}</p>
                    <div className={styles.skillActions}>
                      <span className={styles.badge} data-status={catalog.selectedSkill.status}>
                        {catalog.selectedSkill.status}
                      </span>
                      <button
                        type="button"
                        onClick={() =>
                          void catalog.setEnabled(
                            catalog.selectedSkill!.id,
                            !catalog.selectedSkill!.enabled,
                            scope,
                          )
                        }
                        disabled={catalog.loading}
                      >
                        <Power size={13} />
                        {catalog.selectedSkill.enabled ? "Disable" : "Enable"}
                      </button>
                      {catalog.selectedSkill.sourceRoot === "deepcode" ? (
                        <button
                          type="button"
                          className={styles.dangerButton}
                          disabled={catalog.loading}
                          onClick={() => {
                            const selected = catalog.selectedSkill;
                            if (
                              selected &&
                              window.confirm(
                                `Delete the managed Skill “${selected.name}”?`,
                              )
                            ) {
                              void catalog.deleteSkill(selected.id);
                            }
                          }}
                        >
                          <Trash2 size={13} />
                          Delete
                        </button>
                      ) : null}
                    </div>
                    <dl className={styles.metadata}>
                      <div>
                        <dt>Location</dt>
                        <dd>{catalog.selectedSkill.location}</dd>
                      </div>
                      <div>
                        <dt>Intended tools</dt>
                        <dd>
                          {catalog.selectedSkill.allowedTools.join(", ") || "Not declared"}
                        </dd>
                      </div>
                      <div>
                        <dt>Revision</dt>
                        <dd>{catalog.selectedSkill.revision}</dd>
                      </div>
                    </dl>
                    {catalog.selectedSkill.error ? (
                      <p className={styles.errorBanner}>
                        {catalog.selectedSkill.error}
                      </p>
                    ) : null}
                    <MarkdownContent>
                      {catalog.selectedSkill.instructions}
                    </MarkdownContent>
                    {catalog.selectedSkill.truncated ? (
                      <p className={styles.note}>
                        Instructions were truncated at the safe preview limit.
                      </p>
                    ) : null}
                  </>
                ) : (
                  <p className={styles.emptyCopy}>
                    Select a Skill to inspect its exact instructions, source, status,
                    and revision.
                  </p>
                )}
              </article>
            </div>
          ) : (
            <div className={styles.cardList}>
              {catalog.hooks.length ? (
                catalog.hooks.map((hook) => (
                  <article
                    className={styles.card}
                    key={`${hook.sourcePath}:${hook.displayOrder}`}
                  >
                    <header>
                      <div>
                        <p className={styles.eyebrow}>{hook.source}</p>
                        <h2>{hook.eventName}</h2>
                      </div>
                      <span className={styles.badge}>
                        {hook.matcher || "all events"}
                      </span>
                    </header>
                    <code>{hook.command}</code>
                    <dl className={styles.metadata}>
                      <div>
                        <dt>Timeout</dt>
                        <dd>{hook.timeoutSeconds} seconds</dd>
                      </div>
                      <div>
                        <dt>Source</dt>
                        <dd>{hook.sourcePath}</dd>
                      </div>
                    </dl>
                    {hook.statusMessage ? <p>{hook.statusMessage}</p> : null}
                  </article>
                ))
              ) : (
                <p className={styles.emptyCopy}>
                  No lifecycle command hooks were discovered for this project.
                </p>
              )}
              {catalog.hooksTruncated ? (
                <p className={styles.note}>Hook list truncated at 500 handlers.</p>
              ) : null}
            </div>
          )}
        </>
      )}
    </section>
  );
}

function EmptyProject() {
  return (
    <div className={styles.emptyState}>
      <h2>Open a project to inspect its extensions.</h2>
      <p>Project Skills and Hooks are resolved together with user-level entries.</p>
    </div>
  );
}
