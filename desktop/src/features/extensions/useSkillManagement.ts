import { useCallback, useEffect, useRef, useState } from "react";

import type {
  ConfigScope,
  SkillDetail,
} from "../../generated/app-server";
import type { DesktopRuntime } from "../../rpc/contracts";
import { useSkillCatalog } from "../skills/useSkillCatalog";

interface SkillManagementState {
  projectId: string | null;
  selectedSkill: SkillDetail | null;
  loading: boolean;
  error: string | null;
}

const emptyState: SkillManagementState = {
  projectId: null,
  selectedSkill: null,
  loading: false,
  error: null,
};

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function useSkillManagement(
  runtime: DesktopRuntime,
  projectId: string | null,
) {
  const catalog = useSkillCatalog(runtime, projectId);
  const refreshCatalog = catalog.refresh;
  const replaceCatalog = catalog.replace;
  const [state, setState] = useState<SkillManagementState>(emptyState);
  const generation = useRef(0);

  useEffect(
    () => () => {
      generation.current += 1;
    },
    [projectId],
  );

  const selectSkill = useCallback(
    async (skillId: string) => {
      if (!projectId) return;
      const requestGeneration = ++generation.current;
      setState({
        projectId,
        selectedSkill: null,
        loading: true,
        error: null,
      });
      try {
        const result = await runtime.request("skill/read", {
          projectId,
          skillId,
        });
        if (generation.current !== requestGeneration) return;
        setState({
          projectId,
          selectedSkill: result.skill,
          loading: false,
          error: null,
        });
      } catch (error) {
        if (generation.current !== requestGeneration) return;
        setState({
          projectId,
          selectedSkill: null,
          loading: false,
          error: message(error),
        });
      }
    },
    [projectId, runtime],
  );

  const mutate = useCallback(
    async (operation: (requestGeneration: number) => Promise<void>) => {
      const requestGeneration = ++generation.current;
      setState((current) => ({
        ...current,
        projectId,
        loading: true,
        error: null,
      }));
      try {
        await operation(requestGeneration);
        if (generation.current !== requestGeneration) return;
        setState((current) => ({ ...current, loading: false }));
      } catch (error) {
        if (generation.current !== requestGeneration) return;
        setState((current) => ({
          ...current,
          loading: false,
          error: message(error),
        }));
      }
    },
    [projectId],
  );

  const importSkill = useCallback(
    (path: string, scope: ConfigScope) =>
      mutate(async (requestGeneration) => {
        if (!projectId) return;
        const result = await runtime.request("skills/import", {
          projectId,
          path,
          scope,
        });
        if (generation.current !== requestGeneration) return;
        await refreshCatalog();
        if (generation.current !== requestGeneration) return;
        setState((current) => ({
          ...current,
          selectedSkill: result.skill,
        }));
      }),
    [mutate, projectId, refreshCatalog, runtime],
  );

  const setEnabled = useCallback(
    (skillId: string, enabled: boolean, scope: ConfigScope) =>
      mutate(async (requestGeneration) => {
        if (!projectId) return;
        const result = await runtime.request("skills/set-enabled", {
          projectId,
          skillId,
          enabled,
          scope,
        });
        if (generation.current !== requestGeneration) return;
        replaceCatalog(result);
        setState((current) => ({
          ...current,
          selectedSkill:
            current.selectedSkill?.id === skillId
              ? null
              : current.selectedSkill,
        }));
      }),
    [mutate, projectId, replaceCatalog, runtime],
  );

  const deleteSkill = useCallback(
    (skillId: string) =>
      mutate(async (requestGeneration) => {
        if (!projectId) return;
        await runtime.request("skills/delete", { projectId, skillId });
        if (generation.current !== requestGeneration) return;
        await refreshCatalog();
        if (generation.current !== requestGeneration) return;
        setState((current) => ({ ...current, selectedSkill: null }));
      }),
    [mutate, projectId, refreshCatalog, runtime],
  );

  const visible =
    state.projectId === projectId
      ? state
      : { ...emptyState, projectId };

  const refresh = useCallback(async () => {
    const requestGeneration = ++generation.current;
    await refreshCatalog();
    if (generation.current !== requestGeneration) return;
    setState({ ...emptyState, projectId });
  }, [projectId, refreshCatalog]);

  return {
    ...catalog,
    selectedSkill: visible.selectedSkill,
    loading: catalog.loading || visible.loading,
    error: catalog.error ?? visible.error,
    refresh,
    selectSkill,
    importSkill,
    setEnabled,
    deleteSkill,
  };
}
