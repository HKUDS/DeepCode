import { act, render, screen, waitFor } from "@testing-library/react";
import { expect, test } from "vitest";

import type {
  MethodParams,
  MethodResults,
  SkillCatalogResult,
  SkillInfo,
} from "../../generated/app-server";
import type {
  AnyRpcNotification,
  DesktopRuntime,
  RpcMethod,
} from "../../rpc/contracts";
import { useSkillCatalog } from "./useSkillCatalog";

const projectId = "proj_skill_catalog_test";

function skill(description: string): SkillInfo {
  return {
    id: "sk_0123456789abcdef01234567",
    name: "review",
    description,
    allowedTools: [],
    scope: "project",
    sourceRoot: "agents",
    source: "project:agents",
    location: "project/.agents/skills/review",
    originKind: "local",
    originLabel: "project:agents",
    providerKind: "local",
    providerId: "local",
    packageId: "sk_0123456789abcdef01234567",
    status: "active",
    enabled: true,
    selectable: true,
    revision: `sha256:${"a".repeat(64)}`,
    byteSize: 100,
    shadowedBy: null,
    error: null,
    displayName: null,
    shortDescription: null,
    iconSmall: null,
    iconLarge: null,
    brandColor: null,
    defaultPrompt: null,
    allowImplicitInvocation: true,
    configurableScopes: ["project", "user"],
    deletable: true,
  };
}

function catalog(description: string, revision: string): SkillCatalogResult {
  return {
    skills: [skill(description)],
    warnings: [],
    catalogRevision: `sha256:${revision.repeat(64)}`,
    authoringSkillId: null,
  };
}

class CatalogRuntime {
  current = catalog("First description", "b");
  readonly requests: Array<{
    method: RpcMethod;
    params: MethodParams[RpcMethod];
  }> = [];
  readonly listeners = new Set<(notification: AnyRpcNotification) => void>();

  async request<M extends RpcMethod>(
    method: M,
    params: MethodParams[M],
  ): Promise<MethodResults[M]> {
    this.requests.push({ method, params });
    if (method !== "skills/list") throw new Error(`Unexpected method: ${method}`);
    return this.current as MethodResults[M];
  }

  async onNotification(listener: (notification: AnyRpcNotification) => void) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  emitChanged() {
    for (const listener of this.listeners) {
      listener({
        jsonrpc: "2.0",
        method: "skills.changed",
        params: { projectId },
      });
    }
  }
}

function Consumer({
  runtime,
  label,
}: {
  runtime: DesktopRuntime;
  label: string;
}) {
  const state = useSkillCatalog(runtime, projectId);
  return <p>{`${label}:${state.skills[0]?.description ?? "loading"}`}</p>;
}

test("shares one catalog request across Desktop consumers", async () => {
  const backend = new CatalogRuntime();
  const runtime = backend as unknown as DesktopRuntime;
  render(
    <>
      <Consumer runtime={runtime} label="composer" />
      <Consumer runtime={runtime} label="management" />
    </>,
  );

  expect(await screen.findByText("composer:First description")).toBeTruthy();
  expect(screen.getByText("management:First description")).toBeTruthy();
  expect(backend.requests).toHaveLength(1);
});

test("refreshes every consumer after skills.changed", async () => {
  const backend = new CatalogRuntime();
  const runtime = backend as unknown as DesktopRuntime;
  const view = render(
    <>
      <Consumer runtime={runtime} label="composer" />
      <Consumer runtime={runtime} label="management" />
    </>,
  );
  await screen.findByText("composer:First description");

  backend.current = catalog("Second description", "c");
  act(() => backend.emitChanged());

  expect(await screen.findByText("composer:Second description")).toBeTruthy();
  expect(screen.getByText("management:Second description")).toBeTruthy();
  expect(backend.requests).toHaveLength(2);
  expect(backend.requests[1]).toEqual({
    method: "skills/list",
    params: { projectId, refresh: true },
  });

  view.unmount();
  await waitFor(() => expect(backend.listeners.size).toBe(0));
});
