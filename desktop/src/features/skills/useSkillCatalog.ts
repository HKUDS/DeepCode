import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  SkillCatalogResult,
} from "../../generated/app-server";
import type { DesktopRuntime } from "../../rpc/contracts";

interface SkillCatalogState extends SkillCatalogResult {
  projectId: string | null;
  loading: boolean;
  error: string | null;
}

const emptyCatalog: SkillCatalogState = {
  projectId: null,
  skills: [],
  warnings: [],
  catalogRevision: "",
  loading: false,
  error: null,
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function useSkillCatalog(
  runtime: DesktopRuntime,
  projectId: string | null,
) {
  const [state, setState] = useState<SkillCatalogState>(emptyCatalog);
  const generation = useRef(0);

  const load = useCallback(
    async (force = false) => {
      const requestGeneration = ++generation.current;
      if (!projectId) {
        setState(emptyCatalog);
        return null;
      }
      setState((current) =>
        current.projectId === projectId
          ? { ...current, loading: true, error: null }
          : { ...emptyCatalog, projectId, loading: true },
      );
      try {
        const result = await runtime.request("skills/list", {
          projectId,
          ...(force ? { refresh: true } : {}),
        });
        if (generation.current !== requestGeneration) return null;
        setState({
          projectId,
          ...result,
          loading: false,
          error: null,
        });
        return result;
      } catch (error) {
        if (generation.current !== requestGeneration) return null;
        setState((current) => ({
          ...current,
          loading: false,
          error: errorMessage(error),
        }));
        return null;
      }
    },
    [projectId, runtime],
  );

  useEffect(() => {
    void load();
    return () => {
      generation.current += 1;
    };
  }, [load]);

  const replace = useCallback(
    (catalog: SkillCatalogResult) => {
      if (!projectId) return;
      generation.current += 1;
      setState({
        projectId,
        ...catalog,
        loading: false,
        error: null,
      });
    },
    [projectId],
  );
  const refresh = useCallback(() => load(true), [load]);

  const visible =
    state.projectId === projectId
      ? state
      : { ...emptyCatalog, loading: projectId !== null };
  const activeSkills = useMemo(
    () => visible.skills.filter((skill) => skill.selectable && skill.enabled),
    [visible.skills],
  );
  return {
    ...visible,
    activeSkills,
    refresh,
    replace,
  };
}
