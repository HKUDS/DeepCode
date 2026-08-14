import { Check, ChevronDown } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { AgentPresetEntry } from "../../generated/app-server";
import styles from "./PresetPicker.module.css";

interface PresetPickerProps {
  entries: AgentPresetEntry[];
  current: string | null;
  /** True once the thread has any Turn — the preset is then fixed. */
  locked: boolean;
  busy: boolean;
  error: string | null;
  onSelect(presetId: string | null): void;
}

const LOCKED_HINT =
  "This session already started; its agent preset is fixed. Start a new session to use a different one.";
const PICK_HINT = "Agent preset for this session";
const DEFAULT_DESCRIPTION = "Full tool set and the built-in prompt.";

/** Agent-preset control for the composer toolbar.
 *
 * Mirrors the ModelPicker popover language: a compact trigger while the
 * session is blank, a disabled label of what the session runs once the
 * conversation has started (the dsh pattern — the same control, two honest
 * states). Broken presets are never offered: choosing one would only defer
 * its failure to session start.
 */
export function PresetPicker({
  entries,
  current,
  locked,
  busy,
  error,
  onSelect,
}: PresetPickerProps) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  const selectable = entries.filter((entry) => entry.broken === null);
  // Nothing to offer and nothing to report: the control vanishes entirely.
  if (selectable.length === 0 && current === null) return null;

  const active = selectable.find((entry) => entry.id === current) ?? null;
  const currentLabel = active?.name ?? current ?? "Default";
  const pick = (presetId: string | null) => {
    setOpen(false);
    if (presetId !== (current ?? null)) onSelect(presetId);
  };

  return (
    <div className={styles.picker} ref={rootRef}>
      <button
        type="button"
        className={styles.trigger}
        onClick={() => setOpen((value) => !value)}
        disabled={locked || busy}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label="Agent preset"
        title={error ?? (locked ? LOCKED_HINT : PICK_HINT)}
      >
        <span className={styles.robotIcon} aria-hidden="true" />
        <span>
          <small>Agent</small>
          <strong>{currentLabel}</strong>
        </span>
        {locked ? null : <ChevronDown size={12} />}
      </button>

      {open ? (
        <section className={styles.menu} aria-label="Choose an agent preset">
          <header>
            <strong>Agent for this session</strong>
            <span>Persona and tools · fixed after the first message</span>
          </header>
          <div className={styles.options} role="listbox">
            <button
              type="button"
              role="option"
              aria-selected={current === null}
              onClick={() => pick(null)}
            >
              <span>
                <strong>Default</strong>
                <em>{DEFAULT_DESCRIPTION}</em>
              </span>
              {current === null ? <Check size={12} /> : null}
            </button>
            {selectable.map((entry) => (
              <button
                type="button"
                role="option"
                aria-selected={entry.id === current}
                key={entry.id}
                onClick={() => pick(entry.id)}
              >
                <span>
                  <strong>
                    {entry.name}
                    {entry.trust === "system" ? null : (
                      <small> · {entry.trust}</small>
                    )}
                  </strong>
                  {entry.description ? <em>{entry.description}</em> : null}
                </span>
                {entry.id === current ? <Check size={12} /> : null}
              </button>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
