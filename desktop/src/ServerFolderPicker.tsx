import { CornerLeftUp, Folder } from "lucide-react";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { useTranslation } from "react-i18next";

import { Dialog } from "./components/Dialog";
import type { MethodResults } from "./generated/app-server";
import styles from "./webShell.module.css";

export type FolderListing = MethodResults["directory/list"];

export interface ServerFolderPickerProps {
  /** Lists a folder on the service machine; no path means the home folder. */
  listDirectories(path?: string): Promise<FolderListing>;
  onClose(path: string | null): void;
}

// Wait this long after the last keystroke before listing a typed path.
const TYPING_DELAY_MS = 400;

/**
 * Choose a project folder on the machine running the service.
 *
 * A browser cannot show a native picker for that machine, so the service lists
 * its folders and this dialog lets the user click through them or type a path.
 */
export function ServerFolderPicker({
  listDirectories,
  onClose,
}: ServerFolderPickerProps) {
  const { t } = useTranslation();
  const [path, setPath] = useState("");
  const [listing, setListing] = useState<FolderListing | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const latestRequest = useRef(0);
  const typingTimer = useRef<number | undefined>(undefined);
  // Read through a ref so a caller passing a fresh function each render does
  // not restart the initial listing.
  const list = useRef(listDirectories);
  useLayoutEffect(() => {
    list.current = listDirectories;
  }, [listDirectories]);

  // Only the newest request may update the dialog; older answers are dropped.
  const load = useCallback(
    async (target?: string): Promise<FolderListing | null> => {
      const request = ++latestRequest.current;
      try {
        const result = await list.current(target);
        if (request !== latestRequest.current) return null;
        setListing(result);
        setStatus("ready");
        return result;
      } catch {
        if (request === latestRequest.current) setStatus("error");
        return null;
      }
    },
    [],
  );

  const browse = (target: string) => {
    setStatus("loading");
    void load(target);
  };

  useEffect(() => {
    void load().then((result) => {
      if (result) setPath((current) => current || result.path);
    });
    return () => window.clearTimeout(typingTimer.current);
  }, [load]);

  const enter = (target: string) => {
    window.clearTimeout(typingTimer.current);
    setPath(target);
    browse(target);
  };

  const type = (value: string) => {
    setPath(value);
    window.clearTimeout(typingTimer.current);
    const target = value.trim();
    if (target)
      typingTimer.current = window.setTimeout(
        () => browse(target),
        TYPING_DELAY_MS,
      );
  };

  let contents;
  if (status === "error") {
    contents = (
      <p className={styles.browserNote}>
        {t(
          "browser.picker.unreadable",
          "This folder can't be listed. You can still type its path.",
        )}
      </p>
    );
  } else if (!listing) {
    contents = (
      <p className={styles.browserNote}>
        {t("browser.picker.loading", "Loading folders…")}
      </p>
    );
  } else {
    contents = (
      <>
        {listing.parent && (
          <button type="button" onClick={() => enter(listing.parent!)}>
            <CornerLeftUp size={14} aria-hidden />
            {t("browser.picker.up", "Up one level")}
          </button>
        )}
        {listing.folders.map((folder) => (
          <button
            type="button"
            key={folder.path}
            onClick={() => enter(folder.path)}
          >
            <Folder size={14} aria-hidden />
            {folder.name}
          </button>
        ))}
        {listing.folders.length === 0 && (
          <p className={styles.browserNote}>
            {t("browser.picker.empty", "No subfolders")}
          </p>
        )}
        {listing.truncated && (
          <p className={styles.browserNote}>
            {t(
              "browser.picker.truncated",
              "Showing the first {{count}} folders",
              {
                count: listing.folders.length,
              },
            )}
          </p>
        )}
      </>
    );
  }

  return (
    // The backdrop keeps a typed path: only Cancel or Escape dismiss it.
    <Dialog
      as="form"
      className={styles.dialog}
      backdropClassName={styles.backdrop}
      labelledBy="directory-title"
      onClose={() => onClose(null)}
      onSubmit={(event) => {
        event.preventDefault();
        if (path.trim()) onClose(path.trim());
      }}
    >
      <h2 id="directory-title">
        {t("browser.picker.title", "Open a project on the service machine")}
      </h2>
      <p>
        {t(
          "browser.picker.body",
          "Choose a folder on the machine running DeepCode, or type its path. It opens as untrusted until you choose to trust it.",
        )}
      </p>
      <label htmlFor="server-folder">
        {t("browser.picker.folder", "Project folder")}
      </label>
      <input
        id="server-folder"
        autoFocus
        value={path}
        onChange={(event) => type(event.target.value)}
        placeholder="/path/to/project"
      />
      <div
        className={styles.browser}
        role="group"
        aria-label={t("browser.picker.folders", "Folders")}
        aria-busy={status === "loading"}
      >
        {contents}
      </div>
      <footer>
        <button type="button" onClick={() => onClose(null)}>
          {t("browser.picker.cancel", "Cancel")}
        </button>
        <button disabled={!path.trim()} type="submit">
          {t("browser.picker.open", "Open project")}
        </button>
      </footer>
    </Dialog>
  );
}
