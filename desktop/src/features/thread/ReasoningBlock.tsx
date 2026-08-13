import { BrainCircuit, ChevronRight, CircleDashed } from "lucide-react";
import { useEffect, useState } from "react";

import { decodeReasoningPayload } from "../../app/reasoningPayload";
import type { Item } from "../../generated/app-server";
import { MarkdownContent } from "./MarkdownContent";
import type { TranscriptMode } from "./transcriptMode";
import styles from "./ReasoningBlock.module.css";

interface ReasoningBlockProps {
  item: Item;
  mode: TranscriptMode;
}

interface DisclosureOverride {
  key: string;
  open: boolean;
}

function formatDuration(milliseconds: number): string {
  const seconds = Math.max(0, Math.round(milliseconds / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${(seconds % 60).toString().padStart(2, "0")}s`;
}

function useReasoningDuration(item: Item, active: boolean): number | null {
  const stored = decodeReasoningPayload(item.payload).durationMs;
  const [now, setNow] = useState(Date.now);

  useEffect(() => {
    if (!active) return;
    const interval = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(interval);
  }, [active]);

  if (stored !== null) return stored;
  const start = new Date(item.createdAt).getTime();
  const end = active ? now : new Date(item.updatedAt).getTime();
  return Number.isFinite(start) && Number.isFinite(end)
    ? Math.max(0, end - start)
    : null;
}

export function ReasoningBlock({ item, mode }: ReasoningBlockProps) {
  const active = item.status === "in_progress";
  const payload = decodeReasoningPayload(item.payload);
  const effort = payload.effort ?? "auto";
  const summary = payload.summaryText;
  const trace = payload.traceText;
  const opaque = payload.availability === "opaque";
  const duration = useReasoningDuration(item, active);
  const disclosureKey = `${item.id}:${item.status}:${mode}`;
  const [manualOverride, setManualOverride] =
    useState<DisclosureOverride | null>(null);
  const open =
    manualOverride?.key === disclosureKey
      ? manualOverride.open
      : active || mode === "verbose";

  const durationLabel = duration === null ? null : formatDuration(duration);
  const title = active
    ? ["Thinking", effort !== "auto" ? effort : null, durationLabel]
        .filter(Boolean)
        .join(" · ")
    : durationLabel
      ? `Thought for ${durationLabel}`
      : "Thinking completed";

  return (
    <details
      className={styles.reasoning}
      data-active={active}
      data-status={item.status}
      open={open}
    >
      <summary
        onClick={(event) => {
          event.preventDefault();
          setManualOverride({ key: disclosureKey, open: !open });
        }}
      >
        {active ? (
          <CircleDashed className={styles.spinner} size={16} aria-hidden="true" />
        ) : (
          <BrainCircuit size={16} aria-hidden="true" />
        )}
        <span>
          <strong>{title}</strong>
          <small>
            {active
              ? "Model reasoning"
              : opaque
                ? "Details unavailable"
                : effort === "auto"
                  ? "Reasoning"
                  : `Reasoning · ${effort}`}
          </small>
        </span>
        <ChevronRight className={styles.chevron} size={14} aria-hidden="true" />
      </summary>
      <div
        className={styles.content}
        aria-live={active ? "polite" : undefined}
      >
        {opaque && !summary && !trace ? (
          <p>This model completed reasoning without returning displayable details.</p>
        ) : null}
        {summary ? <MarkdownContent compact>{summary}</MarkdownContent> : null}
        {trace && (!summary || mode === "verbose") ? (
          <section className={styles.trace}>
            {summary ? <h4>Provider reasoning details</h4> : null}
            <MarkdownContent compact>{trace}</MarkdownContent>
          </section>
        ) : null}
        {trace && summary && mode === "normal" ? (
          <details className={styles.traceDisclosure}>
            <summary>Provider reasoning details</summary>
            <MarkdownContent compact>{trace}</MarkdownContent>
          </details>
        ) : null}
        {active && !summary && !trace ? (
          <p className={styles.waiting}>Waiting for reasoning details…</p>
        ) : null}
      </div>
    </details>
  );
}
