import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  Automation,
  AutomationRun,
  Event,
  MethodParams,
  MethodResults,
  Project,
  Thread,
} from "../../generated/app-server";
import type {
  AnyRpcNotification,
  DesktopRuntime,
  DesktopUpdateInfo,
  DesktopUpdateProgress,
  RpcMethod,
  SidecarStatus,
} from "../../rpc/contracts";
import { AutomationsPage } from "./AutomationsPage";

const timestamp = "2026-07-29T02:00:00Z";

const project: Project = {
  id: "project-automation-test",
  canonicalPath: "/workspace/automation-test",
  displayName: "Automation test",
  trustState: "trusted",
  settings: {},
  createdAt: timestamp,
  updatedAt: timestamp,
  lastOpenedAt: timestamp,
};

function goalThread(id = "thread-automation"): Thread {
  return {
    id,
    projectId: project.id,
    parentThreadId: null,
    title: "Repository caretaker",
    mode: "goal",
    status: "idle",
    model: null,
    connectionId: null,
    reasoningEffort: null,
    contextWindow: null,
    accessPresetOverride: null,
    workspacePath: project.canonicalPath,
    worktreePath: null,
    createdAt: timestamp,
    updatedAt: timestamp,
    archivedAt: null,
  };
}

function definition(): Automation {
  return {
    id: "auto-automation-test",
    projectId: project.id,
    threadId: goalThread().id,
    name: "Repository caretaker",
    currentRevisionId: "arev-automation-test",
    prompt: "Review the repository and verify its tests.",
    status: "enabled",
    scheduleKind: "interval",
    intervalSeconds: 3600,
    nextRunAt: "2026-07-29T03:00:00Z",
    lastRunAt: "2026-07-29T01:00:00Z",
    createdAt: timestamp,
    updatedAt: timestamp,
  };
}

function completedRun(automation: Automation): AutomationRun {
  return {
    id: "arun-automation-test",
    automationId: automation.id,
    revisionId: automation.currentRevisionId,
    occurrenceId: "aocc-automation-test",
    threadId: automation.threadId,
    goalId: "goal-automation-test",
    turnId: "turn-automation-test",
    trigger: "scheduled",
    status: "completed",
    scheduledFor: "2026-07-29T01:00:00Z",
    detail: "initial result",
    createdAt: timestamp,
    updatedAt: timestamp,
    startedAt: timestamp,
    completedAt: timestamp,
  };
}

class AutomationRuntime implements DesktopRuntime {
  readonly requests: Array<{ method: RpcMethod; params: unknown }> = [];
  automations = [definition()];
  runs = [completedRun(this.automations[0])];
  automationPages = new Map<
    number,
    MethodResults["automation/list"]
  >();
  runPages = new Map<number, MethodResults["automation/runs"]>();
  automationListFailures = 0;
  notificationListener: ((notification: AnyRpcNotification) => void) | null =
    null;

