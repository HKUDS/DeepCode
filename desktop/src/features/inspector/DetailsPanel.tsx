import { useTranslation } from "react-i18next";

import type { Item } from "../../generated/app-server";
import { itemStatusText, presentItem } from "../execution/itemPresentation";
import { readableCommand } from "../thread/sessionFolder";
import { InspectorEmpty } from "./InspectorEmpty";
import styles from "./Inspector.module.css";

interface DetailsPanelProps {
  selected: Item | null;
  items: Item[];
  /** The Session's folder, left out of a leading `cd` in the title. */
  folder: string | null;
  onSelectItem(itemId: string): void;
}

export function DetailsPanel({
  selected,
  items,
  folder,
  onSelectItem,
}: DetailsPanelProps) {
  const { t } = useTranslation();
  if (!selected) {
    return (
      <div className={styles.content}>
        <InspectorEmpty
          label={t("inspector.details.empty", "Select an activity to inspect it.")}
          compact
        />
        {items.slice(-5).map((item) => (
          <button
            className={styles.detailShortcut}
            key={item.id}
            type="button"
            onClick={() => onSelectItem(item.id)}
          >
            {readableCommand(item.summary, folder)}
          </button>
        ))}
      </div>
    );
  }

  const presentation = presentItem(selected, t);
  return (
    <div className={styles.content}>
      <p className={styles.eyebrow}>{presentation.stage}</p>
      <h2 className={styles.detailTitle}>
        {readableCommand(selected.summary, folder)}
      </h2>
      <dl className={styles.metadata}>
        <div>
          <dt>{t("inspector.details.kind", "Kind")}</dt>
          <dd>{presentation.label}</dd>
        </div>
        <div>
          <dt>{t("inspector.details.status", "Status")}</dt>
          <dd>{itemStatusText(selected.status, t)}</dd>
        </div>
        <div>
          <dt>{t("inspector.details.item", "Item")}</dt>
          <dd>{selected.id}</dd>
        </div>
        <div>
          <dt>{t("inspector.details.turn", "Turn")}</dt>
          <dd>{selected.turnId}</dd>
        </div>
      </dl>
      <h3>{t("inspector.details.payload", "Structured payload")}</h3>
      <pre>{JSON.stringify(selected.payload, null, 2)}</pre>
    </div>
  );
}
