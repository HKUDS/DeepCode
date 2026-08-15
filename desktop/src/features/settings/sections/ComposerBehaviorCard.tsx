/**
 * Enter behavior while busy — dsh's `busyEnter` preference. Machine-local
 * (localStorage), applied immediately; Cmd/Ctrl+Enter always performs the
 * other verb so neither is ever more than one keystroke away.
 */

import {
  BUSY_ENTER_BEHAVIORS,
  useComposerBehavior,
  type BusyEnterBehavior,
} from "../../../app/composerBehavior";
import styles from "../../management/ManagementWorkspace.module.css";

const BEHAVIOR_LABELS: Record<BusyEnterBehavior, string> = {
  steer: "Steer the active Turn",
  queue: "Queue for the next Turn",
};

export function ComposerBehaviorCard() {
  const { busyEnter, setBusyEnter } = useComposerBehavior();
  return (
    <section className={styles.formCard}>
      <header>
        <div>
          <p className={styles.eyebrow}>Composer</p>
          <h2>Enter behavior while busy</h2>
        </div>
      </header>
      <div className={styles.formGrid}>
        <label>
          While a Turn is running, Enter…
          <select
            value={busyEnter}
            onChange={(event) =>
              setBusyEnter(event.target.value as BusyEnterBehavior)
            }
          >
            {BUSY_ENTER_BEHAVIORS.map((behavior) => (
              <option key={behavior} value={behavior}>
                {BEHAVIOR_LABELS[behavior]}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className={styles.note}>
        Busy only; Cmd/Ctrl+Enter uses the other behavior. When idle, Enter
        always sends. Applies immediately on this machine.
      </p>
    </section>
  );
}
