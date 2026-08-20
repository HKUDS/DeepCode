import {
  FilePenLine,
  Globe2,
  ShieldAlert,
  TerminalSquare,
  Wrench,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import type { Approval, ApprovalDecision } from "../../generated/app-server";
import styles from "./ApprovalCard.module.css";

interface ApprovalCardProps {
  approval: Approval;
  busy: boolean;
  onRespond(approvalId: string, decision: ApprovalDecision): void;
}

function requestDetail(approval: Approval, t: (key: string, defaultValue: string, options?: Record<string, string>) => string): string {
  const tool = approval.request.toolName;
  const reason = approval.request.reason;
  if (typeof reason === "string" && reason) return reason;
  if (typeof tool === "string" && tool) return t("approval.allowContinue", "Allow {{tool}} to continue?", { tool });
  return t("approval.reviewOperation", "Review this operation before the agent continues.");
}

function requestTool(approval: Approval, t: (key: string, defaultValue: string) => string): string {
  const tool = approval.request.toolName;
  return typeof tool === "string" && tool.trim() ? tool : t("approval.sensitiveOperation", "Sensitive operation");
}

function requestArguments(approval: Approval): string | null {
  const argumentsValue = approval.request.arguments;
  if (
    typeof argumentsValue !== "object" ||
    argumentsValue === null ||
    Array.isArray(argumentsValue)
  ) {
    return null;
  }
  const serialized = JSON.stringify(argumentsValue, null, 2);
  if (!serialized || serialized === "{}") return null;
  return serialized.length > 4000
    ? `${serialized.slice(0, 4000)}\n… truncated`
    : serialized;
}

function CategoryIcon({ category }: { category: Approval["category"] }) {
  const props = { size: 14, strokeWidth: 1.8 };
  switch (category) {
    case "command":
      return <TerminalSquare {...props} />;
    case "file_write":
      return <FilePenLine {...props} />;
    case "network":
      return <Globe2 {...props} />;
    case "destructive":
      return <ShieldAlert {...props} />;
    default:
      return <Wrench {...props} />;
  }
}

export function ApprovalCard({ approval, busy, onRespond }: ApprovalCardProps) {
  const { t } = useTranslation();
  if (approval.status !== "pending") {
    return (
      <p className={styles.resolution}>
        {t("approval.decision", "Decision: {{status}}", { status: approval.status.replaceAll("_", " ") })}
      </p>
    );
  }
  const argumentsPreview = requestArguments(approval);
  return (
    <div className={styles.card} aria-label={t("approval.label", "Approval required")}>
      <div className={styles.operation}>
        <CategoryIcon category={approval.category} />
        <span>
          <strong>{requestTool(approval, t)}</strong>
          <small>{approval.category.replaceAll("_", " ")}</small>
        </span>
      </div>
      <p>{requestDetail(approval, t)}</p>
      {argumentsPreview ? (
        <details className={styles.arguments}>
          <summary>{t("approval.reviewArguments", "Review arguments")}</summary>
          <pre>{argumentsPreview}</pre>
        </details>
      ) : null}
      <div className={styles.actions}>
        <button
          type="button"
          onClick={() => onRespond(approval.id, "approved_once")}
          disabled={busy}
        >
          {t("approval.allowOnce", "Allow once")}
        </button>
        <button
          type="button"
          onClick={() => onRespond(approval.id, "approved_session")}
          disabled={busy}
        >
          {t("approval.allowSession", "Allow for Session")}
        </button>
        <button
          className={styles.danger}
          type="button"
          onClick={() => onRespond(approval.id, "denied")}
          disabled={busy}
        >
          {t("approval.deny", "Deny")}
        </button>
      </div>
    </div>
  );
}
