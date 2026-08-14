import {
  Check,
  Circle,
  ListChecks,
  LoaderCircle,
} from "lucide-react";
import {
  useEffect,
  useId,
  useRef,
  useState,
  type FocusEvent,
} from "react";

import type { TurnPlanState } from "../../app/workspaceState";
import type { TurnPlanStep } from "../../generated/app-server";
import styles from "./PlanProgress.module.css";

interface PlanProgressProps {
  plan: TurnPlanState;
}

function currentStepIndex(steps: TurnPlanStep[]): number {
  const running = steps.findIndex((step) => step.status === "in_progress");
  if (running >= 0) return running;
  const pending = steps.findIndex((step) => step.status === "pending");
  if (pending >= 0) return pending;
  return Math.max(0, steps.length - 1);
}

function StepIcon({ step }: { step: TurnPlanStep }) {
  const props = { size: 14, strokeWidth: 1.9, "aria-hidden": true };
  switch (step.status) {
    case "completed":
      return <Check {...props} />;
    case "in_progress":
      return <LoaderCircle {...props} className={styles.spinning} />;
    default:
      return <Circle {...props} />;
  }
}

export function PlanProgress({ plan }: PlanProgressProps) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const popoverId = useId();
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const index = currentStepIndex(plan.steps);
  const complete = plan.steps.every((step) => step.status === "completed");

  useEffect(() => {
    if (!pinned) return;
    const closeOutside = (event: PointerEvent) => {
      if (rootRef.current?.contains(event.target as Node)) return;
      setOpen(false);
      setPinned(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
    };
  }, [pinned]);

  if (plan.steps.length === 0) return null;

  const handleBlur = (event: FocusEvent<HTMLDivElement>) => {
    if (pinned || event.currentTarget.contains(event.relatedTarget)) return;
    setOpen(false);
  };

  return (
    <div
      className={styles.root}
      ref={rootRef}
      onPointerEnter={() => setOpen(true)}
      onPointerLeave={() => {
        if (!pinned) setOpen(false);
      }}
      onFocus={() => setOpen(true)}
      onBlur={handleBlur}
      onKeyDown={(event) => {
        if (event.key !== "Escape") return;
        event.stopPropagation();
        setOpen(false);
        setPinned(false);
      }}
    >
      {open ? (
        <section
          className={styles.popover}
          id={popoverId}
          aria-label="Execution plan"
        >
          <header>
            <span>
              <ListChecks size={14} aria-hidden="true" />
              Execution plan
            </span>
            <small>
              {plan.steps.filter((step) => step.status === "completed").length}/
              {plan.steps.length}
            </small>
          </header>
          {plan.explanation ? <p>{plan.explanation}</p> : null}
          <ol>
            {plan.steps.map((step, stepIndex) => (
              <li
                key={`${stepIndex}:${step.step}`}
                data-status={step.status}
                aria-current={step.status === "in_progress" ? "step" : undefined}
              >
                <span>
                  <StepIcon step={step} />
                </span>
                <strong>{step.step}</strong>
              </li>
            ))}
          </ol>
        </section>
      ) : null}
      <button
        type="button"
        className={styles.trigger}
        aria-expanded={open}
        aria-controls={popoverId}
        onClick={() => {
          const nextPinned = !pinned;
          setPinned(nextPinned);
          setOpen(nextPinned);
        }}
      >
        <span data-complete={complete}>
          {complete ? <Check size={14} /> : <Circle size={14} />}
        </span>
        {complete ? "Plan complete" : `Step ${index + 1} / ${plan.steps.length}`}
      </button>
    </div>
  );
}