  async request<M extends RpcMethod>(
    method: M,
    params: MethodParams[M],
  ): Promise<MethodResults[M]> {
    this.requests.push({ method, params });
    switch (method) {
      case "automation/list":
        {
          if (this.automationListFailures > 0) {
            this.automationListFailures -= 1;
            throw new Error("automation list unavailable");
          }
          const request = params as MethodParams["automation/list"];
          const page = this.automationPages.get(request.offset ?? 0);
          if (page) return page as MethodResults[M];
        }
        return {
          automations: this.automations,
          latestRuns: this.runs.slice(0, 1),
          schedulerActive: true,
          executionMode: "requires_live_runtime",
          hasMore: false,
          nextOffset: null,
        } as MethodResults[M];
      case "automation/create": {
        const request = params as MethodParams["automation/create"];
        const thread = goalThread("thread-created-automation");
        const automation: Automation = {
          ...definition(),
          id: "auto-created-automation",
          threadId: thread.id,
          name: request.name,
          prompt: request.prompt,
          scheduleKind: request.scheduleKind,
          intervalSeconds: request.intervalSeconds ?? null,
          status: request.enabled === false ? "paused" : "enabled",
        };
        this.automations = [...this.automations, automation];
        return { automation, thread } as MethodResults[M];
      }
      case "automation/update": {
        const request = params as MethodParams["automation/update"];
        const current = this.automations.find(
          (candidate) => candidate.id === request.automationId,
        );
        if (!current) throw new Error("automation not found");
        const automation: Automation = {
          ...current,
          ...(request.name !== undefined ? { name: request.name } : {}),
          ...(request.prompt !== undefined ? { prompt: request.prompt } : {}),
          ...(request.status !== undefined ? { status: request.status } : {}),
          ...(request.scheduleKind !== undefined
            ? { scheduleKind: request.scheduleKind }
            : {}),
          ...(request.intervalSeconds !== undefined
            ? { intervalSeconds: request.intervalSeconds }
            : {}),
        };
        this.automations = this.automations.map((candidate) =>
          candidate.id === automation.id ? automation : candidate,
        );
        return { automation } as MethodResults[M];
      }
      case "automation/remove": {
        const { automationId } =
          params as MethodParams["automation/remove"];
        this.automations = this.automations.filter(
          (candidate) => candidate.id !== automationId,
        );
        return { removed: true } as MethodResults[M];
      }
      case "automation/runs": {
        const request = params as MethodParams["automation/runs"];
        const page = this.runPages.get(request.offset ?? 0);
        return (page ?? {
          runs: this.runs,
          hasMore: false,
          nextOffset: null,
        }) as MethodResults[M];
      }
      default:
        throw new Error(`Unexpected request: ${method}`);
    }
  }

  emitAutomationUpdate(): void {
    const event: Event = {
      eventId: "event-automation-update",
      sequence: 1,
      type: "automation.updated",
      threadId: this.automations[0]?.threadId ?? "thread-automation",
      turnId: null,
      itemId: null,
      timestamp,
      payload: {},
    };
    this.notificationListener?.({
      jsonrpc: "2.0",
      method: "automation.updated",
      params: event,
    });
  }

  emitServerWarning(): void {
    this.notificationListener?.({
      jsonrpc: "2.0",
      method: "server.warning",
      params: {
        code: "EVENT_QUEUE_OVERFLOW",
        dropped: 3,
        replayRequired: true,
      },
    });
  }

  count(method: RpcMethod): number {
    return this.requests.filter((request) => request.method === method).length;
  }

  async status(): Promise<SidecarStatus> {
    return {
      phase: "ready",
      message: null,
      launchSource: "test",
      serverInfo: null,
    };
  }

  async restart(): Promise<SidecarStatus> {
    return this.status();
  }

  async pickDirectory(): Promise<string | null> {
    return null;
  }

  async pickFile(): Promise<string | null> {
    return null;
  }

  async pickContextFiles(): Promise<string[]> {
    return [];
  }

  async openPath(): Promise<void> {}

  async exportDiagnostics(): Promise<string | null> {
    return null;
  }

  async checkForUpdate(): Promise<DesktopUpdateInfo | null> {
    return null;
  }

  async installUpdate(
    listener: (progress: DesktopUpdateProgress) => void,
  ): Promise<void> {
    void listener;
  }

  async onNotification(
    listener: (notification: AnyRpcNotification) => void,
  ): Promise<() => void> {
    this.notificationListener = listener;
    return () => {
      if (this.notificationListener === listener) {
        this.notificationListener = null;
      }
    };
  }

  async onStatus(
    listener: (status: SidecarStatus) => void,
  ): Promise<() => void> {
    void listener;
    return () => undefined;
  }

  async onLog(listener: (message: string) => void): Promise<() => void> {
    void listener;
    return () => undefined;
  }
}

function renderPage(
  runtime: AutomationRuntime,
  onThreadCreated = vi.fn(),
) {
  return render(
    <AutomationsPage
      runtime={runtime}
      project={project}
      onThreadCreated={onThreadCreated}
      onOpenThread={vi.fn()}
    />,
  );
}

