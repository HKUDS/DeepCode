import {
  Check,
  ChevronDown,
  ChevronUp,
  Pause,
  Play,
  Sparkles,
  Target,
  Trash2,
  X,
} from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  Goal,
  GoalOutcome,
  SkillInfo,
  Turn,
} from "../../generated/app-server";
import type { GoalDefinitionInput } from "../../app/useWorkspaceController";
import { deriveGoalPresentation } from "./goalPresentation";
import styles from "./GoalRail.module.css";

interface GoalRailProps {
  goal: Goal | null;
  outcome: GoalOutcome | null;
  turns: readonly Turn[];
  enabled: boolean;
  busy: boolean;
  skills: SkillInfo[];
  onSet(input: GoalDefinitionInput): Promise<void>;
  onPause(): Promise<void>;
  onResume(): Promise<void>;
  onContinue(): Promise<void>;
  onClear(): Promise<void>;
  onSelectEvidence(itemId: string): void;
}

const MAX_GOAL_SKILLS = 8;

export function GoalRail({
  goal,
  outcome,
  turns,
  enabled,
  busy,
  skills,
  onSet,
  onPause,
  onResume,
  onContinue,
  onClear,
  onSelectEvidence,
}: GoalRailProps) {
  const [editorOpen, setEditorOpen] = useState(false);
  const [resumeAfterSave, setResumeAfterSave] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [objective, setObjective] = useState("");
  const [tokenBudget, setTokenBudget] = useState("");
  const [skillIds, setSkillIds] = useState<string[]>([]);
  const { t } = useTranslation();
  const selectableSkills = useMemo(
    () => skills.filter((skill) => skill.selectable && skill.enabled),
    [skills],
  );
  const presentation = useMemo(
    () =>
      goal
        ? deriveGoalPresentation(
            goal,
            turns,
            outcome?.decidedByTurnId ?? null,
          )
        : null,
    [goal, outcome?.decidedByTurnId, turns],
  );

  const openEditor = (resume = false) => {
    setResumeAfterSave(resume);
    setObjective(goal?.objective ?? "");
    setTokenBudget(goal?.tokenBudget ? String(goal.tokenBudget) : "");
    setSkillIds(goal ? [...goal.skillIds] : []);
    setEditorOpen(true);
  };

  const submit = async () => {
    const cleanObjective = objective.trim();
    if (!cleanObjective || busy || !enabled) return;
    const parsedBudget = tokenBudget.trim()
      ? Number.parseInt(tokenBudget.trim(), 10)
      : null;
    if (parsedBudget !== null && (!Number.isSafeInteger(parsedBudget) || parsedBudget < 1)) {
      return;
    }
    await onSet({
      objective: cleanObjective,
      tokenBudget: parsedBudget,
      skillIds,
      resume: resumeAfterSave,
    });
    setEditorOpen(false);
    setExpanded(false);
  };

  if (!goal && !editorOpen) {
    return (
      <button
        className={styles.emptyRail}
        type="button"
        onClick={() => openEditor()}
        disabled={!enabled || busy}
      >
        <Target size={14} />
        <span>{t("goal.setGoal", "Set a Goal")}</span>
        <small>{t("goal.setGoalHint", "Keep a durable outcome across ordinary Turns")}</small>
      </button>
    );
  }

  return (
    <section
      className={styles.rail}
      data-status={presentation?.status ?? "draft"}
      aria-label={t("goal.sessionGoal", "Session Goal")}
    >
      {goal ? (
        <>
          <button
            className={styles.summary}
            type="button"
            onClick={() => setExpanded((value) => !value)}
            aria-expanded={expanded}
          >
            <span className={styles.statusMark}>
              <Target size={12} />
            </span>
            <span className={styles.summaryText}>
              <strong>{goal.objective}</strong>
              <small>
                <span>{presentation?.label}</span>
                <i>·</i>
                {presentation?.linkedTurnCount ?? 0}{" "}
                {presentation?.linkedTurnCount === 1 ? t("goal.turn", "Turn") : t("goal.turns", "Turns")}
                {goal.tokensUsed > 0 ? (
                  <>
                    <i>·</i>
                    {compactNumber(goal.tokensUsed)} {t("goal.tokens", "tokens")}
                  </>
                ) : null}
              </small>
            </span>
            {goal.tokenBudget ? (
              <progress
                max={goal.tokenBudget}
                value={Math.min(goal.tokensUsed, goal.tokenBudget)}
                aria-label={`${goal.tokensUsed} of ${goal.tokenBudget} Goal tokens`}
              />
            ) : (
              <span />
            )}
            {expanded ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
          </button>
          <div className={styles.actions}>
            {goal.status === "active" ? (
              <>
                {presentation?.status === "readyToContinue" ? (
                  <button
                    type="button"
                    onClick={() => void onContinue()}
                    disabled={!enabled || busy}
                  >
                    <Play size={12} />
                    {t("goal.continue", "Continue")}
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={() => openEditor()}
                  disabled={!enabled || busy}
                >
                  {t("goal.editGoal", "Edit Goal")}
                </button>
                <button
                  type="button"
                  onClick={() => void onPause()}
                  disabled={busy}
                  title={t("goal.pauseTooltip", "Pause automatic continuation; the current Turn is not interrupted")}
                >
                  <Pause size={12} />
                  {t("goal.pause", "Pause")}
                </button>
              </>
            ) : goal.status === "complete" ? (
              <button
                type="button"
                onClick={() => openEditor(true)}
                disabled={!enabled || busy}
              >
                {t("goal.editReopen", "Edit & reopen")}
              </button>
            ) : (
              <>
                <button
                  type="button"
                  onClick={() => void onResume()}
                  disabled={!enabled || busy}
                >
                  <Play size={12} />
                  {t("goal.resume", "Resume")}
                </button>
                <button
                  type="button"
                  onClick={() => openEditor(goal.status === "complete")}
                  disabled={!enabled || busy}
                >
                  {t("goal.edit", "Edit")}
                </button>
              </>
            )}
          </div>
          {expanded ? (
            <div className={styles.details}>
              <p>
                {t("goal.goalDescription", "Goal {{id}} stays attached to this Session. Normal follow-ups steer the work without rewriting the objective.", { id: shortId(goal.id) })}
              </p>
              <p>
                {goal.timeUsedSeconds > 0
                  ? t("goal.activeTime", "{{duration}} active time", { duration: formatDuration(goal.timeUsedSeconds) })
                  : t("goal.noUsage", "No completed Goal Turn usage yet")}
                {goal.tokenBudget
                  ? ` · ${t("goal.tokenBudgetLabel", "{{budget}} token budget", { budget: compactNumber(goal.tokenBudget) })}`
                  : ` · ${t("goal.noTokenBudget", "no token budget")}`}
              </p>
              {outcome ? (
                <section className={styles.outcome} aria-label={t("goal.outcome", "Goal outcome")}>
                  <header>
                    <strong>
                      {outcome.status === "complete"
                        ? t("goal.completionOutcome", "Completion outcome")
                        : t("goal.blockedOutcome", "Blocked outcome")}
                    </strong>
                    <small>
                      {outcome.source}
                      {" · "}
                      {formatOutcomeTime(outcome.decidedAt)}
                    </small>
                  </header>
                  <p>{outcome.reason}</p>
                  {outcome.decidedByTurnId ? (
                    <small>
                      {t("goal.decidingTurn", "Deciding Turn")} <code>{shortId(outcome.decidedByTurnId)}</code>
                    </small>
                  ) : null}
                  {outcome.evidenceRefs.length ? (
                    <div className={styles.evidence}>
                      <span>{t("goal.relatedActivity", "Related activity")}</span>
                      {outcome.evidenceRefs.map((evidence) => (
                        <button
                          type="button"
                          key={evidence.itemId}
                          onClick={() => onSelectEvidence(evidence.itemId)}
                          title={`Open ${evidence.kind.replaceAll("_", " ")} details`}
                        >
                          <Check size={12} />
                          <span>{evidence.summary}</span>
                        </button>
                      ))}
                    </div>
                  ) : null}
                </section>
              ) : null}
              {goal.skillIds.length ? (
                <ul>
                  {goal.skillIds.map((skillId) => {
                    const skill = skills.find((candidate) => candidate.id === skillId);
                    return (
                      <li key={skillId}>
                        <Check size={12} />
                        {skill?.name ?? shortId(skillId)}
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p>{t("goal.noSkills", "No Goal-specific Skills selected.")}</p>
              )}
              <button
                className={styles.clearButton}
                type="button"
                onClick={() => {
                  if (window.confirm(t("goal.clearConfirm", "Clear this Session Goal?"))) {
                    void onClear();
                  }
                }}
                disabled={busy}
              >
                <Trash2 size={12} />
                {t("goal.clearGoal", "Clear Goal")}
              </button>
            </div>
          ) : null}
        </>
      ) : null}

      {editorOpen ? (
        <form
          className={styles.editor}
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <header>
            <div>
              <strong>{goal ? t("goal.editGoal", "Edit Goal") : t("goal.newGoal", "New Goal")}</strong>
              <span>
                {t("goal.objectiveHint", "Objective edits keep the same Goal identity and reach the active Turn.")}
              </span>
            </div>
            <button
              type="button"
              onClick={() => setEditorOpen(false)}
              aria-label={t("goal.closeEditor", "Close Goal editor")}
            >
              <X size={14} />
            </button>
          </header>
          <label>
            <span>{t("goal.outcome", "Outcome")}</span>
            <textarea
              value={objective}
              onChange={(event) => setObjective(event.target.value)}
              placeholder={t("goal.describeOutcome", "Describe the complete outcome DeepCode should achieve.")}
              rows={4}
              autoFocus
            />
          </label>
          <label>
            <span>{t("goal.tokenBudget", "Token budget")} <small>{t("goal.tokenBudgetOptional", "optional")}</small></span>
            <input
              type="number"
              min={1}
              step={1}
              value={tokenBudget}
              onChange={(event) => setTokenBudget(event.target.value)}
              placeholder={t("goal.noLimit", "No limit")}
            />
          </label>
          {selectableSkills.length ? (
            <fieldset>
              <legend>
                <Sparkles size={12} />
                {t("goal.skills", "Skills")}
                <small>
                  {skillIds.length}/{MAX_GOAL_SKILLS}
                </small>
              </legend>
              <div className={styles.skillList}>
                {selectableSkills.map((skill) => {
                  const selected = skillIds.includes(skill.id);
                  return (
                    <button
                      type="button"
                      key={skill.id}
                      aria-pressed={selected}
                      onClick={() =>
                        setSkillIds((current) =>
                          selected
                            ? current.filter((id) => id !== skill.id)
                            : current.length < MAX_GOAL_SKILLS
                              ? [...current, skill.id]
                              : current,
                        )
                      }
                      title={skill.description}
                    >
                      {selected ? <Check size={12} /> : null}
                      {skill.name}
                    </button>
                  );
                })}
              </div>
            </fieldset>
          ) : null}
          <footer>
            <button type="button" onClick={() => setEditorOpen(false)}>
              {t("goal.cancel", "Cancel")}
            </button>
            <button
              className={styles.primary}
              type="submit"
              disabled={!enabled || busy || !objective.trim()}
            >
              <Target size={12} />
              {goal ? (resumeAfterSave ? t("goal.saveResume", "Save & resume") : t("goal.saveGoal", "Save Goal")) : t("goal.startGoal", "Start Goal")}
            </button>
          </footer>
        </form>
      ) : null}
    </section>
  );
}

function compactNumber(value: number): string {
  return new Intl.NumberFormat(undefined, {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

function formatOutcomeTime(value: string): string {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return value;
  return timestamp.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function shortId(value: string): string {
  return value.length > 14 ? `${value.slice(0, 11)}…` : value;
}
