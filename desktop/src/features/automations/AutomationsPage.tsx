import {
  Clock3,
  History,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";

import type {
  Automation,
  AutomationScheduleKind,
  Project,
  Thread,
} from "../../generated/app-server";
import { confirmAction } from "../../platform/confirmAction";
import type { ClientRuntime } from "../../rpc/contracts";
import styles from "../management/ManagementWorkspace.module.css";
import {
  automationIntervalInput,
  automationIntervalLabel,
  automationIntervalSeconds,
  type AutomationIntervalUnit,
} from "./automationInterval";
import { useAutomations } from "./useAutomations";

interface AutomationsPageProps {
  runtime: ClientRuntime;
  project: Project | null;
  onThreadCreated(thread: Thread): void;
  onOpenThread(threadId: string): void;
}

interface AutomationDraft {
  id: string | null;
  name: string;
  prompt: string;
  scheduleKind: AutomationScheduleKind;
  intervalValue: string;
  intervalUnit: AutomationIntervalUnit;
  enabled: boolean;
}

const emptyDraft: AutomationDraft = {
  id: null,
  name: "",
  prompt: "",
  scheduleKind: "manual",
  intervalValue: "1",
  intervalUnit: "hours",
  enabled: true,
};

// automationIntervalSeconds reports these in English; the page shows them.
const INTERVAL_ERROR_KEYS: Record<string, string> = {
  "Interval value must be a positive whole number.":
    "automations.interval.wholeNumber",
  "Interval is too large.": "automations.interval.tooLarge",
  "Interval must be at least 60 seconds.": "automations.interval.tooShort",
  "Interval must not exceed 366 days.": "automations.interval.tooLong",
};

export function AutomationsPage({
  runtime,
  project,
  onThreadCreated,
  onOpenThread,
}: AutomationsPageProps) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<AutomationDraft | null>(null);
  const [expandedRuns, setExpandedRuns] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const automations = useAutomations(
    runtime,
    project?.id ?? null,
    expandedRuns,
  );
  const canExecute = project?.trustState === "trusted";
  const latestRuns = useMemo(
    () =>
      new Map(
        (automations.inventory?.latestRuns ?? []).map((run) => [
          run.automationId,
          run,
        ]),
      ),
    [automations.inventory?.latestRuns],
  );

  const edit = (automation: Automation) => {
    setFormError(null);
    const interval = automationIntervalInput(automation.intervalSeconds);
    setDraft({
      id: automation.id,
      name: automation.name,
      prompt: automation.prompt,
      scheduleKind: automation.scheduleKind,
      intervalValue: interval.value,
      intervalUnit: interval.unit,
      enabled: automation.status === "enabled",
    });
  };

  const save = async () => {
    if (!draft || !project) return;
    setFormError(null);
    let intervalSeconds: number | undefined;
    if (draft.scheduleKind === "interval") {
      const interval = automationIntervalSeconds({
        value: draft.intervalValue,
        unit: draft.intervalUnit,
      });
      if (interval.error !== null) {
        const key = INTERVAL_ERROR_KEYS[interval.error];
        setFormError(key ? t(key, interval.error) : interval.error);
        return;
      }
      intervalSeconds = interval.intervalSeconds;
    }
    if (draft.id) {
      const updated = await automations.update({
        automationId: draft.id,
        name: draft.name.trim(),
        prompt: draft.prompt.trim(),
        scheduleKind: draft.scheduleKind,
        ...(intervalSeconds !== undefined ? { intervalSeconds } : {}),
        status:
          draft.scheduleKind === "interval" && !draft.enabled
            ? "paused"
            : "enabled",
      });
      if (updated) setDraft(null);
      return;
    }
    const created = await automations.create({
      name: draft.name.trim(),
      prompt: draft.prompt.trim(),
      scheduleKind: draft.scheduleKind,
      ...(intervalSeconds !== undefined ? { intervalSeconds } : {}),
      enabled: draft.scheduleKind !== "interval" || draft.enabled,
    });
    if (created) {
      onThreadCreated(created.thread);
      setDraft(null);
    }
  };

  const toggleRuns = async (automationId: string) => {
    if (expandedRuns === automationId) {
      setExpandedRuns(null);
      return;
    }
    setExpandedRuns(automationId);
    await automations.loadRuns(automationId);
  };

  const remove = async (automation: Automation) => {
    if (
      !(await confirmAction(
        t(
          "automations.removeConfirm",
          "Remove the automation “{{name}}”? Its Goal Thread and Session history will be kept.",
          { name: automation.name },
        ),
        {
          confirmLabel: t("automations.removeAutomation", "Remove automation"),
        },
      ))
    ) {
      return;
    }
    if (await automations.remove(automation.id)) {
      if (draft?.id === automation.id) setDraft(null);
      if (expandedRuns === automation.id) setExpandedRuns(null);
    }
  };

  return (
    <section className={styles.page} aria-labelledby="automations-title">
      <header className={styles.pageHeader}>
        <div>
          <p className={styles.eyebrow}>
            {t("automations.eyebrow", "Recurring local work")}
          </p>
          <h1 id="automations-title">{t("automations.title", "Automations")}</h1>
          <p>
            {t(
              "automations.description",
              "Each automation owns a canonical Goal Thread and submits ordinary Agent Turns through the same permission, approval, Hook, and Session lifecycle as interactive work.",
            )}
          </p>
        </div>
        <div className={styles.headerActions}>
          <button
            className={styles.secondaryButton}
            type="button"
            disabled={!project || automations.loading}
            onClick={() => void automations.refresh()}
          >
            <RefreshCw size={14} />
            {t("automations.refresh", "Refresh")}
          </button>
          <button
            className={styles.primaryButton}
            type="button"
            disabled={!project || !canExecute}
            onClick={() => {
              setFormError(null);
              setDraft({ ...emptyDraft });
            }}
          >
            <Plus size={14} />
            {t("automations.newAutomation", "New automation")}
          </button>
        </div>
      </header>

      {!project ? (
        <div className={styles.emptyState}>
          <h2>
            {t(
              "automations.noProject",
              "Open a project to create an automation.",
            )}
          </h2>
          <p>
            {t(
              "automations.noProjectHint",
              "Automations are always fenced to one trusted local project.",
            )}
          </p>
        </div>
      ) : (
        <>
          <div className={styles.contextBar}>
            <strong>{project.displayName}</strong>
            <span>
              {automations.inventory?.schedulerActive
                ? t(
                    "automations.schedulerActive",
                    "Scheduler active · scheduled work runs while a compatible DeepCode runtime is active",
                  )
                : t(
                    "automations.schedulerUnavailable",
                    "Scheduler unavailable · start a scheduler-enabled DeepCode runtime to run scheduled work",
                  )}
            </span>
          </div>
          {!canExecute ? (
            <p className={styles.warningBlock}>
              {t(
                "automations.trustRequired",
                "Trust this project before creating or running unattended work.",
              )}
            </p>
          ) : null}
          {automations.error ? (
            <p className={styles.errorBanner}>{automations.error}</p>
          ) : null}

          <div className={styles.cardList}>
            {(automations.inventory?.automations ?? []).map((automation) => {
              const latest = latestRuns.get(automation.id);
              const runPage = automations.runs[automation.id];
              const runHistory = runPage?.runs ?? [];
              return (
                <article className={styles.card} key={automation.id}>
                  <header>
                    <div>
                      <p className={styles.eyebrow}>
                        {scheduleLabel(automation, t)}
                      </p>
                      <h2>{automation.name}</h2>
                    </div>
                    <div className={styles.headerActions}>
                      <span
                        className={styles.badge}
                        data-status={automation.status}
                      >
                        {t("automations.status", "Status: {{status}}", {
                          status: automation.status,
                        })}
                      </span>
                      {latest ? (
                        <span
                          className={styles.badge}
                          data-status={latest.status}
                        >
                          {t("automations.latestRun", "Latest run: {{status}}", {
                            status: latest.status,
                          })}
                        </span>
                      ) : null}
                    </div>
                  </header>
                  <p>{automation.prompt}</p>
                  <dl className={styles.metadata}>
                    <div>
                      <dt>{t("automations.nextRun", "Next run")}</dt>
                      <dd>
                        {dateLabel(
                          automation.nextRunAt,
                          t("automations.manualOnly", "Manual only"),
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt>{t("automations.lastRun", "Last run")}</dt>
                      <dd>
                        {dateLabel(
                          automation.lastRunAt,
                          t("automations.notRunYet", "Not run yet"),
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt>{t("automations.goalThread", "Goal Thread")}</dt>
                      <dd>{automation.threadId}</dd>
                    </div>
                  </dl>
                  <footer className={styles.cardActions}>
                    <button
                      type="button"
                      disabled={automations.loading || !canExecute}
                      onClick={() => void automations.runNow(automation.id)}
                    >
                      <Play size={14} />
                      {t("automations.runNow", "Run now")}
                    </button>
                    {automation.scheduleKind === "interval" ? (
                      <button
                        type="button"
                        disabled={automations.loading}
                        onClick={() =>
                          void automations.update({
                            automationId: automation.id,
                            status:
                              automation.status === "enabled"
                                ? "paused"
                                : "enabled",
                          })
                        }
                      >
                        {automation.status === "enabled" ? (
                          <Pause size={14} />
                        ) : (
                          <Play size={14} />
                        )}
                        {automation.status === "enabled"
                          ? t("automations.pause", "Pause")
                          : t("automations.resume", "Resume")}
                      </button>
                    ) : null}
                    <button type="button" onClick={() => edit(automation)}>
                      {t("automations.edit", "Edit")}
                    </button>
                    <button
                      type="button"
                      onClick={() => onOpenThread(automation.threadId)}
                    >
                      {t("automations.openThread", "Open Thread")}
                    </button>
                    <button
                      type="button"
                      disabled={automations.loadingRunsFor === automation.id}
                      onClick={() => void toggleRuns(automation.id)}
                    >
                      <History size={14} />
                      {t("automations.runs", "Runs")}
                    </button>
                    <button
                      type="button"
                      disabled={Boolean(latest && !isTerminal(latest.status))}
                      onClick={() => void remove(automation)}
                    >
                      <Trash2 size={14} />
                      {t("automations.remove", "Remove")}
                    </button>
                  </footer>
                  {expandedRuns === automation.id ? (
                    <div className={styles.runList}>
                      {runHistory.length ? (
                        runHistory.map((run) => (
                          <div key={run.id} data-status={run.status}>
                            <Clock3 size={12} />
                            <span>
                              <strong>{run.status}</strong>
                              <small>
                                {dateLabel(
                                  run.scheduledFor,
                                  t("automations.unknownTime", "Unknown time"),
                                )}
                                {run.detail ? ` · ${run.detail}` : ""}
                              </small>
                            </span>
                          </div>
                        ))
                      ) : (
                        <p className={styles.emptyCopy}>
                          {t("automations.noRuns", "No runs recorded yet.")}
                        </p>
                      )}
                      {runPage?.hasMore ? (
                        <footer className={styles.cardActions}>
                          <button
                            type="button"
                            disabled={
                              automations.loadingRunsFor === automation.id
                            }
                            onClick={() =>
                              void automations.loadMoreRuns(automation.id)
                            }
                          >
                            {t("automations.loadMoreRuns", "Load more runs")}
                          </button>
                        </footer>
                      ) : null}
                    </div>
                  ) : null}
                </article>
              );
            })}
            {!automations.loading &&
            !(automations.inventory?.automations.length ?? 0) ? (
              <p className={styles.emptyCopy}>
                {t(
                  "automations.empty",
                  "No automations exist for this project.",
                )}
              </p>
            ) : null}
          </div>
          {automations.inventory?.hasMore ? (
            <footer className={styles.formActions}>
              <span>
                {t(
                  "automations.showing",
                  "Showing {{shown}} automations. More are available.",
                  { shown: automations.inventory.automations.length },
                )}
              </span>
              <button
                type="button"
                disabled={automations.loadingMoreAutomations}
                onClick={() => void automations.loadMoreAutomations()}
              >
                {t("automations.loadMore", "Load more automations")}
              </button>
            </footer>
          ) : null}

          {draft ? (
            <section
              className={styles.formCard}
              aria-label={t("automations.form.label", "Automation editor")}
            >
              <header>
                <div>
                  <p className={styles.eyebrow}>
                    {draft.id
                      ? t("automations.form.edit", "Edit automation")
                      : t("automations.newAutomation", "New automation")}
                  </p>
                  <h2>{t("automations.form.title", "Goal and schedule")}</h2>
                </div>
                <button type="button" onClick={() => setDraft(null)}>
                  {t("automations.form.cancel", "Cancel")}
                </button>
              </header>
              <div className={styles.formGrid}>
                <label>
                  {t("automations.form.name", "Name")}
                  <input
                    value={draft.name}
                    onChange={(event) =>
                      setDraft({ ...draft, name: event.target.value })
                    }
                  />
                </label>
                <label>
                  {t("automations.form.schedule", "Schedule")}
                  <select
                    value={draft.scheduleKind}
                    onChange={(event) =>
                      setDraft({
                        ...draft,
                        scheduleKind: event.target
                          .value as AutomationScheduleKind,
                      })
                    }
                  >
                    <option value="manual">
                      {t("automations.manualOnly", "Manual only")}
                    </option>
                    <option value="interval">
                      {t("automations.form.interval", "Recurring interval")}
                    </option>
                  </select>
                </label>
                {draft.scheduleKind === "interval" ? (
                  <>
                    <label>
                      {t("automations.form.repeatEvery", "Repeat every")}
                      <input
                        inputMode="numeric"
                        value={draft.intervalValue}
                        onChange={(event) =>
                          setDraft({
                            ...draft,
                            intervalValue: event.target.value,
                          })
                        }
                      />
                    </label>
                    <label>
                      {t("automations.form.unit", "Unit")}
                      <select
                        value={draft.intervalUnit}
                        onChange={(event) =>
                          setDraft({
                            ...draft,
                            intervalUnit: event.target
                              .value as AutomationIntervalUnit,
                          })
                        }
                      >
                        <option value="seconds">
                          {t("automations.form.seconds", "Seconds")}
                        </option>
                        <option value="minutes">
                          {t("automations.form.minutes", "Minutes")}
                        </option>
                        <option value="hours">
                          {t("automations.form.hours", "Hours")}
                        </option>
                      </select>
                    </label>
                    <label className={styles.checkboxField}>
                      <input
                        type="checkbox"
                        checked={draft.enabled}
                        onChange={(event) =>
                          setDraft({
                            ...draft,
                            enabled: event.target.checked,
                          })
                        }
                      />
                      {t("automations.form.enable", "Enable recurring runs")}
                    </label>
                  </>
                ) : null}
                <label className={styles.wideField}>
                  {t("automations.form.prompt", "Goal prompt")}
                  <textarea
                    rows={7}
                    value={draft.prompt}
                    onChange={(event) =>
                      setDraft({ ...draft, prompt: event.target.value })
                    }
                    placeholder={t(
                      "automations.form.promptPlaceholder",
                      "Describe the recurring repository task and its verification criteria.",
                    )}
                  />
                </label>
              </div>
              {formError ? (
                <p className={styles.errorBanner}>{formError}</p>
              ) : null}
              <footer className={styles.formActions}>
                <span>
                  {t(
                    "automations.form.note",
                    "Scheduled runs are coalesced after downtime and never queue behind an already active Goal Turn.",
                  )}
                </span>
                <button type="button" onClick={() => setDraft(null)}>
                  {t("automations.form.cancel", "Cancel")}
                </button>
                <button
                  className={styles.primaryButton}
                  type="button"
                  disabled={
                    automations.loading ||
                    !canExecute ||
                    !draft.name.trim() ||
                    !draft.prompt.trim()
                  }
                  onClick={() => void save()}
                >
                  {t("automations.form.save", "Save automation")}
                </button>
              </footer>
            </section>
          ) : null}
        </>
      )}
    </section>
  );
}

function scheduleLabel(automation: Automation, t: TFunction): string {
  if (automation.scheduleKind === "manual") {
    return t("automations.manualGoal", "Manual Goal");
  }
  const { value, unit } = automationIntervalInput(automation.intervalSeconds);
  return t(
    `automations.every.${unit}`,
    `Every ${automationIntervalLabel(automation.intervalSeconds)}`,
    { value },
  );
}

function dateLabel(value: string | null, fallback: string): string {
  if (!value) return fallback;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? fallback : date.toLocaleString();
}

function isTerminal(status: string): boolean {
  return ["completed", "failed", "interrupted", "skipped"].includes(status);
}
