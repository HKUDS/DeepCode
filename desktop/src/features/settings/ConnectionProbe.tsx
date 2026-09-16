import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type {
  ProviderTestResult,
  ProviderUpsertParams,
} from "../../generated/app-server";
import type { ConnectionCatalogController } from "./useConnectionCatalog";
import { ConnectionVerification } from "./ConnectionVerification";
import styles from "./ConnectionSettings.module.css";

export function ConnectionProbe({
  connection,
  controller,
}: {
  connection: ProviderUpsertParams["connection"];
  controller: ConnectionCatalogController;
}) {
  const { t } = useTranslation();
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const [outcome, setOutcome] = useState<{
    fingerprint: string;
    result?: ProviderTestResult;
    error?: string;
  } | null>(null);
  const fingerprint = JSON.stringify([connection, model.trim()]);
  const test = async (mode: "quick" | "agent") => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setOutcome(null);
    try {
      const result = await controller.test(connection.id, model.trim(), {
        connection,
        mode,
      });
      setOutcome({ fingerprint, result });
    } catch (cause) {
      setOutcome({
        fingerprint,
        error: cause instanceof Error ? cause.message : String(cause),
      });
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };
  const current = outcome?.fingerprint === fingerprint ? outcome : null;
  return (
    <fieldset className={styles.wide}>
      <legend>{t("provider.verifyDraft", "Verify current settings")}</legend>
      <label>
        {t("provider.verifyModel", "Model to verify")}
        <input
          value={model}
          onChange={(event) => setModel(event.target.value)}
          placeholder="provider/model-id"
        />
      </label>
      <small>
        {t(
          "provider.probeBudget",
          "Uses the current form without saving. Agent verification makes up to 3 model requests within 90 seconds, using only a local verification tool. Provider reasoning can increase the token budget.",
        )}
      </small>
      <div className={styles.actions}>
        <button
          type="button"
          disabled={busy || !model.trim()}
          onClick={() => void test("quick")}
        >
          {t("provider.quick", "Quick test")}
        </button>
        <button
          type="button"
          disabled={busy || !model.trim()}
          onClick={() => void test("agent")}
        >
          {t("provider.agentTest", "Verify Agent compatibility")}
        </button>
      </div>
      {busy ? (
        <p role="status">{t("provider.testing", "Verification running…")}</p>
      ) : null}
      {current?.error ? <p role="alert">{current.error}</p> : null}
      {current?.result ? (
        <ConnectionVerification result={current.result} />
      ) : null}
      {outcome && !current ? (
        <p>
          {t(
            "provider.probeStale",
            "Settings changed. Verify again to check this configuration.",
          )}
        </p>
      ) : null}
    </fieldset>
  );
}