describe("AutomationsPage", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("creates an interval Automation with validated protocol values", async () => {
    const runtime = new AutomationRuntime();
    const onThreadCreated = vi.fn();
    renderPage(runtime, onThreadCreated);

    await screen.findByRole("heading", { name: "Repository caretaker" });
    expect(
      screen.getByText(
        /scheduled work runs while a compatible DeepCode runtime is active/,
      ),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "New automation" }));
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "  Dependency review  " },
    });
    fireEvent.change(screen.getByLabelText("Schedule"), {
      target: { value: "interval" },
    });
    fireEvent.change(screen.getByLabelText("Repeat every"), {
      target: { value: "15" },
    });
    fireEvent.change(screen.getByLabelText("Unit"), {
      target: { value: "minutes" },
    });
    fireEvent.change(screen.getByLabelText("Goal prompt"), {
      target: { value: "  Review dependencies and run tests.  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save automation" }));

    await waitFor(() => expect(runtime.count("automation/create")).toBe(1));
    const create = runtime.requests.find(
      (request) => request.method === "automation/create",
    );
    expect(create?.params).toEqual({
      projectId: project.id,
      name: "Dependency review",
      prompt: "Review dependencies and run tests.",
      scheduleKind: "interval",
      intervalSeconds: 900,
      enabled: true,
    });
    expect(onThreadCreated).toHaveBeenCalledWith(
      expect.objectContaining({ id: "thread-created-automation" }),
    );
  });

  it("rejects invalid and out-of-range intervals before issuing a request", async () => {
    const runtime = new AutomationRuntime();
    renderPage(runtime);

    await screen.findByRole("heading", { name: "Repository caretaker" });
    fireEvent.click(screen.getByRole("button", { name: "New automation" }));
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Invalid interval" },
    });
    fireEvent.change(screen.getByLabelText("Schedule"), {
      target: { value: "interval" },
    });
    fireEvent.change(screen.getByLabelText("Repeat every"), {
      target: { value: "1.5" },
    });
    fireEvent.change(screen.getByLabelText("Goal prompt"), {
      target: { value: "Do not submit this draft." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save automation" }));

    expect(
      await screen.findByText(
        "Interval value must be a positive whole number.",
      ),
    ).toBeTruthy();
    expect(runtime.count("automation/create")).toBe(0);

    fireEvent.change(screen.getByLabelText("Repeat every"), {
      target: { value: "8785" },
    });
    fireEvent.change(screen.getByLabelText("Unit"), {
      target: { value: "hours" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save automation" }));

    expect(
      await screen.findByText("Interval must not exceed 366 days."),
    ).toBeTruthy();
    expect(runtime.count("automation/create")).toBe(0);
  });

  it("round-trips a 90-second definition while editing only its name", async () => {
    const runtime = new AutomationRuntime();
    runtime.automations = [
      {
        ...definition(),
        intervalSeconds: 90,
        status: "paused",
      },
    ];
    runtime.runs = [completedRun(runtime.automations[0])];
    renderPage(runtime);

    await screen.findByRole("heading", { name: "Repository caretaker" });
    expect(screen.getByText("Every 90 seconds")).toBeTruthy();
    expect(screen.getByText("Status: paused")).toBeTruthy();
    expect(screen.getByText("Latest run: completed")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getByLabelText("Repeat every")).toHaveProperty("value", "90");
    expect(screen.getByLabelText("Unit")).toHaveProperty("value", "seconds");
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Updated 90-second caretaker" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save automation" }));

    await waitFor(() => expect(runtime.count("automation/update")).toBe(1));
    const update = runtime.requests.find(
      (request) => request.method === "automation/update",
    );
    expect(update?.params).toMatchObject({
      automationId: "auto-automation-test",
      name: "Updated 90-second caretaker",
      intervalSeconds: 90,
      status: "paused",
    });
  });

  it("edits and removes through the shared Automation methods", async () => {
    const runtime = new AutomationRuntime();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage(runtime);

    await screen.findByRole("heading", { name: "Repository caretaker" });
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Updated caretaker" },
    });
    fireEvent.change(screen.getByLabelText("Goal prompt"), {
      target: { value: "Inspect, repair, and verify." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save automation" }));

    await waitFor(() => expect(runtime.count("automation/update")).toBe(1));
    const update = runtime.requests.find(
      (request) => request.method === "automation/update",
    );
    expect(update?.params).toMatchObject({
      automationId: "auto-automation-test",
      name: "Updated caretaker",
      prompt: "Inspect, repair, and verify.",
    });
    expect(
      await screen.findByRole("heading", { name: "Updated caretaker" }),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(runtime.count("automation/remove")).toBe(1));
    expect(window.confirm).toHaveBeenCalledWith(
      "Remove the automation “Updated caretaker”? Its Goal Thread and Session history will be kept.",
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("heading", { name: "Updated caretaker" }),
      ).toBeNull(),
    );
  });

  it("refreshes the visible Run history on automation.updated", async () => {
    const runtime = new AutomationRuntime();
    renderPage(runtime);

    await screen.findByRole("heading", { name: "Repository caretaker" });
    fireEvent.click(screen.getByRole("button", { name: "Runs" }));
    expect(await screen.findByText(/initial result/)).toBeTruthy();
    await waitFor(() => expect(runtime.notificationListener).not.toBeNull());

    runtime.runs = [
      {
        ...runtime.runs[0],
        status: "failed",
        detail: "refreshed failure",
        completedAt: timestamp,
      },
    ];
    runtime.emitAutomationUpdate();

    await waitFor(() => expect(runtime.count("automation/runs")).toBe(2));
    expect(await screen.findByText(/refreshed failure/)).toBeTruthy();
    expect(runtime.count("automation/list")).toBeGreaterThanOrEqual(2);
  });

  it("recovers the inventory and visible Run history after server.warning", async () => {
    const runtime = new AutomationRuntime();
    renderPage(runtime);

    await screen.findByRole("heading", { name: "Repository caretaker" });
    fireEvent.click(screen.getByRole("button", { name: "Runs" }));
    expect(await screen.findByText(/initial result/)).toBeTruthy();
    await waitFor(() => expect(runtime.notificationListener).not.toBeNull());

    runtime.automations = [
      {
        ...runtime.automations[0],
        name: "Recovered caretaker",
      },
    ];
    runtime.runs = [
      {
        ...runtime.runs[0],
        detail: "recovered after overflow",
      },
    ];
    runtime.emitServerWarning();

    await waitFor(() => expect(runtime.count("automation/runs")).toBe(2));
    expect(
      await screen.findByRole("heading", { name: "Recovered caretaker" }),
    ).toBeTruthy();
    expect(await screen.findByText(/recovered after overflow/)).toBeTruthy();
    expect(runtime.count("automation/list")).toBeGreaterThanOrEqual(2);
  });

  it("loads explicit Automation and Run pages with stable id deduplication", async () => {
    const runtime = new AutomationRuntime();
    const first = definition();
    const second: Automation = {
      ...definition(),
      id: "auto-second-page",
      threadId: "thread-second-page",
      currentRevisionId: "arev-second-page",
      name: "Second page caretaker",
    };
    const firstRun = completedRun(first);
    const secondRun: AutomationRun = {
      ...firstRun,
      id: "arun-second-page",
      occurrenceId: "aocc-second-page",
      detail: "second page result",
    };
    runtime.automationPages.set(0, {
      automations: [first],
      latestRuns: [firstRun],
      schedulerActive: true,
      executionMode: "requires_live_runtime",
      hasMore: true,
      nextOffset: 1,
    });
    runtime.automationPages.set(1, {
      automations: [
        { ...first, name: "Refreshed first caretaker" },
        second,
      ],
      latestRuns: [firstRun, completedRun(second)],
      schedulerActive: true,
      executionMode: "requires_live_runtime",
      hasMore: false,
      nextOffset: null,
    });
    runtime.runPages.set(0, {
      runs: [firstRun],
      hasMore: true,
      nextOffset: 1,
    });
    runtime.runPages.set(1, {
      runs: [
        { ...firstRun, detail: "refreshed first result" },
        secondRun,
      ],
      hasMore: false,
      nextOffset: null,
    });
    renderPage(runtime);

    await screen.findByRole("heading", { name: "Repository caretaker" });
    fireEvent.click(
      screen.getByRole("button", { name: "Load more automations" }),
    );

    expect(
      await screen.findByRole("heading", {
        name: "Refreshed first caretaker",
      }),
    ).toBeTruthy();
    expect(
      screen.getByRole("heading", { name: "Second page caretaker" }),
    ).toBeTruthy();
    expect(
      screen.getAllByRole("heading", {
        name: "Refreshed first caretaker",
      }),
    ).toHaveLength(1);
    expect(
      runtime.requests.some(
        (request) =>
          request.method === "automation/list" &&
          (request.params as MethodParams["automation/list"]).offset === 1,
      ),
    ).toBe(true);

    fireEvent.click(screen.getAllByRole("button", { name: "Runs" })[0]);
    expect(await screen.findByText(/initial result/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Load more runs" }));

    expect(await screen.findByText(/refreshed first result/)).toBeTruthy();
    expect(await screen.findByText(/second page result/)).toBeTruthy();
    expect(screen.queryByText(/initial result/)).toBeNull();
    expect(
      runtime.requests.some(
        (request) =>
          request.method === "automation/runs" &&
          (request.params as MethodParams["automation/runs"]).offset === 1,
      ),
    ).toBe(true);
    expect(
      screen.queryByRole("button", { name: "Load more automations" }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Load more runs" }),
    ).toBeNull();

    runtime.emitServerWarning();

    expect(
      await screen.findByRole("heading", { name: "Repository caretaker" }),
    ).toBeTruthy();
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Load more automations" }),
      ).toBeTruthy(),
    );
    expect(
      screen.getByRole("button", { name: "Load more runs" }),
    ).toBeTruthy();
    expect(
      screen.queryByRole("heading", { name: "Second page caretaker" }),
    ).toBeNull();
  });

  it("recovers from an initial load failure and does not expose stale data after a project switch", async () => {
    const runtime = new AutomationRuntime();
    runtime.automationListFailures = 1;
    const onThreadCreated = vi.fn();
    const onOpenThread = vi.fn();
    const view = render(
      <AutomationsPage
        runtime={runtime}
        project={project}
        onThreadCreated={onThreadCreated}
        onOpenThread={onOpenThread}
      />,
    );

    expect(await screen.findByText("automation list unavailable")).toBeTruthy();
    const refresh = screen.getByRole("button", { name: "Refresh" });
    expect(refresh).toHaveProperty("disabled", false);

    fireEvent.click(refresh);
    expect(
      await screen.findByRole("heading", { name: "Repository caretaker" }),
    ).toBeTruthy();

    const nextProject: Project = {
      ...project,
      id: "project-automation-next",
      canonicalPath: "/workspace/automation-next",
      displayName: "Next automation test",
    };
    const nextDefinition: Automation = {
      ...definition(),
      id: "auto-automation-next",
      projectId: nextProject.id,
      threadId: "thread-automation-next",
      currentRevisionId: "arev-automation-next",
      name: "Next repository caretaker",
    };
    runtime.automations = [nextDefinition];
    runtime.runs = [completedRun(nextDefinition)];
    runtime.automationListFailures = 1;
    const requestsBeforeSwitch = runtime.count("automation/list");

    view.rerender(
      <AutomationsPage
        runtime={runtime}
        project={nextProject}
        onThreadCreated={onThreadCreated}
        onOpenThread={onOpenThread}
      />,
    );

    await waitFor(() =>
      expect(runtime.count("automation/list")).toBe(requestsBeforeSwitch + 1),
    );
    expect(await screen.findByText("automation list unavailable")).toBeTruthy();
    expect(
      screen.queryByRole("heading", { name: "Repository caretaker" }),
    ).toBeNull();
    expect(screen.getByRole("button", { name: "Refresh" })).toHaveProperty(
      "disabled",
      false,
    );

    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(
      await screen.findByRole("heading", {
        name: "Next repository caretaker",
      }),
    ).toBeTruthy();
  });
});
