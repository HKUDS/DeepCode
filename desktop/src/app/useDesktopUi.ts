import { useCallback, useMemo, useState } from "react";

import { confirmAction } from "../platform/confirmAction";

export type DesktopDestination =
  | "threads"
  | "automations"
  | "skills"
  | "settings";
export type DesktopInspectorTab =
  | "changes"
  | "files"
  | "artifacts"
  | "tests"
  | "terminal"
  | "details";

export interface DesktopUiState {
  destination: DesktopDestination;
  sessionQuery: string;
  inspectorOpen: boolean;
  inspectorTab: DesktopInspectorTab;
  inspectorDirty: boolean;
}

export interface DesktopUiController extends DesktopUiState {
  setDestination(destination: DesktopDestination): void;
  navigateTo(destination: DesktopDestination): Promise<boolean>;
  setSessionQuery(query: string): void;
  openInspector(tab?: DesktopInspectorTab): void;
  closeInspector(): Promise<void>;
  toggleInspector(): Promise<void>;
  setInspectorTab(tab: DesktopInspectorTab): void;
  setInspectorDirty(dirty: boolean): void;
  confirmDiscardInspectorDraft(): Promise<boolean>;
}

export function useDesktopUi(): DesktopUiController {
  const [destination, setDestination] = useState<DesktopDestination>("threads");
  const [sessionQuery, setSessionQuery] = useState("");
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [inspectorTab, setInspectorTab] =
    useState<DesktopInspectorTab>("changes");
  const [inspectorDirty, setInspectorDirty] = useState(false);

  const confirmDiscardInspectorDraft = useCallback(async () => {
    if (
      inspectorDirty &&
      !(await confirmAction(
        "Discard the unsaved editor draft and continue? Your edits cannot be recovered.",
        {
          confirmLabel: "Discard draft",
        },
      ))
    ) {
      return false;
    }
    setInspectorDirty(false);
    return true;
  }, [inspectorDirty]);
  const navigateTo = useCallback(
    async (nextDestination: DesktopDestination) => {
      if (!(await confirmDiscardInspectorDraft())) return false;
      setDestination(nextDestination);
      if (nextDestination !== "threads") setInspectorOpen(false);
      return true;
    },
    [confirmDiscardInspectorDraft],
  );

  const openInspector = useCallback((tab?: DesktopInspectorTab) => {
    if (tab) setInspectorTab(tab);
    setInspectorOpen(true);
  }, []);
  const closeInspector = useCallback(async () => {
    if (await confirmDiscardInspectorDraft()) setInspectorOpen(false);
  }, [confirmDiscardInspectorDraft]);
  const toggleInspector = useCallback(async () => {
    if (inspectorOpen) {
      if (await confirmDiscardInspectorDraft()) setInspectorOpen(false);
      return;
    }
    setInspectorOpen(true);
  }, [confirmDiscardInspectorDraft, inspectorOpen]);

  return useMemo(
    () => ({
      destination,
      sessionQuery,
      inspectorOpen,
      inspectorTab,
      inspectorDirty,
      setDestination,
      navigateTo,
      setSessionQuery,
      openInspector,
      closeInspector,
      toggleInspector,
      setInspectorTab,
      setInspectorDirty,
      confirmDiscardInspectorDraft,
    }),
    [
      closeInspector,
      destination,
      inspectorOpen,
      inspectorTab,
      inspectorDirty,
      navigateTo,
      openInspector,
      sessionQuery,
      toggleInspector,
      confirmDiscardInspectorDraft,
    ],
  );
}
