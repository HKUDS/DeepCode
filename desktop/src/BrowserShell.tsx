import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { App } from "./App";
import { ConfirmHost } from "./components/ConfirmHost";
import { BrowserRuntime } from "./rpc/browserRuntime";
import { ServerFolderPicker } from "./ServerFolderPicker";
import styles from "./webShell.module.css";

declare const __WEB_BUILD_ID__: string;

export function BrowserShell() {
  const { t } = useTranslation();
  const [picker, setPicker] = useState<{
    resolve(value: string | null): void;
  } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [runtime] = useState(
    () =>
      new BrowserRuntime({
        buildId: __WEB_BUILD_ID__,
        chooseDirectory: () =>
          new Promise((resolve) => {
            setPicker({ resolve });
          }),
      }),
  );
  useEffect(() => {
    const dispose = () => runtime.dispose();
    // Opening a fresh access link in this tab only changes its fragment.
    // Reload so the new document exchanges the ticket before connecting.
    const openAccessLink = () => {
      if (new URLSearchParams(location.hash.slice(1)).has("ticket"))
        location.reload();
    };
    window.addEventListener("pagehide", dispose);
    window.addEventListener("hashchange", openAccessLink);
    return () => {
      window.removeEventListener("pagehide", dispose);
      window.removeEventListener("hashchange", openAccessLink);
      dispose();
    };
  }, [runtime]);
  const closePicker = (value: string | null) => {
    picker?.resolve(value);
    setPicker(null);
  };
  return (
    <div className={styles.shell}>
      <header className={styles.bar}>
        <span>{t("browser.title", "DeepCode · Local service")}</span>
        <span>{notice}</span>
        <button
          onClick={() =>
            void runtime
              .logout()
              .then(() =>
                setNotice(
                  t(
                    "browser.signedOut",
                    "Signed out · run deepcode web for a fresh link",
                  ),
                ),
              )
              .catch((error) => setNotice(String(error)))
          }
        >
          {t("browser.signOut", "Sign out")}
        </button>
      </header>
      <div className={styles.app}>
        <App runtime={runtime} />
      </div>
      {picker && (
        <ServerFolderPicker
          listDirectories={(path) =>
            runtime.request("directory/list", path ? { path } : {})
          }
          onClose={closePicker}
        />
      )}
      <ConfirmHost />
    </div>
  );
}
