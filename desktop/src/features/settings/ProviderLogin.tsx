import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type {
  ConnectionInfo,
  ProviderLoginFlow,
} from "../../generated/app-server";
import type { ConnectionCatalogController } from "./useConnectionCatalog";
import { confirmAction } from "../../platform/confirmAction";
import styles from "./ConnectionSettings.module.css";

const pending = (flow: ProviderLoginFlow | null) =>
  flow && ["starting", "pending", "exchanging"].includes(flow.status);

export function ProviderLogin({
  connection,
  controller,
}: {
  connection: ConnectionInfo;
  controller: ConnectionCatalogController;
}) {
  const { t } = useTranslation();
  const [flow, setFlow] = useState<ProviderLoginFlow | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { pollLogin, reload } = controller;
  useEffect(() => {
    if (!pending(flow) || !flow) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      void pollLogin(flow.flowId)
        .then(async (next) => {
          if (cancelled) return;
          setFlow(next);
          if (next.status === "authenticated") await reload();
        })
        .catch((cause) => {
          if (!cancelled) setError(String(cause));
        });
    }, 1000);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [flow, pollLogin, reload]);
  const run = async (operation: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await operation();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div>
      {connection.accountId ? (
        <p>
          {t("provider.account", "Account")}: {connection.accountId}
        </p>
      ) : null}
      <small>
        {t(
          "provider.loginExplanation",
          "OpenRouter login supplies a user-controlled API key. No refresh token is issued. Sign in on the machine running DeepCode.",
        )}
      </small>
      <div className={styles.actions}>
        <button
          type="button"
          disabled={busy || Boolean(pending(flow))}
          onClick={() =>
            void run(async () => setFlow(await controller.login(connection.id)))
          }
        >
          {t("provider.signInAuth", "Sign in with OpenRouter")}
        </button>
        {pending(flow) && flow ? (
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void run(async () =>
                setFlow(await controller.cancelLogin(flow.flowId)),
              )
            }
          >
            {t("provider.cancelLogin", "Cancel login")}
          </button>
        ) : null}
        {connection.accountId ? (
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                if (
                  !(await confirmAction(
                    t(
                      "provider.disconnectConfirm",
                      "Disconnect this account locally? Its next model request will stop. To revoke the key remotely, use OpenRouter's key settings.",
                    ),
                    { confirmLabel: t("provider.disconnect", "Disconnect") },
                  ))
                )
                  return;
                await controller.logout(connection.id);
                setFlow(null);
                await reload();
              })
            }
          >
            {t("provider.disconnect", "Disconnect")}
          </button>
        ) : null}
        <a
          href="https://openrouter.ai/settings/keys"
          target="_blank"
          rel="noreferrer"
        >
          {t("provider.manageKeys", "Manage remote keys")}
        </a>
      </div>
      {flow?.authorizationUrl ? (
        <a href={flow.authorizationUrl} target="_blank" rel="noreferrer">
          {t("provider.openLogin", "Open sign-in page")}
        </a>
      ) : null}
      {flow ? (
        <p role="status">{t(`provider.login.${flow.status}`, flow.status)}</p>
      ) : null}
      {error || flow?.error ? <p role="alert">{error || flow?.error}</p> : null}
    </div>
  );
}
