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
import { useTranslation } from "react-i18next";

import { useEscapeLayer } from "../../app/escapeLayer";
import { Dialog } from "../../components/Dialog";
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
  const { t } = useTranslation();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const moreRef = useRef<HTMLButtonElement | null>(null);
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

  // The menu and the inline rename close themselves; the archive and delete
  // confirmations are dialogs and handle Escape and outside presses there.
  useEffect(() => {
    if (mode !== "menu") return;
    const closeOnOutsidePress = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setMode("closed");
      }
    };
    document.addEventListener("pointerdown", closeOnOutsidePress);
    return () => document.removeEventListener("pointerdown", closeOnOutsidePress);
  }, [mode]);

  // On the shared Escape stack, so Escape closes this menu and not also the
  // drawer or dialog around it.
  useEscapeLayer(() => setMode("closed"), mode === "menu" || mode === "rename");

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
            <span className={styles.srOnly}>
              {t("session.rename", "Rename Session")}
            </span>
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
            aria-label={t("session.saveName", "Save Session name")}
          >
            <Check size={14} />
          </button>
          <button
            type="button"
            onClick={() => setMode("closed")}
            aria-label={t("session.cancelRename", "Cancel Session rename")}
          >
            <X size={14} />
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
            aria-label={t("session.open", "Open Session {{title}}", {
              title: thread.title,
            })}
            title={`${thread.title}\n${thread.workspacePath}`}
          >
            <span
              className={styles.beacon}
              data-status={thread.status}
              aria-label={t(`session.status.${thread.status}`, thread.status)}
            />
            <span className={styles.copy}>
              <strong>{thread.title}</strong>
            </span>
          </button>
          <button
            ref={moreRef}
            className={styles.moreButton}
            type="button"
            onClick={() => setMode((current) => (current === "closed" ? "menu" : "closed"))}
            disabled={busy}
            aria-label={t("session.actionsFor", "Session actions for {{title}}", {
              title: thread.title,
            })}
            aria-expanded={mode !== "closed"}
            aria-haspopup="menu"
          >
            <MoreHorizontal size={16} />
          </button>
        </>
      )}

      {mode === "menu" ? (
        <div
          className={styles.menu}
          role="menu"
          aria-label={t("session.actions", "Actions for {{title}}", {
            title: thread.title,
          })}
        >
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setDraft(thread.title);
              setMode("rename");
            }}
          >
            <Pencil size={14} />
            {t("session.renameAction", "Rename")}
          </button>
          <button
            type="button"
            role="menuitem"
            className={styles.archiveAction}
            onClick={() => setMode("archive")}
            disabled={archiveDisabled}
            title={
              archiveDisabled
                ? t(
                    "session.archiveBlocked",
                    "Stop active work before archiving this Session.",
                  )
                : undefined
            }
          >
            <Archive size={14} />
            {t("session.archive", "Archive")}
          </button>
          <button
            type="button"
            role="menuitem"
            className={styles.deleteAction}
            onClick={() => setMode("delete")}
            disabled={archiveDisabled}
            title={
              archiveDisabled
                ? t(
                    "session.deleteBlocked",
                    "Stop active work before deleting this Session.",
                  )
                : undefined
            }
          >
            <Trash2 size={14} />
            {t("session.delete", "Delete permanently")}
          </button>
        </div>
      ) : null}

      {mode === "archive" ? (
        <Dialog
          className={styles.confirmation}
          role="alertdialog"
          label={t("session.archiveTitle", "Archive {{title}}", {
            title: thread.title,
          })}
          dismissOnPressOutside={rootRef}
          returnFocus={moreRef}
          onClose={() => setMode("closed")}
        >
          <p>
            {t("session.archiveConfirm", "Archive this Session?")}
            <span>
              {t("session.archiveNote", "Its canonical history is preserved.")}
            </span>
          </p>
          <div>
            <button type="button" onClick={() => setMode("closed")}>
              {t("session.cancel", "Cancel")}
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
              {t("session.archive", "Archive")}
            </button>
          </div>
        </Dialog>
      ) : null}

      {mode === "delete" ? (
        <Dialog
          className={styles.confirmation}
          role="alertdialog"
          label={t("session.deleteTitle", "Delete {{title}}", {
            title: thread.title,
          })}
          dismissOnPressOutside={rootRef}
          returnFocus={moreRef}
          onClose={() => setMode("closed")}
        >
          <p>
            {t("session.deleteConfirm", "Permanently delete this Session?")}
            <span>
              {t(
                "session.deleteNote",
                "Conversation and Goal history will be removed. Workspace files stay untouched.",
              )}
            </span>
          </p>
          <div>
            <button type="button" onClick={() => setMode("closed")}>
              {t("session.cancel", "Cancel")}
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
              {t("session.delete", "Delete permanently")}
            </button>
          </div>
        </Dialog>
      ) : null}
    </div>
  );
}
