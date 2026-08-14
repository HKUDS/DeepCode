import styles from "./Inspector.module.css";

export function InspectorEmpty({
  label,
  compact = false,
}: {
  label: string;
  compact?: boolean;
}) {
  return (
    <div className={styles.empty} data-compact={compact}>
      <span className={styles.emptyRule} aria-hidden="true" />
      <p>{label}</p>
    </div>
  );
}
