import {
  AlertCircle,
  CheckCircle2,
  CircleDashed,
  MinusCircle,
} from "lucide-react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";

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
  stream: "Streaming",
  tool: "Tool call",
  continuation: "Tool result continuation",
  reasoning: "Reasoning",
  image: "Image input",
};

export function ConnectionVerification({
  result,
  compact = false,
}: ConnectionVerificationProps) {
  const { t } = useTranslation();
  const label =
    result.status === "ready" &&
    result.stages.some(
      (stage) => stage.id === "continuation" && stage.status === "passed",
    )
      ? t("provider.verification.roundTrip", "Agent tool round trip verified")
      : statusLabel(result.status, t);
  return (
    <section
      className={styles.verification}
      data-status={result.status}
      data-compact={compact}
      aria-label={t("provider.verification.status", "Connection status: {{label}}", {
        label,
      })}
    >
      <header>
        <strong>{label}</strong>
        <span>
          {t("provider.verification.latency", "{{latency}} ms total", {
            latency: result.latencyMs,
          })}
        </span>
      </header>
      <div className={styles.stages}>
        {result.stages.map((stage) => (
          <div key={stage.id} data-status={stage.status}>
            <StageIcon stage={stage} />
            <span>
              <strong>
                {t(`provider.verification.stage.${stage.id}`, STAGE_LABELS[stage.id])}
              </strong>
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

function statusLabel(
  status: ProviderTestResult["status"],
  t: TFunction,
): string {
  switch (status) {
    case "ready":
      return t("provider.verification.ready", "Model request verified");
    case "connected":
      return t("provider.verification.connected", "Catalog connected");
    case "limited":
      return t("provider.verification.limited", "Configured · model check needed");
    default:
      return t("provider.verification.error", "Connection needs attention");
  }
}
