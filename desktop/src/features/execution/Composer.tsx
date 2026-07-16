import {
  ArrowUp,
  Cpu,
  Paperclip,
  ShieldCheck,
  Square,
  X,
} from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import type {
  Project,
  SettingsSnapshot,
  Thread,
} from "../../generated/app-server";
import type { DesktopPermissionMode } from "../../app/useWorkspaceController";
import {
  matchingCommands,
  parseComposerCommand,
  type ComposerCommand,
} from "./commands";
import styles from "./Composer.module.css";
import { usePromptDraft } from "./usePromptDraft";

interface ComposerProps {
  enabled: boolean;
  busy: boolean;
  active: boolean;
  project: Project | null;
  thread: Thread | null;
  settings: SettingsSnapshot | null;
  disabledReason: string | null;
  onModelChange(model: string | null): void;
  onPermissionModeChange(mode: DesktopPermissionMode): void;
  onPickContextFiles(): Promise<string[]>;
  onCommand(command: ComposerCommand): Promise<boolean>;
  onSubmit(prompt: string): Promise<void>;
  onQueue(prompt: string): Promise<void>;
  onInterrupt(): void;
}

export function Composer({
  enabled,
  busy,
  active,
  project,
  thread,
  settings,
  disabledReason,
  onModelChange,
  onPermissionModeChange,
  onPickContextFiles,
  onCommand,
  onSubmit,
  onQueue,
  onInterrupt,
}: ComposerProps) {
  const {
    prompt,
    setPrompt,
    record,
    browse,
    browsingHistory,
    attachments,
    addAttachments,
    removeAttachment,
    clearAttachments,
  } = usePromptDraft(thread?.id ?? "unselected");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [contextError, setContextError] = useState<string | null>(null);
  const [commandError, setCommandError] = useState<string | null>(null);
  const defaultModel = settingsDefaultModel(settings);
  const permissionMode = settingsPermissionMode(settings);
  const modelOptions = settings?.models ?? [];

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "0px";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 190)}px`;
  }, [prompt]);

  const submit = async () => {
    const value = prompt.trim();
    if (!value || !enabled || busy) return;
    const parsed = parseComposerCommand(value);
    if (parsed) {
      if (!parsed.ok) {
        setCommandError(parsed.message);
        return;
      }
      record(value);
      if (!(await onCommand(parsed.command))) return;
      setPrompt("");
      setCommandError(null);
      return;
    }
    record(value);
    const executionPrompt = withContextFiles(
      value,
      attachments,
      thread?.workspacePath,
    );
    if (active) {
      await onQueue(executionPrompt);
    } else {
      await onSubmit(executionPrompt);
    }
    setPrompt("");
    clearAttachments();
  };
  const commandSuggestions = matchingCommands(prompt);

  const pickContextFiles = async () => {
    setContextError(null);
    const selected = await onPickContextFiles();
    const workspace = thread?.workspacePath;
    const accepted = workspace
      ? selected.filter((path) => isInsideWorkspace(path, workspace))
      : [];
    if (accepted.length !== selected.length) {
      setContextError("Only files inside this Session workspace can be attached.");
    }
    addAttachments(accepted);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault();
      void submit();
      return;
    }
    if (
      event.key === "ArrowUp" &&
      (!prompt || browsingHistory) &&
      !event.shiftKey &&
      !event.metaKey &&
      !event.ctrlKey
    ) {
      event.preventDefault();
      browse("older");
    } else if (
      event.key === "ArrowDown" &&
      browsingHistory &&
      !event.shiftKey &&
      !event.metaKey &&
      !event.ctrlKey
    ) {
      browse("newer");
    }
  };

  return (
    <footer className={styles.region}>
      <div className={styles.composer}>
        <label htmlFor="turn-prompt">Task instruction</label>
        <textarea
          ref={textareaRef}
          id="turn-prompt"
          value={prompt}
          onChange={(event) => {
            setPrompt(event.target.value);
            setCommandError(null);
          }}
          onKeyDown={onKeyDown}
          placeholder={
            active
              ? "Prepare the next instruction while DeepCode is working…"
              : "Ask DeepCode to build, inspect, or verify…"
          }
          rows={1}
          disabled={!enabled}
        />
        {commandSuggestions.length ? (
          <div className={styles.commandMenu} role="listbox" aria-label="Commands">
            {commandSuggestions.map((command) => (
              <button
                type="button"
                role="option"
                aria-selected={false}
                key={command.name}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => {
                  setPrompt(command.usage);
                  textareaRef.current?.focus();
                }}
              >
                <code>/{command.name}</code>
                <span>{command.description}</span>
              </button>
            ))}
          </div>
        ) : null}
        {attachments.length ? (
          <div className={styles.attachments} aria-label="Attached context files">
            {attachments.map((path) => (
              <span key={path} title={path}>
                <Paperclip size={11} />
                {fileName(path)}
                <button
                  type="button"
                  onClick={() => removeAttachment(path)}
                  aria-label={`Remove ${fileName(path)}`}
                >
                  <X size={11} />
                </button>
              </span>
            ))}
          </div>
        ) : null}
        <div className={styles.toolbar}>
          <div className={styles.context}>
            <button
              className={styles.attachButton}
              type="button"
              onClick={() => void pickContextFiles()}
              disabled={!enabled || busy}
              aria-label="Attach workspace files"
              title="Attach workspace files"
            >
              <Paperclip size={13} />
            </button>
            <span title={thread?.workspacePath ?? project?.canonicalPath}>
              {thread?.mode === "paper" ? "Paper2Code" : "Local"}
            </span>
            <label className={styles.selector} title="Model for this Session">
              <Cpu size={12} />
              <select
                aria-label="Session model"
                value={thread?.model ?? ""}
                onChange={(event) => onModelChange(event.target.value || null)}
                disabled={busy || active}
              >
                <option value="">
                  {defaultModel ? `Default · ${defaultModel}` : "Configured model"}
                </option>
                {thread?.model &&
                !modelOptions.some((model) => model.id === thread.model) ? (
                  <option value={thread.model}>{thread.model}</option>
                ) : null}
                {modelOptions.map((model) => (
                  <option value={model.id} key={model.id}>
                    {model.id}
                  </option>
                ))}
              </select>
            </label>
            <label className={styles.selector} title="Tool permission mode">
              <ShieldCheck size={12} />
              <select
                aria-label="Permission mode"
                value={permissionMode}
                onChange={(event) =>
                  onPermissionModeChange(
                    event.target.value as DesktopPermissionMode,
                  )
                }
                disabled={busy || active}
              >
                <option value="default">Approval first</option>
                <option value="plan">Plan only</option>
                <option value="full_auto">Full auto</option>
              </select>
            </label>
          </div>
          {active ? (
            <div className={styles.activeActions}>
              <button
                className={styles.queueButton}
                type="button"
                onClick={() => void submit()}
                disabled={!enabled || busy || !prompt.trim()}
              >
                Queue
              </button>
              <button
                className={styles.stopButton}
                type="button"
                onClick={onInterrupt}
                aria-label="Stop turn"
              >
                <Square size={13} fill="currentColor" />
                Stop
              </button>
            </div>
          ) : (
            <button
              className={styles.sendButton}
              type="button"
              onClick={() => void submit()}
              disabled={!enabled || busy || !prompt.trim()}
              aria-label="Run turn"
            >
              <ArrowUp size={17} strokeWidth={2.2} />
            </button>
          )}
        </div>
      </div>
      <p className={styles.hint}>
        {commandError ??
          contextError ??
          disabledReason ??
          "DeepCode may ask before sensitive tools run."}
        <span>{active ? "↵ queue" : "↵ send"} · ⇧↵ newline</span>
      </p>
    </footer>
  );
}

function normalizedPath(path: string): string {
  return path.replaceAll("\\", "/").replace(/\/+$/, "");
}

function isInsideWorkspace(path: string, workspace: string): boolean {
  const candidate = normalizedPath(path);
  const root = normalizedPath(workspace);
  return candidate === root || candidate.startsWith(`${root}/`);
}

function fileName(path: string): string {
  return normalizedPath(path).split("/").at(-1) ?? path;
}

function withContextFiles(
  prompt: string,
  paths: string[],
  workspace: string | undefined,
): string {
  if (!paths.length) return prompt;
  const root = workspace ? normalizedPath(workspace) : "";
  const references = paths.map((path) => {
    const normalized = normalizedPath(path);
    return normalized.startsWith(`${root}/`)
      ? normalized.slice(root.length + 1)
      : normalized;
  });
  return [
    prompt,
    "",
    "Attached workspace context:",
    ...references.map((path) => `- ${path}`),
  ].join("\n");
}

function settingsDefaultModel(settings: SettingsSnapshot | null): string | null {
  const defaults = settings?.agents.defaults;
  if (typeof defaults !== "object" || defaults === null || Array.isArray(defaults)) {
    return null;
  }
  const model = defaults.model;
  return typeof model === "string" && model ? model : null;
}

function settingsPermissionMode(
  settings: SettingsSnapshot | null,
): DesktopPermissionMode {
  if (!settings?.permissionModeExplicit) return "default";
  const mode = settings.security.permissionMode;
  return mode === "plan" || mode === "full_auto" || mode === "default"
    ? mode
    : "default";
}
