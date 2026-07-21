import {
  Check,
  ChevronDown,
  ChevronUp,
  Pause,
  Play,
  RotateCcw,
  Sparkles,
  Target,
  Trash2,
  X,
} from "lucide-react";
import { useMemo, useState } from "react";

import type { Goal, SkillInfo } from "../../generated/app-server";
import type { GoalDefinitionInput } from "../../app/useWorkspaceController";
import styles from "./GoalRail.module.css";

interface GoalRailProps {
  goal: Goal | null;
  enabled: boolean;
  busy: boolean;
  skills: SkillInfo[];
  onSet(input: GoalDefinitionInput): Promise<void>;
  onPause(): Promise<void>;
  onResume(): Promise<void>;
  onClear(): Promise<void>;
}

const TERMINAL_STATUSES = new Set<Goal["status"]>([
  "completed",
  "budget_limited",
]);
const MAX_GOAL_SKILLS = 8;

export function GoalRail({
  goal,
  enabled,
  busy,
  skills,
  onSet,
  onPause,
  onResume,
  onClear,
}: GoalRailProps) {
  const [editorOpen, setEditorOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [objective, setObjective] = useState("");
  const [criteria, setCriteria] = useState("");
  const [skillIds, setSkillIds] = useState<string[]>([]);
  const selectableSkills = useMemo(
    () => skills.filter((skill) => skill.selectable && skill.enabled),
    [skills],
  );
  const terminal = goal ? TERMINAL_STATUSES.has(goal.status) : false;

  const openEditor = () => {
    const editable = goal && !terminal && goal.status !== "active";
    setObjective(editable ? goal.objective : "");
    setCriteria(editable ? goal.acceptanceCriteria.join("\n") : "");
    setSkillIds(editable ? [...goal.skillIds] : []);
    setEditorOpen(true);
  };

  const submit = async () => {
    const cleanObjective = objective.trim();
    if (!cleanObjective || busy || !enabled) return;
    await onSet({
      objective: cleanObjective,
      acceptanceCriteria: criteria
        .split(/\r?\n/)
        .map((value) => value.trim())
        .filter(Boolean),
      skillIds,
    });
    setEditorOpen(false);
    setExpanded(false);
  };

  if (!goal && !editorOpen) {
    return (
      <button
        className={styles.emptyRail}
        type="button"
        onClick={openEditor}
        disabled={!enabled || busy}
      >
        <Target size={13} />
        <span>Set a Goal</span>
        <small>Let DeepCode continue until the outcome is verified</small>
      </button>
    );
  }

  return (
    <section
      className={styles.rail}
      data-status={goal?.status ?? "draft"}
      aria-label="Session Goal"
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
                {statusLabel(goal.status)}
                <i>·</i>
                Attempt {attemptLabel(goal)}
                {goal.tokensUsed ? (
                  <>
                    <i>·</i>
                    {compactNumber(goal.tokensUsed)} tokens
                  </>
                ) : null}
              </small>
            </span>
            {goal.budget.maxAttempts ? (
              <progress
                max={goal.budget.maxAttempts}
                value={Math.min(goal.attemptCount, goal.budget.maxAttempts)}
                aria-label={`${goal.attemptCount} of ${goal.budget.maxAttempts} attempts`}
              />
            ) : null}
            {expanded ? <ChevronDown size={13} /> : <ChevronUp size={13} />}
          </button>
          <div className={styles.actions}>
            {goal.status === "active" ? (
              <button
                type="button"
                onClick={() => void onPause()}
                disabled={busy}
                title="Pause after stopping the current Goal Turn"
              >
                <Pause size={12} />
                Pause
              </button>
            ) : terminal ? (
              <button
                type="button"
                onClick={openEditor}
                disabled={!enabled || busy}
              >
                <RotateCcw size={12} />
                New Goal
              </button>
            ) : (
              <>
                <button
                  type="button"
                  onClick={() => void onResume()}
                  disabled={!enabled || busy}
                >
                  <Play size={12} />
                  Resume
                </button>
                <button
                  type="button"
                  onClick={openEditor}
                  disabled={!enabled || busy}
                >
                  Edit
                </button>
              </>
            )}
          </div>
          {expanded ? (
            <div className={styles.details}>
              {goal.acceptanceCriteria.length ? (
                <ul>
                  {goal.acceptanceCriteria.map((criterion) => (
                    <li key={criterion}>
                      <Check size={11} />
                      {criterion}
                    </li>
                  ))}
                </ul>
              ) : (
                <p>Completion is evaluated against the stated outcome.</p>
              )}
              {goal.lastReason ? <p>{goal.lastReason}</p> : null}
              <button
                className={styles.clearButton}
                type="button"
                onClick={() => {
                  if (window.confirm("Clear this Session Goal?")) {
                    void onClear();
                  }
                }}
                disabled={busy}
              >
                <Trash2 size={11} />
                Clear Goal
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
              <strong>{goal && !terminal ? "Refine Goal" : "New Goal"}</strong>
              <span>DeepCode will work in ordinary, reviewable Turns.</span>
            </div>
            <button
              type="button"
              onClick={() => setEditorOpen(false)}
              aria-label="Close Goal editor"
            >
              <X size={14} />
            </button>
          </header>
          <label>
            <span>Outcome</span>
            <textarea
              value={objective}
              onChange={(event) => setObjective(event.target.value)}
              placeholder="What should be true when DeepCode stops?"
              rows={2}
              autoFocus
            />
          </label>
          <label>
            <span>Done when</span>
            <textarea
              value={criteria}
              onChange={(event) => setCriteria(event.target.value)}
              placeholder={"One acceptance criterion per line (optional)"}
              rows={2}
            />
          </label>
          {selectableSkills.length ? (
            <fieldset>
              <legend>
                <Sparkles size={11} />
                Skills
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
                      {selected ? <Check size={10} /> : null}
                      {skill.name}
                    </button>
                  );
                })}
              </div>
            </fieldset>
          ) : null}
          <footer>
            <button type="button" onClick={() => setEditorOpen(false)}>
              Cancel
            </button>
            <button
              className={styles.primary}
              type="submit"
              disabled={!enabled || busy || !objective.trim()}
            >
              <Target size={12} />
              {goal && !terminal ? "Save and run" : "Start Goal"}
            </button>
          </footer>
        </form>
      ) : null}
    </section>
  );
}

function attemptLabel(goal: Goal): string {
  return goal.budget.maxAttempts
    ? `${goal.attemptCount}/${goal.budget.maxAttempts}`
    : String(goal.attemptCount);
}

function statusLabel(status: Goal["status"]): string {
  return {
    active: "Working",
    paused: "Paused",
    blocked: "Needs input",
    usage_limited: "Usage limit",
    budget_limited: "Budget reached",
    completed: "Verified",
  }[status];
}

function compactNumber(value: number): string {
  return new Intl.NumberFormat(undefined, {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}
