import {
  AlertCircle,
  CheckCircle2,
  CircleDashed,
  MinusCircle,
} from "lucide-react";

import type {
  ProviderTestResult,
  ProviderVerificationStage,
} from "../../generated/app-server";
import styles from "./ConnectionVerification.module.css";

interface ConnectionVerificationProps {
  result: ProviderTestResult;
  compact?: boolean;
}

const STAGE_LABELS: Record<ProviderVerificationStage["id"], string> = {
  credential: "Credential",
  catalog: "Model catalog",
  model: "Model request",
};

export function ConnectionVerification({
  result,
  compact = false,
}: ConnectionVerificationProps) {
  return (
    <section
      className={styles.verification}
      data-status={result.status}
      data-compact={compact}
      aria-label={`Connection status: ${statusLabel(result.status)}`}
    >
      <header>
        <strong>{statusLabel(result.status)}</strong>
        <span>{result.latencyMs} ms total</span>
      </header>
      <div className={styles.stages}>
        {result.stages.map((stage) => (
          <div key={stage.id} data-status={stage.status}>
            <StageIcon stage={stage} />
            <span>
              <strong>{STAGE_LABELS[stage.id]}</strong>
              <small>{stage.detail}</small>
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

function StageIcon({ stage }: { stage: ProviderVerificationStage }) {
  if (stage.status === "passed") return <CheckCircle2 size={14} />;
  if (stage.status === "failed") return <AlertCircle size={14} />;
  if (stage.status === "skipped") return <MinusCircle size={14} />;
  return <CircleDashed size={14} />;
}

function statusLabel(status: ProviderTestResult["status"]): string {
  switch (status) {
    case "ready":
      return "Ready for agent work";
    case "connected":
      return "Catalog connected";
    case "limited":
      return "Configured · model check needed";
    default:
      return "Connection needs attention";
  }
}
