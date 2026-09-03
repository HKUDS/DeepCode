import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  McpInventory,
  McpPresetInventory,
  McpProbeResult,
  Project,
} from "../../generated/app-server";
import type { DesktopRuntime, RpcMethod } from "../../rpc/contracts";
import { useMcpCatalog } from "./useMcpCatalog";

function deferred<Value>() {
  let resolve!: (value: Value) => void;
  const promise = new Promise<Value>((accept) => {
    resolve = accept;
  });
  return { promise, resolve };
}

function project(id: string): Project {
  return {
    id,
    canonicalPath: `/workspace/${id}`,
    displayName: id,
    trustState: "trusted",
    settings: {},
    createdAt: "2026-09-03T00:00:00Z",
    updatedAt: "2026-09-03T00:00:00Z",
    lastOpenedAt: "2026-09-03T00:00:00Z",
  };
}

function inventory(projectId: string): McpInventory {
  return {
    servers: [],
    userConfigPath: "/home/user/.deepcode/deepcode_config.json",
    projectConfigPath: `/workspace/${projectId}/deepcode_config.json`,
  };
}

function presets(projectId: string): McpPresetInventory {
  return {
    source: projectId,
    sourceRevision: "test-revision",
    presets: [],
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("useMcpCatalog project ownership", () => {
  it("ignores a late refresh from a mutation started in the previous project", async () => {
    const probeResult = deferred<McpProbeResult>();
    const projectBInventory = deferred<McpInventory>();
    const projectBPresets = deferred<McpPresetInventory>();
    let projectAListCalls = 0;

    const request = vi.fn((method: RpcMethod, params: unknown) => {
      const projectId = (params as { projectId?: string }).projectId;
      if (method === "mcp/probe") return probeResult.promise;
      if (projectId === "B" && method === "mcp/list") {
        return projectBInventory.promise;
      }
      if (projectId === "B" && method === "mcp/presets") {
        return projectBPresets.promise;
      }
      if (projectId === "A" && method === "mcp/list") {
        projectAListCalls += 1;
      }
      if (method === "mcp/list") return Promise.resolve(inventory(projectId ?? "user"));
      if (method === "mcp/presets") return Promise.resolve(presets(projectId ?? "user"));
      throw new Error(`Unexpected request: ${method}`);
    });
    const runtime = {
      request,
      onNotification: async () => () => undefined,
    } as unknown as DesktopRuntime;

    const { result, rerender } = renderHook(
      ({ selectedProject }) => useMcpCatalog(runtime, selectedProject),
      { initialProps: { selectedProject: project("A") } },
    );

    await waitFor(() => {
      expect(result.current.inventory?.projectConfigPath).toBe(
        "/workspace/A/deepcode_config.json",
      );
    });

    let pendingProbe!: Promise<McpProbeResult>;
    act(() => {
      pendingProbe = result.current.probe("server");
    });
    rerender({ selectedProject: project("B") });

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith("mcp/list", { projectId: "B" });
      expect(request).toHaveBeenCalledWith("mcp/presets", { projectId: "B" });
    });

    await act(async () => {
      probeResult.resolve({
        serverId: "server",
        name: "server",
        ok: true,
        transport: "stdio",
        toolCount: 0,
        resourceCount: 0,
        promptCount: 0,
        elapsedSeconds: 0,
        error: null,
      });
      await pendingProbe;
    });

    await act(async () => {
      projectBInventory.resolve(inventory("B"));
      projectBPresets.resolve(presets("B"));
      await Promise.resolve();
    });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
      expect(result.current.inventory?.projectConfigPath).toBe(
        "/workspace/B/deepcode_config.json",
      );
    });
    expect(projectAListCalls).toBe(1);
  });
});
