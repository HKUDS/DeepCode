import { RefreshCw } from "lucide-react";
import { useState } from "react";

import type { Project } from "../../generated/app-server";
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

  return (
    <section className={styles.page} aria-labelledby="extensions-title">
      <header className={styles.pageHeader}>
        <div>
          <p className={styles.eyebrow}>Agent extensions</p>
          <h1 id="extensions-title">Skills &amp; Hooks</h1>
          <p>
            The same project and user extensions discovered when an Agent Session
            starts.
          </p>
        </div>
        <button
          className={styles.secondaryButton}
          type="button"
          disabled={!project || catalog.loading}
          onClick={() => void catalog.refresh()}
        >
          <RefreshCw size={14} />
          Refresh
        </button>
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
                      key={`${skill.source}:${skill.name}`}
                      data-active={catalog.selectedSkill?.name === skill.name}
                      onClick={() => void catalog.selectSkill(skill.name)}
                    >
                      <span>{skill.source.replace(":", " · ")}</span>
                      <strong>{skill.name}</strong>
                      <small>{skill.description}</small>
                    </button>
                  ))
                ) : (
                  <p className={styles.emptyCopy}>
                    No SKILL.md capabilities were discovered for this project.
                  </p>
                )}
              </div>
              <article className={styles.detailPane}>
                {catalog.selectedSkill ? (
                  <>
                    <p className={styles.eyebrow}>
                      {catalog.selectedSkill.source}
                    </p>
                    <h2>{catalog.selectedSkill.name}</h2>
                    <p>{catalog.selectedSkill.description}</p>
                    <dl className={styles.metadata}>
                      <div>
                        <dt>Directory</dt>
                        <dd>{catalog.selectedSkill.directory}</dd>
                      </div>
                      <div>
                        <dt>Intended tools</dt>
                        <dd>
                          {catalog.selectedSkill.allowedTools.join(", ") || "Not declared"}
                        </dd>
                      </div>
                    </dl>
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
                    Select a Skill to inspect the instructions the Agent can load.
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
