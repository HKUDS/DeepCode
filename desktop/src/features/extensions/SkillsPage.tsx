import { FolderInput, Power, RefreshCw, Sparkles, Trash2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  ConfigScope,
  Project,
  SkillInfo,
} from "../../generated/app-server";
import { confirmAction } from "../../platform/confirmAction";
import type { ClientRuntime } from "../../rpc/contracts";
import styles from "../management/ManagementWorkspace.module.css";
import { MarkdownContent } from "../thread/MarkdownContent";
import { configScopeLabel } from "../../app/configScope";
import { skillOriginLabel, skillStatusLabel } from "./skillPresentation";
import { useSkillManagement } from "./useSkillManagement";

interface SkillsPageProps {
  runtime: ClientRuntime;
  project: Project | null;
  onCreateSkill(skill: SkillInfo): Promise<void>;
}

export function SkillsPage({ runtime, project, onCreateSkill }: SkillsPageProps) {
  const { t } = useTranslation();
  const catalog = useSkillManagement(runtime, project?.id ?? null);
  const [scope, setScope] = useState<ConfigScope>("project");
  const [creating, setCreating] = useState(false);
  const creator = catalog.authoringSkillId
    ? catalog.skills.find(
        (skill) =>
          skill.id === catalog.authoringSkillId &&
          skill.selectable &&
          skill.enabled,
      )
    : undefined;

  const createSkill = async () => {
    if (!creator || creating) return;
    setCreating(true);
    try {
      await onCreateSkill(creator);
    } finally {
      setCreating(false);
    }
  };

  const importSkill = async () => {
    const path = await runtime.pickDirectory();
    if (path) await catalog.importSkill(path, scope);
  };

  return (
    <section className={styles.page} aria-labelledby="skills-title">
      <header className={styles.pageHeader}>
        <div>
          <h1 id="skills-title">{t("skills.title", "Skills")}</h1>
          <p>
            {t(
              "skills.description",
              "Import, inspect, and enable the same project or user Skills used by Desktop and CLI turns.",
            )}
          </p>
        </div>
        <div className={styles.formActions}>
          <label className={styles.compactSelect}>
            <span>{t("skills.storeChangesIn", "Store changes in")}</span>
            <select
              aria-label={t("skills.scopeLabel", "Skill configuration scope")}
              title={t(
                "skills.scopeTitle",
                "Controls the import destination and enablement policy layer",
              )}
              value={scope}
              onChange={(event) => setScope(event.target.value as ConfigScope)}
            >
              <option value="project">
                {configScopeLabel("project", t)}
              </option>
              <option value="user">{configScopeLabel("user", t)}</option>
            </select>
          </label>
          <button
            className={styles.secondaryButton}
            type="button"
            disabled={!project || catalog.loading}
            onClick={() => void catalog.refresh()}
          >
            <RefreshCw size={14} />
            {t("skills.reload", "Reload")}
          </button>
          <button
            className={styles.secondaryButton}
            type="button"
            disabled={!project || !creator || catalog.loading || creating}
            onClick={() => void createSkill()}
          >
            <Sparkles size={14} />
            {creating
              ? t("skills.opening", "Opening…")
              : t("skills.create", "Create Skill")}
          </button>
          <button
            className={styles.primaryButton}
            type="button"
            disabled={!project || catalog.loading}
            onClick={() => void importSkill()}
          >
            <FolderInput size={14} />
            {t("skills.import", "Import folder")}
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
              <summary>
                {t("skills.warningsSummary", "{{total}} discovery warning(s)", {
                  total: catalog.warnings.length,
                })}
              </summary>
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
                      {skillOriginLabel(skill, t)}
                      <em data-status={skill.status}>{skillStatusLabel(skill.status, t)}</em>
                    </span>
                    <strong>
                      {skill.displayName ??
                        skill.name ??
                        t("skills.invalid", "Invalid Skill")}
                    </strong>
                    <small>
                      {skill.shortDescription ?? skill.description ?? skill.error}
                    </small>
                  </button>
                ))
              ) : (
                <p className={styles.emptyCopy}>
                  {t(
                    "skills.empty",
                    "No Skills yet. Import a folder containing a valid SKILL.md or create a reusable workflow.",
                  )}
                </p>
              )}
            </div>
            <article className={styles.detailPane}>
              {catalog.selectedSkill ? (
                <>
                  <p className={styles.eyebrow}>
                    {skillOriginLabel(catalog.selectedSkill, t)}
                  </p>
                  <h2>
                    {catalog.selectedSkill.displayName ??
                      catalog.selectedSkill.name}
                  </h2>
                  <p>
                    {catalog.selectedSkill.shortDescription ??
                      catalog.selectedSkill.description}
                  </p>
                  <div className={styles.skillActions}>
                    <span
                      className={styles.badge}
                      data-status={catalog.selectedSkill.status}
                    >
                      {skillStatusLabel(catalog.selectedSkill.status, t)}
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
                      disabled={
                        catalog.loading ||
                        !catalog.selectedSkill.configurableScopes.includes(scope)
                      }
                    >
                      <Power size={14} />
                      {catalog.selectedSkill.enabled
                        ? t("skills.disable", "Disable")
                        : t("skills.enable", "Enable")}
                    </button>
                    {catalog.selectedSkill.deletable ? (
                      <button
                        type="button"
                        className={styles.dangerButton}
                        disabled={catalog.loading}
                        onClick={() => {
                          const selected = catalog.selectedSkill;
                          if (!selected) return;
                          void (async () => {
                            if (
                              await confirmAction(
                                t(
                                  "skills.deleteConfirm",
                                  "Delete the managed Skill “{{name}}”?",
                                  { name: selected.name },
                                ),
                                {
                                  destructive: true,
                                  confirmLabel: t("skills.delete", "Delete"),
                                },
                              )
                            ) {
                              await catalog.deleteSkill(selected.id);
                            }
                          })();
                        }}
                      >
                        <Trash2 size={14} />
                        {t("skills.delete", "Delete")}
                      </button>
                    ) : null}
                  </div>
                  <dl className={styles.metadata}>
                    <div>
                      <dt>{t("skills.detail.location", "Location")}</dt>
                      <dd>{catalog.selectedSkill.location}</dd>
                    </div>
                    <div>
                      <dt>{t("skills.detail.intendedTools", "Intended tools")}</dt>
                      <dd>
                        {catalog.selectedSkill.allowedTools.join(", ") ||
                          t("skills.detail.notDeclared", "Not declared")}
                      </dd>
                    </div>
                    <div>
                      <dt>{t("skills.detail.invocation", "Invocation")}</dt>
                      <dd>
                        {catalog.selectedSkill.allowImplicitInvocation
                          ? t("skills.detail.invocationAutomatic", "Explicit or automatic")
                          : t("skills.detail.invocationExplicit", "Explicit selection only")}
                      </dd>
                    </div>
                    <div>
                      <dt>{t("skills.detail.revision", "Revision")}</dt>
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
                      {t(
                        "skills.detail.truncated",
                        "Instructions were truncated at the safe preview limit.",
                      )}
                    </p>
                  ) : null}
                </>
              ) : (
                <p className={styles.emptyCopy}>
                  {t(
                    "skills.selectHint",
                    "Select a Skill to inspect its exact instructions, source, status, and revision.",
                  )}
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
  const { t } = useTranslation();
  return (
    <div className={styles.emptyState}>
      <h2>{t("skills.noProject.title", "Open a project to manage its Skills.")}</h2>
      <p>
        {t(
          "skills.noProject.hint",
          "Project and user Skills are resolved through the same Agent runtime.",
        )}
      </p>
    </div>
  );
}
