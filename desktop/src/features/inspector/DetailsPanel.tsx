import type { Item } from "../../generated/app-server";
import { presentItem } from "../execution/itemPresentation";
import { InspectorEmpty } from "./InspectorEmpty";
import styles from "./Inspector.module.css";

interface DetailsPanelProps {
  selected: Item | null;
  items: Item[];
  onSelectItem(itemId: string): void;
}

export function DetailsPanel({
  selected,
  items,
  onSelectItem,
}: DetailsPanelProps) {
  if (!selected) {
    return (
      <div className={styles.content}>
        <InspectorEmpty label="Select an activity to inspect it." compact />
        {items.slice(-5).map((item) => (
          <button
            className={styles.detailShortcut}
            key={item.id}
            type="button"
            onClick={() => onSelectItem(item.id)}
          >
            {item.summary}
          </button>
        ))}
      </div>
    );
  }

  return (
    <div className={styles.content}>
      <p className={styles.eyebrow}>{presentItem(selected).stage}</p>
      <h2>{selected.summary}</h2>
      <dl className={styles.metadata}>
        <div>
          <dt>Kind</dt>
          <dd>{selected.kind.replaceAll("_", " ")}</dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd>{selected.status.replaceAll("_", " ")}</dd>
        </div>
        <div>
          <dt>Item</dt>
          <dd>{selected.id}</dd>
        </div>
        <div>
          <dt>Turn</dt>
          <dd>{selected.turnId}</dd>
        </div>
      </dl>
      <h3>Structured payload</h3>
      <pre>{JSON.stringify(selected.payload, null, 2)}</pre>
    </div>
  );
}
