import {
  Archive,
  Check,
  MoreHorizontal,
  Pencil,
  Trash2,
  X,
} from "lucide-react";
import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";

import type { Thread } from "../../generated/app-server";
import styles from "./SessionRow.module.css";

interface SessionRowProps {
  thread: Thread;
  active: boolean;
  busy: boolean;
  onSelect(threadId: string): void;
  onRename(threadId: string, title: string): Promise<void>;
  onArchive(threadId: string): Promise<void>;
  onDelete(threadId: string): Promise<void>;
}

type RowMode = "closed" | "menu" | "rename" | "archive" | "delete";

export function SessionRow({
  thread,
  active,
  busy,
  onSelect,
  onRename,
  onArchive,
  onDelete,
}: SessionRowProps) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [mode, setMode] = useState<RowMode>("closed");
  const [draft, setDraft] = useState(thread.title);
  const archiveDisabled =
    thread.status === "running" || thread.status === "waiting";

  useEffect(() => {
    if (mode !== "rename") return;
    requestAnimationFrame(() => {
      inputRef.current?.focus();
      inputRef.current?.select();
    });
  }, [mode]);

  useEffect(() => {
    if (mode === "closed" || mode === "rename") return;
    const closeOnOutsidePress = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setMode("closed");
      }
    };
    document.addEventListener("pointerdown", closeOnOutsidePress);
    return () => document.removeEventListener("pointerdown", closeOnOutsidePress);
  }, [mode]);

  useEffect(() => {
    if (mode === "closed") return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMode("closed");
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [mode]);

  const submitRename = async (event: FormEvent) => {
    event.preventDefault();
    const title = draft.trim();
    if (!title) return;
    if (title !== thread.title) {
      await onRename(thread.id, title);
    }
    setMode("closed");
  };

  return (
    <div className={styles.row} data-active={active} ref={rootRef}>
      {mode === "rename" ? (
        <form className={styles.renameForm} onSubmit={(event) => void submitRename(event)}>
          <span
            className={styles.beacon}
            data-status={thread.status}
            aria-hidden="true"
          />
          <label>
            <span className={styles.srOnly}>Rename Session</span>
            <input
              ref={inputRef}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              disabled={busy}
              maxLength={160}
            />
          </label>
          <button
            type="submit"
            disabled={busy || !draft.trim()}
            aria-label="Save Session name"
          >
            <Check size={13} />
          </button>
          <button
            type="button"
            onClick={() => setMode("closed")}
            aria-label="Cancel Session rename"
          >
            <X size={13} />
          </button>
        </form>
      ) : (
        <>
          <button
            type="button"
            className={styles.sessionButton}
            onClick={() => {
              setMode("closed");
              onSelect(thread.id);
            }}
            disabled={busy}
            aria-label={`Open Session ${thread.title}`}
            title={`${thread.title}\n${thread.workspacePath}`}
          >
            <span
              className={styles.beacon}
              data-status={thread.status}
              aria-label={thread.status.replaceAll("_", " ")}
            />
            <span className={styles.copy}>
              <strong>{thread.title}</strong>
            </span>
          </button>
          <button
            className={styles.moreButton}
            type="button"
            onClick={() => setMode((current) => (current === "closed" ? "menu" : "closed"))}
            disabled={busy}
            aria-label={`Session actions for ${thread.title}`}
            aria-expanded={mode !== "closed"}
            aria-haspopup="menu"
          >
            <MoreHorizontal size={15} />
          </button>
        </>
      )}

      {mode === "menu" ? (
        <div className={styles.menu} role="menu" aria-label={`Actions for ${thread.title}`}>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setDraft(thread.title);
              setMode("rename");
            }}
          >
            <Pencil size={13} />
            Rename
          </button>
          <button
            type="button"
            role="menuitem"
            className={styles.archiveAction}
            onClick={() => setMode("archive")}
            disabled={archiveDisabled}
            title={
              archiveDisabled
                ? "Stop active work before archiving this Session."
                : undefined
            }
          >
            <Archive size={13} />
            Archive
          </button>
          <button
            type="button"
            role="menuitem"
            className={styles.deleteAction}
            onClick={() => setMode("delete")}
            disabled={archiveDisabled}
            title={
              archiveDisabled
                ? "Stop active work before deleting this Session."
                : undefined
            }
          >
            <Trash2 size={13} />
            Delete permanently
          </button>
        </div>
      ) : null}

      {mode === "archive" ? (
        <div
          className={styles.confirmation}
          role="alertdialog"
          aria-label={`Archive ${thread.title}`}
        >
          <p>
            Archive this Session?
            <span>Its canonical history is preserved.</span>
          </p>
          <div>
            <button type="button" onClick={() => setMode("closed")}>
              Cancel
            </button>
            <button
              type="button"
              className={styles.confirmArchive}
              disabled={busy}
              onClick={() => {
                setMode("closed");
                void onArchive(thread.id);
              }}
            >
              Archive
            </button>
          </div>
        </div>
      ) : null}

      {mode === "delete" ? (
        <div
          className={styles.confirmation}
          role="alertdialog"
          aria-label={`Delete ${thread.title}`}
        >
          <p>
            Permanently delete this Session?
            <span>
              Conversation and Goal history will be removed. Workspace files stay untouched.
            </span>
          </p>
          <div>
            <button type="button" onClick={() => setMode("closed")}>
              Cancel
            </button>
            <button
              type="button"
              className={styles.confirmDelete}
              disabled={busy}
              onClick={() => {
                setMode("closed");
                void onDelete(thread.id);
              }}
            >
              Delete permanently
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
