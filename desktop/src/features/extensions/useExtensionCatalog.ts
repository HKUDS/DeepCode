import { useCallback, useEffect, useRef, useState } from "react";

import type {
  ConfigScope,
  HookInfo,
  SkillDetail,
} from "../../generated/app-server";
import type { DesktopRuntime } from "../../rpc/contracts";
import { useSkillCatalog } from "../skills/useSkillCatalog";

interface ExtensionState {
  projectId: string | null;
  hooks: HookInfo[];
  hooksWarnings: string[];
  hooksTruncated: boolean;
  selectedSkill: SkillDetail | null;
  loading: boolean;
  error: string | null;
}

const emptyState: ExtensionState = {
  projectId: null,
  hooks: [],
  hooksWarnings: [],
  hooksTruncated: false,
  selectedSkill: null,
  loading: false,
  error: null,
};

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function useExtensionCatalog(
  runtime: DesktopRuntime,
  projectId: string | null,
) {
  const skillCatalog = useSkillCatalog(runtime, projectId);
  const refreshSkills = skillCatalog.refresh;
  const replaceSkills = skillCatalog.replace;
  const [state, setState] = useState<ExtensionState>(emptyState);
  const hooksGeneration = useRef(0);
  const skillGeneration = useRef(0);

  const loadHooks = useCallback(async () => {
    const requestGeneration = ++hooksGeneration.current;
    if (!projectId) {
      setState(emptyState);
      return;
    }
    setState((current) =>
      current.projectId === projectId
        ? { ...current, loading: true, error: null }
        : { ...emptyState, projectId, loading: true },
    );
    try {
      const result = await runtime.request("hooks/list", { projectId });
      if (hooksGeneration.current !== requestGeneration) return;
      setState((current) => ({
        ...current,
        projectId,
        hooks: result.hooks,
        hooksWarnings: result.warnings,
        hooksTruncated: result.truncated,
        loading: false,
        error: null,
      }));
    } catch (error) {
      if (hooksGeneration.current !== requestGeneration) return;
      setState((current) => ({
        ...current,
        loading: false,
        error: message(error),
      }));
    }
  }, [projectId, runtime]);

  useEffect(() => {
    void loadHooks();
    return () => {
      hooksGeneration.current += 1;
    };
  }, [loadHooks]);

  useEffect(
    () => () => {
      skillGeneration.current += 1;
    },
    [projectId],
  );

  const selectSkill = useCallback(
    async (skillId: string) => {
      if (!projectId) return;
      const requestGeneration = ++skillGeneration.current;
      setState((current) => ({
        ...current,
        selectedSkill: null,
        loading: true,
        error: null,
      }));
      try {
        const result = await runtime.request("skill/read", {
          projectId,
          skillId,
        });
        if (skillGeneration.current !== requestGeneration) return;
        setState((current) => ({
          ...current,
          selectedSkill: result.skill,
          loading: false,
        }));
      } catch (error) {
        if (skillGeneration.current !== requestGeneration) return;
        setState((current) => ({
          ...current,
          loading: false,
          error: message(error),
        }));
      }
    },
    [projectId, runtime],
  );

  const mutate = useCallback(
    async (operation: (generation: number) => Promise<void>) => {
      const requestGeneration = ++skillGeneration.current;
      setState((current) => ({ ...current, loading: true, error: null }));
      try {
        await operation(requestGeneration);
        if (skillGeneration.current !== requestGeneration) return;
        setState((current) => ({ ...current, loading: false }));
      } catch (error) {
        if (skillGeneration.current !== requestGeneration) return;
        setState((current) => ({
          ...current,
          loading: false,
          error: message(error),
        }));
      }
    },
    [],
  );

  const importSkill = useCallback(
    (path: string, scope: ConfigScope) =>
      mutate(async (generation) => {
        if (!projectId) return;
        const result = await runtime.request("skills/import", {
          projectId,
          path,
          scope,
        });
        if (skillGeneration.current !== generation) return;
        await refreshSkills();
        if (skillGeneration.current !== generation) return;
        setState((current) => ({
          ...current,
          selectedSkill: result.skill,
        }));
      }),
    [mutate, projectId, refreshSkills, runtime],
  );

  const setEnabled = useCallback(
    (skillId: string, enabled: boolean, scope: ConfigScope) =>
      mutate(async (generation) => {
        if (!projectId) return;
        const catalog = await runtime.request("skills/set-enabled", {
          projectId,
          skillId,
          enabled,
          scope,
        });
        if (skillGeneration.current !== generation) return;
        replaceSkills(catalog);
        setState((current) => ({
          ...current,
          selectedSkill:
            current.selectedSkill?.id === skillId
              ? null
              : current.selectedSkill,
        }));
      }),
    [mutate, projectId, replaceSkills, runtime],
  );

  const deleteSkill = useCallback(
    (skillId: string) =>
      mutate(async (generation) => {
        if (!projectId) return;
        await runtime.request("skills/delete", { projectId, skillId });
        if (skillGeneration.current !== generation) return;
        await refreshSkills();
        if (skillGeneration.current !== generation) return;
        setState((current) => ({
          ...current,
          selectedSkill: null,
        }));
      }),
    [mutate, projectId, refreshSkills, runtime],
  );

  const visible =
    state.projectId === projectId
      ? state
      : { ...emptyState, loading: projectId !== null };
  return {
    ...visible,
    skills: skillCatalog.skills,
    catalogRevision: skillCatalog.catalogRevision,
    warnings: [...skillCatalog.warnings, ...visible.hooksWarnings],
    loading: skillCatalog.loading || visible.loading,
    error: skillCatalog.error ?? visible.error,
    refresh: async () => {
      await Promise.all([refreshSkills(), loadHooks()]);
      setState((current) => ({ ...current, selectedSkill: null }));
    },
    selectSkill,
    importSkill,
    setEnabled,
    deleteSkill,
  };
}
