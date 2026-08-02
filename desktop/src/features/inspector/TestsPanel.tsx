import type { Turn } from "../../generated/app-server";
import type { CodeWorkbenchController } from "../workbench/useCodeWorkbench";
import { InspectorEmpty } from "./InspectorEmpty";
import styles from "./Inspector.module.css";

interface TestsPanelProps {
  trusted: boolean;
  hasActiveTurn: boolean;
  latestTurn: Turn | null;
  workbench: CodeWorkbenchController;
}

export function TestsPanel({
  trusted,
  hasActiveTurn,
  latestTurn,
  workbench,
}: TestsPanelProps) {
  return (
    <div className={styles.content}>
      <p className={styles.eyebrow}>Verification</p>
      <h2>Project test commands</h2>
      {workbench.tests.length ? (
        <div className={styles.testList}>
          {workbench.tests.map((command) => (
            <button
              key={command.id}
              type="button"
              disabled={
                !trusted || hasActiveTurn || !latestTurn || workbench.loading
              }
              onClick={() =>
                latestTurn && void workbench.runTest(latestTurn.id, command.id)
              }
            >
              <strong>{command.label}</strong>
              <code>{command.argv.join(" ")}</code>
            </button>
          ))}
        </div>
      ) : (
        <InspectorEmpty label="No supported test command was detected." compact />
      )}
      {workbench.testError ? (
        <p className={styles.panelError}>{workbench.testError}</p>
      ) : null}
      {!latestTurn ? (
        <p className={styles.note}>
          Complete a Turn before attaching a durable TestResult.
        </p>
      ) : hasActiveTurn ? (
        <p className={styles.note}>
          Verification is locked while a Turn is active.
        </p>
      ) : null}
      {workbench.lastTestRun ? (
        <section
          className={styles.testResult}
          data-passed={
            workbench.lastTestRun.exitCode === 0 &&
            !workbench.lastTestRun.timedOut
          }
          aria-live="polite"
        >
          <header>
            <strong>{workbench.lastTestRun.command.label}</strong>
            <span>
              {workbench.lastTestRun.timedOut
                ? "Timed out"
                : `Exit ${workbench.lastTestRun.exitCode ?? "unknown"}`}
              {" · "}
              {workbench.lastTestRun.durationMs} ms
            </span>
          </header>
          {workbench.lastTestRun.stdout ? (
            <pre>{workbench.lastTestRun.stdout}</pre>
          ) : null}
          {workbench.lastTestRun.stderr ? (
            <pre>{workbench.lastTestRun.stderr}</pre>
          ) : null}
          {workbench.lastTestRun.outputTruncated ? (
            <small>Output was truncated to the bounded tail.</small>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
