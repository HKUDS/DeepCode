import { useCallback, useMemo, useSyncExternalStore } from "react";

import type {
  SkillCatalogResult,
  SkillInfo,
} from "../../generated/app-server";
import type {
  AnyRpcNotification,
  DesktopRuntime,
} from "../../rpc/contracts";

interface SkillCatalogState extends SkillCatalogResult {
  projectId: string | null;
  loading: boolean;
  error: string | null;
}

interface CatalogEntry {
  state: SkillCatalogState;
  loaded: boolean;
  generation: number;
  request: Promise<SkillCatalogResult | null> | null;
  listeners: Set<() => void>;
}

const emptyCatalog: SkillCatalogState = {
  projectId: null,
  skills: [],
  warnings: [],
  catalogRevision: "",
  authoringSkillId: null,
  loading: false,
  error: null,
};

const stores = new WeakMap<DesktopRuntime, SkillCatalogStore>();

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function projectCatalog(projectId: string): SkillCatalogState {
  return { ...emptyCatalog, projectId };
}

class SkillCatalogStore {
  private readonly entries = new Map<string, CatalogEntry>();
  private subscriberCount = 0;
  private notificationGeneration = 0;
  private unsubscribeNotifications: (() => void) | null = null;

  constructor(private readonly runtime: DesktopRuntime) {}

  snapshot(projectId: string | null): SkillCatalogState {
    return projectId ? this.entry(projectId).state : emptyCatalog;
  }

  subscribe(projectId: string | null, listener: () => void): () => void {
    if (!projectId) return () => undefined;
    const entry = this.entry(projectId);
    entry.listeners.add(listener);
    this.subscriberCount += 1;
    if (this.subscriberCount === 1) this.startNotifications();
    if (!entry.loaded && entry.request === null) void this.load(projectId);
    return () => {
      if (!entry.listeners.delete(listener)) return;
      this.subscriberCount -= 1;
      if (this.subscriberCount === 0) this.stopNotifications();
    };
  }

  load(
    projectId: string,
    force = false,
  ): Promise<SkillCatalogResult | null> {
    const entry = this.entry(projectId);
    if (!force && entry.request) return entry.request;
    if (!force && entry.loaded && entry.state.error === null) {
      return Promise.resolve(entry.state);
    }
    const generation = ++entry.generation;
    this.update(entry, { ...entry.state, loading: true, error: null });
    const request = this.runtime
      .request("skills/list", {
        projectId,
        ...(force ? { refresh: true } : {}),
      })
      .then((result) => {
        if (entry.generation !== generation) return null;
        entry.loaded = true;
        this.update(entry, {
          projectId,
          ...result,
          loading: false,
          error: null,
        });
        return result;
      })
      .catch((error: unknown) => {
        if (entry.generation !== generation) return null;
        this.update(entry, {
          ...entry.state,
          loading: false,
          error: errorMessage(error),
        });
        return null;
      })
      .finally(() => {
        if (entry.generation === generation) entry.request = null;
      });
    entry.request = request;
    return request;
  }

  replace(projectId: string, catalog: SkillCatalogResult): void {
    const entry = this.entry(projectId);
    entry.generation += 1;
    entry.request = null;
    entry.loaded = true;
    this.update(entry, {
      projectId,
      ...catalog,
      loading: false,
      error: null,
    });
  }

  private entry(projectId: string): CatalogEntry {
    let entry = this.entries.get(projectId);
    if (!entry) {
      entry = {
        state: projectCatalog(projectId),
        loaded: false,
        generation: 0,
        request: null,
        listeners: new Set(),
      };
      this.entries.set(projectId, entry);
    }
    return entry;
  }

  private update(entry: CatalogEntry, state: SkillCatalogState): void {
    entry.state = state;
    for (const listener of entry.listeners) listener();
  }

  private startNotifications(): void {
    const generation = ++this.notificationGeneration;
    void this.runtime
      .onNotification((notification) => this.onNotification(notification))
      .then((unsubscribe) => {
        if (
          generation !== this.notificationGeneration ||
          this.subscriberCount === 0
        ) {
          unsubscribe();
          return;
        }
        this.unsubscribeNotifications = unsubscribe;
      })
      .catch(() => {
        // Catalog requests and manual reload remain available if the runtime
        // cannot establish its optional notification channel.
      });
  }

  private stopNotifications(): void {
    this.notificationGeneration += 1;
    this.unsubscribeNotifications?.();
    this.unsubscribeNotifications = null;
  }

  private onNotification(notification: AnyRpcNotification): void {
    if (notification.method !== "skills.changed") return;
    const entry = this.entries.get(notification.params.projectId);
    if (!entry?.listeners.size) return;
    void this.load(notification.params.projectId, true);
  }
}

function storeFor(runtime: DesktopRuntime): SkillCatalogStore {
  let store = stores.get(runtime);
  if (!store) {
    store = new SkillCatalogStore(runtime);
    stores.set(runtime, store);
  }
  return store;
}

export function useSkillCatalog(
  runtime: DesktopRuntime,
  projectId: string | null,
) {
  const store = useMemo(() => storeFor(runtime), [runtime]);
  const subscribe = useCallback(
    (listener: () => void) => store.subscribe(projectId, listener),
    [projectId, store],
  );
  const snapshot = useCallback(
    () => store.snapshot(projectId),
    [projectId, store],
  );
  const state = useSyncExternalStore(subscribe, snapshot, snapshot);
  const refresh = useCallback(
    () => (projectId ? store.load(projectId, true) : Promise.resolve(null)),
    [projectId, store],
  );
  const replace = useCallback(
    (catalog: SkillCatalogResult) => {
      if (projectId) store.replace(projectId, catalog);
    },
    [projectId, store],
  );
  const activeSkills = useMemo<SkillInfo[]>(
    () => state.skills.filter((skill) => skill.selectable && skill.enabled),
    [state.skills],
  );
  return {
    ...state,
    activeSkills,
    refresh,
    replace,
  };
}
