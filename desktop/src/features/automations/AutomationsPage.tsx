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

import type {
  Automation,
  AutomationScheduleKind,
  Project,
  Thread,
} from "../../generated/app-server";
import { confirmAction } from "../../platform/confirmAction";
import type { DesktopRuntime } from "../../rpc/contracts";
import styles from "../management/ManagementWorkspace.module.css";
import { useAutomations } from "./useAutomations";

interface AutomationsPageProps {
  runtime: DesktopRuntime;
  project: Project | null;
  onThreadCreated(thread: Thread): void;
  onOpenThread(threadId: string): void;
}

interface AutomationDraft {
  id: string | null;
  name: string;
  prompt: string;
  scheduleKind: AutomationScheduleKind;
  intervalMinutes: string;
  enabled: boolean;
}

const emptyDraft: AutomationDraft = {
  id: null,
  name: "",
  prompt: "",
  scheduleKind: "manual",
  intervalMinutes: "60",
  enabled: true,
};

export function AutomationsPage({
  runtime,
  project,
  onThreadCreated,
  onOpenThread,
}: AutomationsPageProps) {
  const automations = useAutomations(runtime, project?.id ?? null);
  const [draft, setDraft] = useState<AutomationDraft | null>(null);
  const [expandedRuns, setExpandedRuns] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
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
    setDraft({
      id: automation.id,
      name: automation.name,
      prompt: automation.prompt,
      scheduleKind: automation.scheduleKind,
      intervalMinutes: String((automation.intervalSeconds ?? 3600) / 60),
      enabled: automation.status === "enabled",
    });
  };

  const save = async () => {
    if (!draft || !project) return;
    setFormError(null);
    const intervalSeconds =
      draft.scheduleKind === "interval"
        ? Number(draft.intervalMinutes) * 60
        : undefined;
    if (
      intervalSeconds !== undefined &&
      (!Number.isSafeInteger(intervalSeconds) || intervalSeconds < 60)
    ) {
      setFormError("Interval must be a whole number of minutes.");
      return;
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
        `Remove the automation “${automation.name}”? Its Goal Thread and Session history will be kept.`,
        {
          confirmLabel: "Remove automation",
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
          <p className={styles.eyebrow}>Recurring local work</p>
          <h1 id="automations-title">Automations</h1>
          <p>
            Each automation owns a canonical Goal Thread and submits ordinary
            Agent Turns through the same permission, approval, Hook, and Session
            lifecycle as interactive work.
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
            Refresh
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
            New automation
          </button>
        </div>
      </header>

      {!project ? (
        <div className={styles.emptyState}>
          <h2>Open a project to create an automation.</h2>
          <p>Automations are always fenced to one trusted local project.</p>
        </div>
      ) : (
        <>
          <div className={styles.contextBar}>
            <strong>{project.displayName}</strong>
            <span>
              {automations.inventory?.schedulerActive
                ? "Scheduler active · runs while DeepCode Desktop is open"
                : "Scheduler unavailable · scheduled work will not start"}
            </span>
          </div>
          {!canExecute ? (
            <p className={styles.warningBlock}>
              Trust this project before creating or running unattended work.
            </p>
          ) : null}
          {automations.error ? (
            <p className={styles.errorBanner}>{automations.error}</p>
          ) : null}

          <div className={styles.cardList}>
            {(automations.inventory?.automations ?? []).map((automation) => {
              const latest = latestRuns.get(automation.id);
              const runHistory = automations.runs[automation.id] ?? [];
              return (
                <article className={styles.card} key={automation.id}>
                  <header>
                    <div>
                      <p className={styles.eyebrow}>
                        {scheduleLabel(automation)}
                      </p>
                      <h2>{automation.name}</h2>
                    </div>
                    <span
                      className={styles.badge}
                      data-status={latest?.status ?? automation.status}
                    >
                      {latest?.status ?? automation.status}
                    </span>
                  </header>
                  <p>{automation.prompt}</p>
                  <dl className={styles.metadata}>
                    <div>
                      <dt>Next run</dt>
                      <dd>{dateLabel(automation.nextRunAt, "Manual only")}</dd>
                    </div>
                    <div>
                      <dt>Last run</dt>
                      <dd>{dateLabel(automation.lastRunAt, "Not run yet")}</dd>
                    </div>
                    <div>
                      <dt>Goal Thread</dt>
                      <dd>{automation.threadId}</dd>
                    </div>
                  </dl>
                  <footer className={styles.cardActions}>
                    <button
                      type="button"
                      disabled={automations.loading || !canExecute}
                      onClick={() => void automations.runNow(automation.id)}
                    >
                      <Play size={13} />
                      Run now
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
                          <Pause size={13} />
                        ) : (
                          <Play size={13} />
                        )}
                        {automation.status === "enabled" ? "Pause" : "Resume"}
                      </button>
                    ) : null}
                    <button type="button" onClick={() => edit(automation)}>
                      Edit
                    </button>
                    <button
                      type="button"
                      onClick={() => onOpenThread(automation.threadId)}
                    >
                      Open Thread
                    </button>
                    <button
                      type="button"
                      onClick={() => void toggleRuns(automation.id)}
                    >
                      <History size={13} />
                      Runs
                    </button>
                    <button
                      type="button"
                      disabled={Boolean(latest && !isTerminal(latest.status))}
                      onClick={() => void remove(automation)}
                    >
                      <Trash2 size={13} />
                      Remove
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
                                {dateLabel(run.scheduledFor, "Unknown time")}
                                {run.detail ? ` · ${run.detail}` : ""}
                              </small>
                            </span>
                          </div>
                        ))
                      ) : (
                        <p className={styles.emptyCopy}>No runs recorded yet.</p>
                      )}
                    </div>
                  ) : null}
                </article>
              );
            })}
            {!automations.loading &&
            !(automations.inventory?.automations.length ?? 0) ? (
              <p className={styles.emptyCopy}>
                No automations exist for this project.
              </p>
            ) : null}
          </div>

          {draft ? (
            <section className={styles.formCard} aria-label="Automation editor">
              <header>
                <div>
                  <p className={styles.eyebrow}>
                    {draft.id ? "Edit automation" : "New automation"}
                  </p>
                  <h2>Goal and schedule</h2>
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
                    onChange={(event) =>
                      setDraft({ ...draft, name: event.target.value })
                    }
                  />
                </label>
                <label>
                  Schedule
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
                    <option value="manual">Manual only</option>
                    <option value="interval">Recurring interval</option>
                  </select>
                </label>
                {draft.scheduleKind === "interval" ? (
                  <>
                    <label>
                      Interval minutes
                      <input
                        inputMode="numeric"
                        value={draft.intervalMinutes}
                        onChange={(event) =>
                          setDraft({
                            ...draft,
                            intervalMinutes: event.target.value,
                          })
                        }
                      />
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
                      Enable recurring runs
                    </label>
                  </>
                ) : null}
                <label className={styles.wideField}>
                  Goal prompt
                  <textarea
                    rows={7}
                    value={draft.prompt}
                    onChange={(event) =>
                      setDraft({ ...draft, prompt: event.target.value })
                    }
                    placeholder="Describe the recurring repository task and its verification criteria."
                  />
                </label>
              </div>
              {formError ? (
                <p className={styles.errorBanner}>{formError}</p>
              ) : null}
              <footer className={styles.formActions}>
                <span>
                  Scheduled runs are coalesced after downtime and never queue
                  behind an already active Goal Turn.
                </span>
                <button type="button" onClick={() => setDraft(null)}>
                  Cancel
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
                  Save automation
                </button>
              </footer>
            </section>
          ) : null}
        </>
      )}
    </section>
  );
}

function scheduleLabel(automation: Automation): string {
  if (automation.scheduleKind === "manual") return "Manual Goal";
  const minutes = (automation.intervalSeconds ?? 60) / 60;
  return `Every ${minutes} minute${minutes === 1 ? "" : "s"}`;
}

function dateLabel(value: string | null, fallback: string): string {
  if (!value) return fallback;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? fallback : date.toLocaleString();
}

function isTerminal(status: string): boolean {
  return ["completed", "failed", "interrupted", "skipped"].includes(status);
}
