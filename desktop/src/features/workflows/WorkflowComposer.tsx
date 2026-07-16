import { useMemo, useState } from "react";

import type {
  JsonObject,
  WorkflowRun,
  WorkflowStartParams,
} from "../../generated/app-server";
import styles from "./WorkflowComposer.module.css";

type SourceType = WorkflowStartParams["sourceType"];

interface WorkflowComposerProps {
  enabled: boolean;
  busy: boolean;
  workflow: WorkflowRun | null;
  disabledReason: string | null;
  onPickFile(): Promise<string | null>;
  onStart(
    sourceType: SourceType,
    source: string,
    options: NonNullable<WorkflowStartParams["options"]>,
  ): Promise<void>;
  onRetry(workflowRunId: string): Promise<void>;
  onRespond(
    workflowRunId: string,
    interactionId: string,
    action: "approve" | "modify" | "cancel",
    feedback?: string,
  ): Promise<void>;
  onInterrupt(): void;
}

const sourceLabels: Array<[SourceType, string]> = [
  ["local", "Document"],
  ["url", "URL"],
  ["repository", "Repository"],
  ["requirement", "Requirement"],
];

export function WorkflowComposer({
  enabled,
  busy,
  workflow,
  disabledReason,
  onPickFile,
  onStart,
  onRetry,
  onRespond,
  onInterrupt,
}: WorkflowComposerProps) {
  const [sourceType, setSourceType] = useState<SourceType>("local");
  const [source, setSource] = useState("");
  const [enableIndexing, setEnableIndexing] = useState(false);
  const [planReview, setPlanReview] = useState(true);
  const [feedback, setFeedback] = useState("");
  const active = workflow && ["queued", "running", "waiting"].includes(workflow.status);
  const interaction = useMemo(() => workflowInteraction(workflow), [workflow]);
  const progress = workflow?.progressTotal
    ? Math.round((workflow.progressCurrent / workflow.progressTotal) * 100)
    : 0;

  const chooseFile = async () => {
    const path = await onPickFile();
    if (path) setSource(path);
  };

  const start = async () => {
    const value = source.trim();
    if (!value || !enabled || busy || active) return;
    await onStart(sourceType, value, { enableIndexing, planReview });
  };

  const respond = async (action: "approve" | "modify" | "cancel") => {
    if (!workflow || !interaction) return;
    await onRespond(workflow.id, interaction.id, action, feedback);
    if (action !== "modify") setFeedback("");
  };

  return (
    <footer className={styles.region}>
      {active ? (
        <section className={styles.console} aria-live="polite">
          <div className={styles.consoleHead}>
            <div>
              <span className={styles.kicker}>
                Paper2Code · attempt {workflow.attempt}
              </span>
              <strong>{workflow.currentStage?.replaceAll("_", " ") ?? workflow.status}</strong>
            </div>
            <span className={styles.percent}>{progress}%</span>
          </div>
          <div className={styles.progress} aria-label={`${progress}% complete`}>
            <span style={{ width: `${progress}%` }} />
          </div>
          {interaction ? (
            <div className={styles.review}>
              <div>
                <span className={styles.kicker}>Review gate</span>
                <h3>{interaction.title}</h3>
                <p>{interaction.description}</p>
              </div>
              {interaction.planPreview ? <pre>{interaction.planPreview}</pre> : null}
              <textarea
                value={feedback}
                onChange={(event) => setFeedback(event.target.value)}
                placeholder="Optional feedback for a revised plan…"
                rows={2}
              />
              <div className={styles.reviewActions}>
                <button type="button" onClick={() => void respond("cancel")}>
                  Cancel run
                </button>
                <button
                  type="button"
                  disabled={!feedback.trim() || busy}
                  onClick={() => void respond("modify")}
                >
                  Request changes
                </button>
                <button
                  className={styles.primary}
                  type="button"
                  disabled={busy}
                  onClick={() => void respond("approve")}
                >
                  Approve &amp; continue
                </button>
              </div>
            </div>
          ) : (
            <div className={styles.runningRow}>
              <span>The run is durable; closing the window will recover it as retryable.</span>
              <button
                className={styles.danger}
                type="button"
                onClick={onInterrupt}
              >
                Stop workflow
              </button>
            </div>
          )}
        </section>
      ) : (
        <section className={styles.composer}>
          <div
            className={styles.sourceTabs}
            role="tablist"
            aria-label="Input source"
          >
            {sourceLabels.map(([value, label]) => (
              <button
                key={value}
                type="button"
                role="tab"
                aria-selected={sourceType === value}
                onClick={() => {
                  setSourceType(value);
                  setSource("");
                }}
              >
                {label}
              </button>
            ))}
          </div>
          <div className={styles.inputRow}>
            {sourceType === "requirement" ? (
              <textarea
                value={source}
                onChange={(event) => setSource(event.target.value)}
                placeholder="Describe the implementation outcome and verification criteria…"
                rows={2}
                disabled={!enabled}
              />
            ) : (
              <input
                value={source}
                onChange={(event) => setSource(event.target.value)}
                placeholder={placeholderFor(sourceType)}
                readOnly={sourceType === "local"}
                disabled={!enabled}
              />
            )}
            {sourceType === "local" ? (
              <button type="button" onClick={() => void chooseFile()} disabled={!enabled}>
                Choose file
              </button>
            ) : null}
          </div>
          <div className={styles.toolbar}>
            <div className={styles.options}>
              <label>
                <input
                  type="checkbox"
                  checked={planReview}
                  onChange={(event) => setPlanReview(event.target.checked)}
                />
                Review plan
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={enableIndexing}
                  onChange={(event) => setEnableIndexing(event.target.checked)}
                />
                Reference indexing
              </label>
            </div>
            <div className={styles.submit}>
              <span>{disabledReason ?? terminalSummary(workflow)}</span>
              {workflow?.status === "failed" || workflow?.status === "cancelled" ? (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void onRetry(workflow.id)}
                >
                  Retry attempt {workflow.attempt + 1}
                </button>
              ) : null}
              <button
                className={styles.primary}
                type="button"
                disabled={!enabled || busy || !source.trim()}
                onClick={() => void start()}
              >
                Start Paper2Code
              </button>
            </div>
          </div>
        </section>
      )}
    </footer>
  );
}

interface InteractionView {
  id: string;
  title: string;
  description: string;
  planPreview: string | null;
}

function workflowInteraction(workflow: WorkflowRun | null): InteractionView | null {
  if (!workflow || workflow.status !== "waiting") return null;
  const interaction = recordValue(workflow.checkpoint.interaction);
  if (!interaction || typeof interaction.id !== "string") return null;
  const request = recordValue(interaction.request) ?? ({} as JsonObject);
  const data = recordValue(request.data);
  return {
    id: interaction.id,
    title: stringValue(request.title) ?? "Review implementation plan",
    description:
      stringValue(request.description) ?? "Approve or revise the plan before generation.",
    planPreview: stringValue(data?.plan_preview),
  };
}

function recordValue(value: unknown): JsonObject | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as JsonObject)
    : null;
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function placeholderFor(sourceType: SourceType): string {
  if (sourceType === "url") return "https://arxiv.org/abs/…";
  if (sourceType === "repository") return "https://github.com/owner/repository";
  return "Choose a PDF, Markdown, DOCX, HTML, or text document";
}

function terminalSummary(workflow: WorkflowRun | null): string {
  if (!workflow) return "The generated code must pass discovered tests to complete.";
  if (workflow.status === "completed") return "Last run completed with passing verification.";
  return workflow.errorMessage ?? "Last run can be retried from its checkpoint.";
}
