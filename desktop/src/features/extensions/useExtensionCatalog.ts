import { useCallback, useEffect, useRef, useState } from "react";

import type {
  HookInfo,
  SkillDetail,
  SkillInfo,
} from "../../generated/app-server";
import type { DesktopRuntime } from "../../rpc/contracts";

interface ExtensionCatalogState {
  projectId: string | null;
  skills: SkillInfo[];
  hooks: HookInfo[];
  warnings: string[];
  hooksTruncated: boolean;
  selectedSkill: SkillDetail | null;
  loading: boolean;
  error: string | null;
}

const emptyState: ExtensionCatalogState = {
  projectId: null,
  skills: [],
  hooks: [],
  warnings: [],
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
  const [state, setState] = useState<ExtensionCatalogState>(emptyState);
  const catalogGeneration = useRef(0);
  const detailGeneration = useRef(0);

  const refresh = useCallback(async () => {
    const requestGeneration = ++catalogGeneration.current;
    detailGeneration.current += 1;
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
      const [skills, hooks] = await Promise.all([
        runtime.request("skills/list", { projectId }),
        runtime.request("hooks/list", { projectId }),
      ]);
      if (catalogGeneration.current !== requestGeneration) return;
      setState({
        projectId,
        skills: skills.skills,
        hooks: hooks.hooks,
        warnings: [...skills.warnings, ...hooks.warnings],
        hooksTruncated: hooks.truncated,
        selectedSkill: null,
        loading: false,
        error: null,
      });
    } catch (error) {
      if (catalogGeneration.current !== requestGeneration) return;
      setState((current) => ({
        ...current,
        loading: false,
        error: message(error),
      }));
    }
  }, [projectId, runtime]);

  useEffect(() => {
    const requestGeneration = ++catalogGeneration.current;
    detailGeneration.current += 1;
    if (projectId) {
      void Promise.all([
        runtime.request("skills/list", { projectId }),
        runtime.request("hooks/list", { projectId }),
      ])
        .then(([skills, hooks]) => {
          if (catalogGeneration.current !== requestGeneration) return;
          setState({
            projectId,
            skills: skills.skills,
            hooks: hooks.hooks,
            warnings: [...skills.warnings, ...hooks.warnings],
            hooksTruncated: hooks.truncated,
            selectedSkill: null,
            loading: false,
            error: null,
          });
        })
        .catch((error: unknown) => {
          if (catalogGeneration.current !== requestGeneration) return;
          setState({
            ...emptyState,
            projectId,
            error: message(error),
          });
        });
    }
    return () => {
      catalogGeneration.current += 1;
      detailGeneration.current += 1;
    };
  }, [projectId, runtime]);

  const selectSkill = useCallback(
    async (name: string) => {
      if (!projectId) return;
      const requestGeneration = ++detailGeneration.current;
      setState((current) => ({ ...current, loading: true, error: null }));
      try {
        const result = await runtime.request("skill/read", { projectId, name });
        if (detailGeneration.current !== requestGeneration) return;
        setState((current) => ({
          ...current,
          selectedSkill: result.skill,
          loading: false,
        }));
      } catch (error) {
        if (detailGeneration.current !== requestGeneration) return;
        setState((current) => ({
          ...current,
          loading: false,
          error: message(error),
        }));
      }
    },
    [projectId, runtime],
  );

  const visible =
    state.projectId === projectId
      ? state
      : {
          ...emptyState,
          loading: projectId !== null,
        };
  return { ...visible, refresh, selectSkill };
}
