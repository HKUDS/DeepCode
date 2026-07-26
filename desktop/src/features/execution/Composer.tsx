import {
  ArrowUp,
  Check,
  Paperclip,
  ShieldCheck,
  Sparkles,
  Square,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import type {
  Goal,
  Project,
  SettingsSnapshot,
  Thread,
} from "../../generated/app-server";
import type { DesktopPermissionMode } from "../../app/useWorkspaceController";
import type { GoalDefinitionInput } from "../../app/useWorkspaceController";
import type { DesktopRuntime } from "../../rpc/contracts";
import { useSkillCatalog } from "../skills/useSkillCatalog";
import { GoalRail } from "../goal/GoalRail";
import {
  matchingCommands,
  parseComposerCommand,
  type ComposerCommand,
} from "./commands";
import styles from "./Composer.module.css";
import { usePromptDraft } from "./usePromptDraft";
import { ModelPicker } from "./ModelPicker";

interface ComposerProps {
  editable: boolean;
  canExecute: boolean;
  busy: boolean;
  active: boolean;
  runtime: DesktopRuntime;
  project: Project | null;
  thread: Thread | null;
  settings: SettingsSnapshot | null;
  goal: Goal | null;
  disabledReason: string | null;
  onModelChange(
    connectionId: string | null,
    model: string | null,
    reasoningEffort: string | null,
  ): void;
  onPermissionModeChange(mode: DesktopPermissionMode): void;
  onSetGoal(input: GoalDefinitionInput): Promise<void>;
  onPauseGoal(): Promise<void>;
  onResumeGoal(): Promise<void>;
  onClearGoal(): Promise<void>;
  onPickContextFiles(): Promise<string[]>;
  onCommand(command: ComposerCommand): Promise<boolean>;
  onSubmit(prompt: string, skillIds?: string[]): Promise<void>;
  onQueue(prompt: string, skillIds?: string[]): Promise<void>;
  onInterrupt(): void;
}

export function Composer({
  editable,
  canExecute,
  busy,
  active,
  runtime,
  project,
  thread,
  settings,
  goal,
  disabledReason,
  onModelChange,
  onPermissionModeChange,
  onSetGoal,
  onPauseGoal,
  onResumeGoal,
  onClearGoal,
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
  const [skillPickerOpen, setSkillPickerOpen] = useState(false);
  const [skillQuery, setSkillQuery] = useState("");
  const [selectedSkillIds, setSelectedSkillIds] = useState<string[]>([]);
  const skillCatalog = useSkillCatalog(runtime, project?.id ?? null);
  const availableSkills = useMemo(() => {
    const query = skillQuery.trim().toLocaleLowerCase();
    return skillCatalog.activeSkills.filter(
      (skill) =>
        !query ||
        skill.name.toLocaleLowerCase().includes(query) ||
        skill.description.toLocaleLowerCase().includes(query),
    );
  }, [skillCatalog.activeSkills, skillQuery]);
  const selectedSkills = useMemo(() => {
    const byId = new Map(skillCatalog.skills.map((skill) => [skill.id, skill]));
    return selectedSkillIds.flatMap((skillId) => {
      const skill = byId.get(skillId);
      return skill ? [skill] : [];
    });
  }, [selectedSkillIds, skillCatalog.skills]);
  const permissionMode = settingsPermissionMode(settings);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "0px";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 190)}px`;
  }, [prompt]);

  const submit = async () => {
    const value = prompt.trim();
    if (!value || !canExecute || busy) return;
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
    const selectedIds = selectedSkills.map((skill) => skill.id);
    if (active) {
      await onQueue(executionPrompt, selectedIds);
    } else {
      await onSubmit(executionPrompt, selectedIds);
    }
    setPrompt("");
    clearAttachments();
    setSelectedSkillIds([]);
    setSkillPickerOpen(false);
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
      <GoalRail
        goal={goal}
        enabled={canExecute}
        busy={busy}
        skills={skillCatalog.activeSkills}
        onSet={onSetGoal}
        onPause={onPauseGoal}
        onResume={onResumeGoal}
        onClear={onClearGoal}
      />
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
          disabled={!editable}
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
        {skillPickerOpen ? (
          <section className={styles.skillMenu} aria-label="Select Skills">
            <header>
              <div>
                <strong>Skills for this turn</strong>
                <span>Choose up to 8. You can also type $name.</span>
              </div>
              <button
                type="button"
                onClick={() => setSkillPickerOpen(false)}
                aria-label="Close Skill picker"
              >
                <X size={13} />
              </button>
            </header>
            <input
              value={skillQuery}
              onChange={(event) => setSkillQuery(event.target.value)}
              placeholder="Filter Skills"
              aria-label="Filter Skills"
              autoFocus
            />
            <div className={styles.skillOptions} role="listbox" aria-multiselectable>
              {availableSkills.length ? (
                availableSkills.map((skill) => {
                  const selected = selectedSkillIds.includes(skill.id);
                  return (
                    <button
                      type="button"
                      role="option"
                      aria-selected={selected}
                      key={skill.id}
                      onClick={() =>
                        setSelectedSkillIds((current) => {
                          const selectable = new Set(
                            skillCatalog.activeSkills.map((entry) => entry.id),
                          );
                          const valid = current.filter((skillId) =>
                            selectable.has(skillId),
                          );
                          return selected
                            ? valid.filter((skillId) => skillId !== skill.id)
                            : valid.length < 8
                              ? [...valid, skill.id]
                              : valid;
                        })
                      }
                    >
                      <span className={styles.skillCheck}>
                        {selected ? <Check size={11} /> : null}
                      </span>
                      <span>
                        <strong>{skill.name}</strong>
                        <small>{skill.description}</small>
                      </span>
                      <em>{skill.source.replace(":", " · ")}</em>
                    </button>
                  );
                })
              ) : (
                <p>
                  {skillCatalog.loading
                    ? "Loading Skills…"
                    : skillCatalog.error ?? "No matching Skills."}
                </p>
              )}
            </div>
          </section>
        ) : null}
        {selectedSkills.length ? (
          <div className={styles.skills} aria-label="Selected Skills">
            {selectedSkills.map((skill) => (
              <span key={skill.id} title={skill.description}>
                <Sparkles size={11} />
                {skill.name}
                <button
                  type="button"
                  onClick={() =>
                    setSelectedSkillIds((current) =>
                      current.filter((skillId) => skillId !== skill.id),
                    )
                  }
                  aria-label={`Remove ${skill.name}`}
                >
                  <X size={11} />
                </button>
              </span>
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
              disabled={!editable || busy}
              aria-label="Attach workspace files"
              title="Attach workspace files"
            >
              <Paperclip size={13} />
            </button>
            <button
              className={styles.skillButton}
              type="button"
              onClick={() => setSkillPickerOpen((open) => !open)}
              disabled={!editable || busy || !skillCatalog.activeSkills.length}
              aria-expanded={skillPickerOpen}
              aria-label="Select Skills for this turn"
              title={
                skillCatalog.activeSkills.length
                  ? "Select Skills for this turn"
                  : "No selectable Skills"
              }
            >
              <Sparkles size={13} />
              {selectedSkills.length ? <b>{selectedSkills.length}</b> : null}
            </button>
            <span title={thread?.workspacePath ?? project?.canonicalPath}>
              {thread?.mode === "paper" ? "Paper2Code" : "Local"}
            </span>
            <ModelPicker
              runtime={runtime}
              project={project}
              thread={thread}
              settings={settings}
              disabled={busy || active}
              onChange={onModelChange}
            />
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
                disabled={!canExecute || busy || !prompt.trim()}
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
              disabled={!canExecute || busy || !prompt.trim()}
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

function settingsPermissionMode(
  settings: SettingsSnapshot | null,
): DesktopPermissionMode {
  if (!settings?.permissionModeExplicit) return "default";
  const mode = settings.security.permissionMode;
  return mode === "plan" || mode === "full_auto" || mode === "default"
    ? mode
    : "default";
}
