import { useEffect, useState } from "react";
import { App } from "./App";
import { BrowserRuntime } from "./rpc/browserRuntime";
import styles from "./webShell.module.css";

declare const __WEB_BUILD_ID__: string;

export function BrowserShell() {
  const [picker, setPicker] = useState<{
    resolve(value: string | null): void;
  } | null>(null);
  const [path, setPath] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [runtime] = useState(
    () =>
      new BrowserRuntime({
        buildId: __WEB_BUILD_ID__,
        chooseDirectory: () =>
          new Promise((resolve) => {
            setPath("");
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
        <span>DeepCode · Local service</span>
        <span>{notice}</span>
        <button
          onClick={() =>
            void runtime
              .logout()
              .then(() =>
                setNotice("Signed out · run deepcode web for a fresh link"),
              )
              .catch((error) => setNotice(String(error)))
          }
        >
          Sign out
        </button>
      </header>
      <div className={styles.app}>
        <App runtime={runtime} />
      </div>
      {picker && (
        <div className={styles.backdrop}>
          <form
            className={styles.dialog}
            role="dialog"
            aria-modal="true"
            aria-labelledby="directory-title"
            onSubmit={(event) => {
              event.preventDefault();
              if (path.trim()) closePicker(path.trim());
            }}
          >
            <h2 id="directory-title">Open a project on the service machine</h2>
            <p>
              Enter the folder path on the machine running DeepCode. It will be
              opened as untrusted until you choose to trust it.
            </p>
            <label htmlFor="server-folder">Project folder</label>
            <input
              id="server-folder"
              autoFocus
              value={path}
              onChange={(event) => setPath(event.target.value)}
              placeholder="/path/to/project"
            />
            <footer>
              <button type="button" onClick={() => closePicker(null)}>
                Cancel
              </button>
              <button disabled={!path.trim()} type="submit">
                Open project
              </button>
            </footer>
          </form>
        </div>
      )}
    </div>
  );
}
