import { FolderInput, Power, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";

import type { ConfigScope, Project } from "../../generated/app-server";
import type { DesktopRuntime } from "../../rpc/contracts";
import styles from "../management/ManagementWorkspace.module.css";
import { MarkdownContent } from "../thread/MarkdownContent";
import { useSkillManagement } from "./useSkillManagement";

interface SkillsPageProps {
  runtime: DesktopRuntime;
  project: Project | null;
}

export function SkillsPage({ runtime, project }: SkillsPageProps) {
  const catalog = useSkillManagement(runtime, project?.id ?? null);
  const [scope, setScope] = useState<ConfigScope>("project");

  const importSkill = async () => {
    const path = await runtime.pickDirectory();
    if (path) await catalog.importSkill(path, scope);
  };

  return (
    <section className={styles.page} aria-labelledby="skills-title">
      <header className={styles.pageHeader}>
        <div>
          <p className={styles.eyebrow}>Reusable workflows</p>
          <h1 id="skills-title">Skills</h1>
          <p>
            Import, inspect, and enable the same project or user Skills used by
            Desktop and CLI turns.
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
                    <span
                      className={styles.badge}
                      data-status={catalog.selectedSkill.status}
                    >
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
                        {catalog.selectedSkill.allowedTools.join(", ") ||
                          "Not declared"}
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
                  Select a Skill to inspect its exact instructions, source,
                  status, and revision.
                </p>
              )}
            </article>
          </div>
        </>
      )}
    </section>
  );
}

function EmptyProject() {
  return (
    <div className={styles.emptyState}>
      <h2>Open a project to manage its Skills.</h2>
      <p>Project and user Skills are resolved through the same Agent runtime.</p>
    </div>
  );
}
