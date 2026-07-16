import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type {
  Approval,
  Automation,
  AutomationRun,
  DiagnosticsSnapshot,
  Event,
  Item,
  JsonValue,
  MethodParams,
  MethodResults,
  Project,
  SettingsSnapshot,
  Thread,
  Turn,
  WorkflowRun,
} from "./generated/app-server";
import { App } from "./App";
import type {
  AnyRpcNotification,
  DesktopRuntime,
  DesktopUpdateInfo,
  DesktopUpdateProgress,
  RpcMethod,
  SidecarStatus,
} from "./rpc/contracts";

const readyStatus: SidecarStatus = {
  phase: "ready",
  message: null,
  launchSource: "test",
  serverInfo: {
    protocolVersion: "1.0",
    serverInfo: { name: "deepcode-app-server", version: "test" },
    clientInfo: { name: "desktop-test", version: "test" },
    capabilities: {
      methods: [],
      eventReplay: true,
      liveEvents: true,
      maxMessageBytes: 1024 * 1024,
    },
  },
};

const desktopSettings: SettingsSnapshot = {
  configPath: "/tmp/deepcode_config.json",
  agents: {
    defaults: {
      model: "gpt-5",
    },
  },
  security: {
    permissionMode: "full_auto",
    permissions: {},
    sandbox: true,
  },
  permissionModeExplicit: false,
  providers: [
    {
      name: "openai",
      label: "OpenAI",
      configured: true,
      credentialSource: "environment",
      apiBase: null,
      local: false,
    },
  ],
  models: [
    {
      id: "gpt-5",
      contextWindow: 400000,
      maxOutputTokens: 128000,
      source: "catalog",
    },
    {
      id: "gpt-5-mini",
      contextWindow: 400000,
      maxOutputTokens: 128000,
      source: "catalog",
    },
  ],
};

const diagnostics: DiagnosticsSnapshot = {
  appVersion: "1.2.0",
  pythonVersion: "3.12.9",
  pythonExecutable: "/usr/bin/python3",
  platform: "macOS-15",
  architecture: "arm64",
  processId: 1234,
  databasePath: "/tmp/deepcode.sqlite3",
  databaseSchemaVersion: 5,
  databaseBytes: 4096,
  sessionStorePath: "/tmp/sessions",
  sessionCount: 4,
  projectCount: 1,
  threadCount: 2,
  workflowCount: 0,
  automationCount: 1,
  userConfigPath: "/tmp/deepcode_config.json",
  projectConfigPath: "/workspace/deepcode/deepcode_config.json",
  projectPath: "/workspace/deepcode",
  projectTrust: "trusted",
  configError: null,
  checks: [
    {
      id: "database",
      label: "Desktop database",
      status: "ok",
      detail: "SQLite integrity check passed",
    },
  ],
};

class TestRuntime implements DesktopRuntime {
  readonly calls: string[] = [];
  readonly requests: Array<{ method: string; params: unknown }> = [];
  readonly diagnosticsExports: DiagnosticsSnapshot[] = [];
  updateInstallCount = 0;
  private readonly threadState: Thread[];
  private settingsState: SettingsSnapshot = {
    ...desktopSettings,
    agents: { ...desktopSettings.agents },
    security: { ...desktopSettings.security },
    providers: desktopSettings.providers.map((provider) => ({ ...provider })),
    models: desktopSettings.models.map((model) => ({ ...model })),
  };
  private automationStatus: Automation["status"] = "enabled";

  constructor(
    private readonly projects: Project[] = [],
    threads: Thread[] = [],
    private readonly events: Event[] = [],
    private readonly contextFiles: string[] = [],
    private readonly availableUpdate: DesktopUpdateInfo | null = null,
  ) {
    this.threadState = threads.map((candidate) => ({ ...candidate }));
  }

  async request<M extends RpcMethod>(
    method: M,
    params: MethodParams[M],
  ): Promise<MethodResults[M]> {
    void params;
    this.calls.push(method);
    this.requests.push({ method, params });
    switch (method) {
      case "project/list":
        return { projects: this.projects } as MethodResults[M];
      case "settings/read":
        return { settings: this.settingsState } as MethodResults[M];
      case "settings/update": {
        const request = params as MethodParams["settings/update"];
        const security = request.patch.security;
        this.settingsState = {
          ...this.settingsState,
          security:
            typeof security === "object" &&
            security !== null &&
            !Array.isArray(security)
              ? { ...this.settingsState.security, ...security }
              : this.settingsState.security,
          permissionModeExplicit: true,
        };
        return { settings: this.settingsState } as MethodResults[M];
      }
      case "skills/list":
        return {
          skills: [
            {
              name: "review",
              description: "Review a change carefully",
              allowedTools: ["read", "grep"],
              directory: "/workspace/deepcode/.deepcode/skills/review",
              source: "project:.deepcode",
            },
          ],
          warnings: [],
        } as unknown as MethodResults[M];
      case "skill/read":
        return {
          skill: {
            name: "review",
            description: "Review a change carefully",
            allowedTools: ["read", "grep"],
            directory: "/workspace/deepcode/.deepcode/skills/review",
            source: "project:.deepcode",
            instructions: "Inspect the change and report **concrete evidence**.",
            truncated: false,
          },
        } as unknown as MethodResults[M];
      case "hooks/list":
        return {
          hooks: [
            {
              eventName: "PreToolUse",
              matcher: "Bash",
              command: "python3 check.py",
              timeoutSeconds: 15,
              source: "project",
              sourcePath: "/workspace/deepcode/.deepcode/hooks.json",
              displayOrder: 0,
              statusMessage: null,
            },
          ],
          warnings: [],
          truncated: false,
        } as unknown as MethodResults[M];
      case "mcp/list":
        return {
          servers: [
            {
              name: "filesystem",
              transport: "stdio",
              command: "npx",
              args: ["-y", "@modelcontextprotocol/server-filesystem"],
              url: null,
              enabledTools: ["*"],
              toolTimeout: 300,
              description: "Workspace filesystem tools",
              envKeys: [],
              headerKeys: [],
              source: "user",
              configurationState: "configured",
              configurationMessage:
                "Configuration is ready; connection is checked on use",
            },
          ],
          userConfigPath: "/tmp/deepcode_config.json",
          projectConfigPath: "/workspace/deepcode/deepcode_config.json",
        } as unknown as MethodResults[M];
      case "diagnostics/read":
        return { diagnostics } as MethodResults[M];
      case "automation/list":
        return {
          automations: [
            { ...automation, status: this.automationStatus },
          ],
          latestRuns: [automationRun],
          schedulerActive: true,
          executionMode: "while_app_running",
        } as unknown as MethodResults[M];
      case "automation/create":
        return {
          automation,
          thread: goalThread,
        } as unknown as MethodResults[M];
      case "automation/update": {
        const request = params as MethodParams["automation/update"];
        if (request.status) this.automationStatus = request.status;
        return {
          automation: {
            ...automation,
            status: this.automationStatus,
          },
        } as unknown as MethodResults[M];
      }
      case "automation/remove":
        return { removed: true } as MethodResults[M];
      case "automation/run":
        return {
          run: { ...automationRun, status: "queued", completedAt: null },
          turn: {
            ...turn,
            id: "turn-automation",
            threadId: goalThread.id,
            status: "queued",
            prompt: automation.prompt,
            completedAt: null,
            stopReason: null,
          },
        } as unknown as MethodResults[M];
      case "automation/runs":
        return { runs: [automationRun] } as unknown as MethodResults[M];
      case "thread/list":
        return {
          threads: this.threadState.filter((candidate) => candidate.status !== "archived"),
        } as MethodResults[M];
      case "thread/resume": {
        const sessionId = (params as MethodParams["thread/resume"]).sessionId;
        const resumed = this.threadState.find((candidate) => candidate.id === sessionId);
        if (!resumed) throw new Error(`Missing test thread: ${sessionId}`);
        return { thread: resumed } as MethodResults[M];
      }
      case "thread/rename": {
        const request = params as MethodParams["thread/rename"];
        const index = this.threadState.findIndex(
          (candidate) => candidate.id === request.threadId,
        );
        if (index === -1) throw new Error(`Missing test thread: ${request.threadId}`);
        this.threadState[index] = {
          ...this.threadState[index],
          title: request.title,
        };
        return { thread: this.threadState[index] } as MethodResults[M];
      }
      case "thread/model": {
        const request = params as MethodParams["thread/model"];
        const index = this.threadState.findIndex(
          (candidate) => candidate.id === request.threadId,
        );
        if (index === -1) throw new Error(`Missing test thread: ${request.threadId}`);
        this.threadState[index] = {
          ...this.threadState[index],
          model: request.model,
        };
        return { thread: this.threadState[index] } as MethodResults[M];
      }
      case "thread/archive": {
        const request = params as MethodParams["thread/archive"];
        const index = this.threadState.findIndex(
          (candidate) => candidate.id === request.threadId,
        );
        if (index === -1) throw new Error(`Missing test thread: ${request.threadId}`);
        this.threadState[index] = {
          ...this.threadState[index],
          status: "archived",
          archivedAt: "2026-07-16T02:00:00Z",
        };
        return { thread: this.threadState[index] } as MethodResults[M];
      }
      case "turn/start": {
        const request = params as MethodParams["turn/start"];
        const startedTurn: Turn = {
          id: "turn-retry",
          threadId: request.threadId,
          ordinal: 2,
          prompt: request.prompt,
          status: "queued",
          stopReason: null,
          errorCode: null,
          errorMessage: null,
          startedAt: null,
          completedAt: null,
        };
        const userItem: Item = {
          id: "item-retry-user",
          threadId: request.threadId,
          turnId: startedTurn.id,
          ordinal: 1,
          kind: "user_message",
          status: "completed",
          summary: request.prompt,
          payload: { text: request.prompt },
          createdAt: "2026-07-16T02:00:00Z",
          updatedAt: "2026-07-16T02:00:00Z",
        };
        return {
          turn: startedTurn,
          items: [userItem],
          approvals: [],
        } as unknown as MethodResults[M];
      }
      case "turn/enqueue": {
        const request = params as MethodParams["turn/enqueue"];
        const queuedTurn: Turn = {
          id: "turn-queued",
          threadId: request.threadId,
          ordinal: 2,
          prompt: request.prompt,
          status: "queued",
          stopReason: null,
          errorCode: null,
          errorMessage: null,
          startedAt: null,
          completedAt: null,
        };
        const userItem: Item = {
          id: "item-queued-user",
          threadId: request.threadId,
          turnId: queuedTurn.id,
          ordinal: 1,
          kind: "user_message",
          status: "completed",
          summary: request.prompt,
          payload: { text: request.prompt },
          createdAt: "2026-07-16T02:00:00Z",
          updatedAt: "2026-07-16T02:00:00Z",
        };
        return {
          turn: queuedTurn,
          items: [userItem],
          approvals: [],
        } as unknown as MethodResults[M];
      }
      case "turn/interrupt": {
        const request = params as MethodParams["turn/interrupt"];
        const interrupted: Turn = {
          id: request.turnId,
          threadId: thread.id,
          ordinal: request.turnId === "turn-queued" ? 2 : 1,
          prompt: request.turnId === "turn-queued" ? "queued" : "active",
          status: "interrupted",
          stopReason: "interrupted",
          errorCode: null,
          errorMessage: null,
          startedAt: null,
          completedAt: "2026-07-16T02:00:01Z",
        };
        return {
          accepted: true,
          turn: interrupted,
        } as unknown as MethodResults[M];
      }
      case "approval/respond": {
        const request = params as MethodParams["approval/respond"];
        return {
          approval: {
            ...pendingApproval,
            status: request.decision,
            decision: { status: request.decision },
            resolvedAt: "2026-07-16T02:00:00Z",
          },
        } as unknown as MethodResults[M];
      }
      case "event/replay":
        return { events: this.events } as MethodResults[M];
      case "file/list":
        return { entries: [], truncated: false } as unknown as MethodResults[M];
      case "git/status":
        return {
          status: {
            repositoryRoot: "/workspace/deepcode",
            branch: null,
            upstream: null,
            ahead: 0,
            behind: 0,
            detached: false,
            entries: [],
          },
        } as unknown as MethodResults[M];
      case "git/diff":
        return { files: [] } as unknown as MethodResults[M];
      case "test/discover":
        return { commands: [] } as unknown as MethodResults[M];
      default:
        throw new Error(`Unexpected test RPC method: ${method}`);
    }
  }

  async status() {
    return readyStatus;
  }

  async restart() {
    return readyStatus;
  }

  async pickDirectory() {
    return null;
  }

  async pickFile() {
    return null;
  }

  async pickContextFiles() {
    return [...this.contextFiles];
  }

  async exportDiagnostics(snapshot: DiagnosticsSnapshot) {
    this.diagnosticsExports.push(snapshot);
    return "/tmp/deepcode-diagnostics-test.json";
  }

  async checkForUpdate() {
    return this.availableUpdate;
  }

  async installUpdate(
    listener: (progress: DesktopUpdateProgress) => void,
  ) {
    this.updateInstallCount += 1;
    listener({
      phase: "finished",
      downloadedBytes: 100,
      totalBytes: 100,
    });
  }

  async onNotification(listener: (notification: AnyRpcNotification) => void) {
    void listener;
    return () => undefined;
  }

  async onStatus(listener: (status: SidecarStatus) => void) {
    void listener;
    return () => undefined;
  }

  async onLog(listener: (message: string) => void) {
    void listener;
    return () => undefined;
  }
}

const project: Project = {
  id: "project-1",
  canonicalPath: "/workspace/deepcode",
  displayName: "DeepCode",
  trustState: "trusted",
  settings: {},
  createdAt: "2026-07-16T00:00:00Z",
  updatedAt: "2026-07-16T00:00:00Z",
  lastOpenedAt: "2026-07-16T00:00:00Z",
};

const thread: Thread = {
  id: "thread-1",
  projectId: project.id,
  parentThreadId: null,
  title: "Recovered task",
  mode: "code",
  status: "idle",
  model: null,
  workspacePath: project.canonicalPath,
  worktreePath: null,
  createdAt: "2026-07-16T00:00:00Z",
  updatedAt: "2026-07-16T00:00:00Z",
  archivedAt: null,
};

const goalThread: Thread = {
  ...thread,
  id: "thread-goal",
  title: "Repository caretaker",
  mode: "goal",
};

const automation: Automation = {
  id: "auto-test",
  projectId: project.id,
  threadId: goalThread.id,
  name: "Repository caretaker",
  prompt: "Review and maintain the repository",
  status: "enabled",
  scheduleKind: "interval",
  intervalSeconds: 3600,
  nextRunAt: "2026-07-16T03:00:00Z",
  lastRunAt: "2026-07-16T02:00:00Z",
  createdAt: "2026-07-16T00:00:00Z",
  updatedAt: "2026-07-16T02:00:00Z",
};

const automationRun: AutomationRun = {
  id: "arun-test",
  automationId: automation.id,
  threadId: goalThread.id,
  turnId: "turn-automation",
  trigger: "scheduled",
  status: "completed",
  scheduledFor: "2026-07-16T02:00:00Z",
  detail: "completed",
  createdAt: "2026-07-16T02:00:00Z",
  updatedAt: "2026-07-16T02:00:05Z",
  startedAt: "2026-07-16T02:00:01Z",
  completedAt: "2026-07-16T02:00:05Z",
};

const turn: Turn = {
  id: "turn-1",
  threadId: thread.id,
  ordinal: 1,
  prompt: "Inspect the repository",
  status: "completed",
  stopReason: "completed",
  errorCode: null,
  errorMessage: null,
  startedAt: "2026-07-16T00:00:01Z",
  completedAt: "2026-07-16T00:00:03Z",
};

const failedTurn: Turn = {
  ...turn,
  status: "interrupted",
  stopReason: "application_restarted",
  completedAt: "2026-07-16T00:00:04Z",
};

const runningTurn: Turn = {
  ...turn,
  status: "running",
  stopReason: null,
  completedAt: null,
};

const failedCompletion: Item = {
  id: "item-recovered-completion",
  threadId: thread.id,
  turnId: failedTurn.id,
  ordinal: 2,
  kind: "completion",
  status: "failed",
  summary: "Turn interrupted after application restart",
  payload: { stopReason: "application_restarted" },
  createdAt: "2026-07-16T00:00:04Z",
  updatedAt: "2026-07-16T00:00:04Z",
};

const pendingApproval: Approval = {
  id: "apr-1",
  threadId: thread.id,
  turnId: turn.id,
  itemId: "item-approval",
  category: "command",
  status: "pending",
  request: {
    toolName: "execute_bash",
    arguments: { command: "pytest -q" },
    reason: "Run the project test suite.",
  },
  decision: null,
  requestedAt: "2026-07-16T00:00:02Z",
  resolvedAt: null,
};

const recoveryEvents: Event[] = [
  {
    eventId: "event-1",
    sequence: 1,
    type: "turn.completed",
    threadId: thread.id,
    turnId: turn.id,
    itemId: null,
    timestamp: "2026-07-16T00:00:03Z",
    payload: { turn: turn as unknown as JsonValue },
  },
  {
    eventId: "event-2",
    sequence: 2,
    type: "item.created",
    threadId: thread.id,
    turnId: turn.id,
    itemId: "item-1",
    timestamp: "2026-07-16T00:00:02Z",
    payload: {
      item: {
        id: "item-1",
        threadId: thread.id,
        turnId: turn.id,
        ordinal: 1,
        kind: "assistant_message",
        status: "completed",
        summary: "Recovered final answer",
        payload: { text: "Recovered final answer", streaming: false },
        createdAt: "2026-07-16T00:00:02Z",
        updatedAt: "2026-07-16T00:00:02Z",
      },
    },
  },
];

const failedRecoveryEvents: Event[] = [
  {
    eventId: "event-recovered-turn",
    sequence: 1,
    type: "turn.recovered",
    threadId: thread.id,
    turnId: failedTurn.id,
    itemId: null,
    timestamp: failedTurn.completedAt ?? failedCompletion.updatedAt,
    payload: { turn: failedTurn as unknown as JsonValue },
  },
  {
    eventId: "event-recovered-completion",
    sequence: 2,
    type: "item.created",
    threadId: thread.id,
    turnId: failedTurn.id,
    itemId: failedCompletion.id,
    timestamp: failedCompletion.updatedAt,
    payload: { item: failedCompletion as unknown as JsonValue },
  },
];

const runningEvents: Event[] = [
  {
    eventId: "event-running-turn",
    sequence: 1,
    type: "turn.updated",
    threadId: thread.id,
    turnId: runningTurn.id,
    itemId: null,
    timestamp: "2026-07-16T00:00:02Z",
    payload: { turn: runningTurn as unknown as JsonValue },
  },
];

const approvalEvents: Event[] = [
  {
    eventId: "event-waiting-turn",
    sequence: 1,
    type: "turn.updated",
    threadId: thread.id,
    turnId: turn.id,
    itemId: null,
    timestamp: pendingApproval.requestedAt,
    payload: {
      turn: {
        ...turn,
        status: "waiting_approval",
      } as unknown as JsonValue,
    },
  },
  {
    eventId: "event-approval-item",
    sequence: 2,
    type: "item.created",
    threadId: thread.id,
    turnId: turn.id,
    itemId: pendingApproval.itemId,
    timestamp: pendingApproval.requestedAt,
    payload: {
      item: {
        id: pendingApproval.itemId,
        threadId: thread.id,
        turnId: turn.id,
        ordinal: 2,
        kind: "approval_request",
        status: "pending",
        summary: "Approval required: execute_bash",
        payload: pendingApproval.request,
        createdAt: pendingApproval.requestedAt,
        updatedAt: pendingApproval.requestedAt,
      },
    },
  },
  {
    eventId: "event-approval-requested",
    sequence: 3,
    type: "approval.requested",
    threadId: thread.id,
    turnId: turn.id,
    itemId: pendingApproval.itemId,
    timestamp: pendingApproval.requestedAt,
    payload: { approval: pendingApproval as unknown as JsonValue },
  },
];

const paperThread: Thread = {
  ...thread,
  id: "thread-paper",
  title: "Paper reproduction",
  mode: "paper",
  status: "waiting",
};

const waitingWorkflow: WorkflowRun = {
  id: "workflow-1",
  threadId: paperThread.id,
  turnId: "turn-paper",
  kind: "paper2code",
  status: "waiting",
  input: { sourceType: "url", source: "https://example.com/paper.pdf", options: {} },
  result: {},
  attempt: 1,
  retryOf: null,
  currentStage: "planning",
  progressCurrent: 65,
  progressTotal: 100,
  checkpoint: {
    interaction: {
      id: "wfi-1",
      request: {
        title: "Review Implementation Plan",
        description: "Check the generated plan before code generation.",
        data: { plan_preview: "file_structure:\n  - src/main.py" },
      },
    },
  },
  createdAt: "2026-07-16T00:00:00Z",
  updatedAt: "2026-07-16T00:00:03Z",
  startedAt: "2026-07-16T00:00:01Z",
  completedAt: null,
  errorCode: null,
  errorMessage: null,
};

const workflowEvents: Event[] = [
  {
    eventId: "event-workflow",
    sequence: 1,
    type: "workflow.interaction_requested",
    threadId: paperThread.id,
    turnId: waitingWorkflow.turnId,
    itemId: null,
    timestamp: waitingWorkflow.updatedAt,
    payload: { workflow: waitingWorkflow as unknown as JsonValue },
  },
];

describe("desktop command center", () => {
  beforeEach(() => localStorage.clear());
  afterEach(cleanup);

  it("renders an honest empty state backed by the ready runtime", async () => {
    render(<App runtime={new TestRuntime()} />);

    expect(
      screen.getByRole("heading", { name: "Start a local coding thread" }),
    ).toBeTruthy();
    expect(
      screen.getAllByRole("button", { name: "Open project folder" }),
    ).toHaveLength(2);
    await waitFor(() => expect(screen.getByText("Local agent ready")).toBeTruthy());
  });

  it("navigates to the real Skill and Hook inventory for the selected project", async () => {
    const runtime = new TestRuntime([project], [thread], []);
    render(<App runtime={runtime} />);

    await screen.findByRole("heading", { name: "Recovered task" });
    fireEvent.click(screen.getByRole("button", { name: "Skills & Hooks" }));

    await screen.findByRole("heading", { name: "Skills & Hooks" });
    await waitFor(() => {
      expect(runtime.calls).toContain("skills/list");
      expect(runtime.calls).toContain("hooks/list");
    });
    fireEvent.click(screen.getByRole("button", { name: /review/i }));
    expect(await screen.findByText("concrete evidence")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: /Hooks 1/ }));
    expect(screen.getByText("python3 check.py")).toBeTruthy();

    const skillsRequest = runtime.requests.find(
      (candidate) => candidate.method === "skills/list",
    );
    expect(skillsRequest?.params).toEqual({ projectId: project.id });
  });

  it("runs and manages a durable automation backed by a Goal Thread", async () => {
    const runtime = new TestRuntime([project], [thread, goalThread], []);
    render(<App runtime={runtime} />);

    await screen.findByRole("heading", { name: "Recovered task" });
    fireEvent.click(screen.getByRole("button", { name: "Automations" }));

    await screen.findByRole("heading", { name: "Automations" });
    expect(
      await screen.findByRole("heading", { name: "Repository caretaker" }),
    ).toBeTruthy();
    expect(
      screen.getByText(/runs while DeepCode Desktop is open/),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Run now" }));
    await waitFor(() => expect(runtime.calls).toContain("automation/run"));

    fireEvent.click(screen.getByRole("button", { name: "Runs" }));
    expect(await screen.findByText(/· completed/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    await waitFor(() => {
      const request = runtime.requests.find(
        (candidate) =>
          candidate.method === "automation/update" &&
          (candidate.params as MethodParams["automation/update"]).status ===
            "paused",
      );
      expect(request).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Open Thread" }));
    await screen.findByRole("heading", { name: "Repository caretaker" });
    expect(
      runtime.requests.filter(
        (candidate) =>
          candidate.method === "thread/resume" &&
          (candidate.params as MethodParams["thread/resume"]).sessionId ===
            goalThread.id,
      ),
    ).toHaveLength(1);
  });

  it("shows honest MCP configuration state without claiming a live connection", async () => {
    const runtime = new TestRuntime([project], [thread], []);
    render(<App runtime={runtime} />);

    await screen.findByRole("heading", { name: "Recovered task" });
    fireEvent.click(screen.getByRole("button", { name: "MCP" }));

    await screen.findByRole("heading", { name: "MCP configuration" });
    expect(await screen.findByRole("heading", { name: "filesystem" })).toBeTruthy();
    expect(
      screen.getByText(/“Configured” does not claim a live connection/),
    ).toBeTruthy();
    expect(runtime.requests.find((request) => request.method === "mcp/list")?.params)
      .toEqual({ projectId: project.id });
  });

  it("loads effective Settings and sanitized diagnostics for the selected project", async () => {
    const runtime = new TestRuntime([project], [thread], []);
    render(<App runtime={runtime} />);

    await screen.findByRole("heading", { name: "Recovered task" });
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));

    await screen.findByRole("heading", { name: "Settings" });
    expect(await screen.findByText("SQLite integrity check passed")).toBeTruthy();
    expect(
      runtime.requests.find((request) => request.method === "diagnostics/read")
        ?.params,
    ).toEqual({ projectId: project.id });

    fireEvent.click(screen.getByRole("button", { name: "Export report" }));
    expect(
      await screen.findByText(
        "Sanitized diagnostics saved to /tmp/deepcode-diagnostics-test.json",
      ),
    ).toBeTruthy();
    expect(runtime.diagnosticsExports).toEqual([diagnostics]);
  });

  it("checks and installs only a verified desktop update selected by the user", async () => {
    const runtime = new TestRuntime([project], [thread], [], [], {
      currentVersion: "0.1.0",
      version: "0.2.0",
      date: "2026-07-16T00:00:00Z",
      body: "Release reliability improvements.",
    });
    render(<App runtime={runtime} />);

    await screen.findByRole("heading", { name: "Recovered task" });
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    await screen.findByRole("heading", { name: "Application updates" });
    fireEvent.click(
      screen.getByRole("button", { name: "Check for updates" }),
    );

    expect(
      await screen.findByText(/DeepCode 0.2.0 is available/),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Install 0.2.0" }));
    await waitFor(() => expect(runtime.updateInstallCount).toBe(1));
  });

  it("restores a thread and its final durable item from event replay", async () => {
    const runtime = new TestRuntime([project], [thread], recoveryEvents);
    render(<App runtime={runtime} />);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Recovered task" })).toBeTruthy();
      expect(screen.getByText("Recovered final answer")).toBeTruthy();
    });
    expect(runtime.calls.slice(0, 5)).toEqual([
      "project/list",
      "thread/list",
      "thread/resume",
      "event/replay",
      "settings/read",
    ]);
    expect(runtime.calls).not.toContain("file/list");
    fireEvent.click(screen.getByRole("button", { name: /Review/ }));
    await waitFor(() => {
      expect(runtime.calls).toContain("file/list");
      expect(runtime.calls).toContain("git/diff");
    });
  });

  it("keeps missing-workspace Sessions readable and non-executable", async () => {
    const recoveredProject: Project = {
      ...project,
      id: "project-recovered-history",
      canonicalPath:
        "/tmp/.deepcode/sessions/.missing-workspaces/session-77f8ff1b",
      displayName: "session-77f8ff1b",
    };
    const recoveredThread: Thread = {
      ...thread,
      id: "session-77f8ff1b",
      projectId: recoveredProject.id,
      title: "Session 77f8ff1b",
      workspacePath: recoveredProject.canonicalPath,
    };
    const runtime = new TestRuntime(
      [recoveredProject],
      [recoveredThread],
      [],
    );
    render(<App runtime={runtime} />);

    await screen.findByRole("heading", { name: "Session 77f8ff1b" });

    expect(screen.getAllByText("Previous sessions").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Folder unavailable").length).toBeGreaterThan(0);
    expect(
      screen.getByText(
        "The original folder is unavailable. This Session remains readable.",
      ),
    ).toBeTruthy();
    expect(
      (screen.getByRole("textbox", {
        name: "Task instruction",
      }) as HTMLTextAreaElement).disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("button", {
        name: /New thread/,
      }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(screen.queryByText("Trusted")).toBeNull();
  });

  it("searches Sessions across projects and changes project context atomically", async () => {
    const secondProject: Project = {
      ...project,
      id: "project-2",
      canonicalPath: "/workspace/another",
      displayName: "Another repo",
    };
    const secondThread: Thread = {
      ...thread,
      id: "thread-2",
      projectId: secondProject.id,
      title: "Cross-project task",
      workspacePath: secondProject.canonicalPath,
      updatedAt: "2026-07-16T01:00:00Z",
    };
    const runtime = new TestRuntime(
      [project, secondProject],
      [thread, secondThread],
      [],
    );
    render(<App runtime={runtime} />);

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Recovered task" })).toBeTruthy(),
    );
    fireEvent.change(screen.getByPlaceholderText("Search Sessions"), {
      target: { value: "Cross-project" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Open Session Cross-project task" }),
    );

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Cross-project task" })).toBeTruthy();
      expect(screen.getAllByText("Another repo").length).toBeGreaterThanOrEqual(2);
    });
  });

  it("renames and archives the canonical Session through the App Server", async () => {
    const runtime = new TestRuntime([project], [thread], recoveryEvents);
    render(<App runtime={runtime} />);

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Recovered task" })).toBeTruthy(),
    );
    fireEvent.click(
      screen.getByRole("button", {
        name: "Session actions for Recovered task",
      }),
    );
    fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    const renameInput = screen.getByRole("textbox", { name: "Rename Session" });
    fireEvent.change(renameInput, { target: { value: "Architecture audit" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Session name" }));

    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "Architecture audit" }),
      ).toBeTruthy(),
    );
    fireEvent.click(
      screen.getByRole("button", {
        name: "Session actions for Architecture audit",
      }),
    );
    fireEvent.click(screen.getByRole("menuitem", { name: "Archive" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Archive" }),
    );

    await waitFor(() => {
      expect(
        screen.getByRole("heading", {
          name: "No Sessions here yet.",
        }),
      ).toBeTruthy();
      expect(
        screen.queryByRole("button", {
          name: "Open Session Architecture audit",
        }),
      ).toBeNull();
    });
    expect(runtime.calls).toContain("thread/rename");
    expect(runtime.calls).toContain("thread/archive");
  });

  it("retries a turn that was interrupted by App Server recovery", async () => {
    const runtime = new TestRuntime([project], [thread], failedRecoveryEvents);
    render(<App runtime={runtime} />);

    const retry = await screen.findByRole("button", { name: "Retry" });
    expect(
      screen.getByText("The previous process stopped. Retry from the same prompt."),
    ).toBeTruthy();
    fireEvent.click(retry);

    await waitFor(() => expect(runtime.calls).toContain("turn/start"));
  });

  it("shows approval arguments and applies the durable decision", async () => {
    const runtime = new TestRuntime([project], [thread], approvalEvents);
    render(<App runtime={runtime} />);

    await screen.findByText("execute_bash");
    fireEvent.click(screen.getByText("Review arguments"));
    expect(screen.getByText(/pytest -q/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Allow once" }));

    await waitFor(() =>
      expect(screen.getByText("Decision: approved once")).toBeTruthy(),
    );
    expect(runtime.calls).toContain("approval/respond");
  });

  it("keeps a per-Session draft and sends with Enter", async () => {
    const firstRuntime = new TestRuntime([project], [thread], []);
    const firstView = render(<App runtime={firstRuntime} />);
    const firstComposer = await screen.findByRole("textbox", {
      name: "Task instruction",
    });
    fireEvent.change(firstComposer, {
      target: { value: "Finish the recovery audit" },
    });
    firstView.unmount();

    const secondRuntime = new TestRuntime([project], [thread], []);
    render(<App runtime={secondRuntime} />);
    const restoredComposer = await screen.findByRole("textbox", {
      name: "Task instruction",
    });
    expect((restoredComposer as HTMLTextAreaElement).value).toBe(
      "Finish the recovery audit",
    );
    fireEvent.keyDown(restoredComposer, { key: "Enter" });

    await waitFor(() => expect(secondRuntime.calls).toContain("turn/start"));
    expect((restoredComposer as HTMLTextAreaElement).value).toBe("");
  });

  it("titles a new Desktop Session from its first prompt like the CLI", async () => {
    const untitledThread = { ...thread, title: "New task" };
    const runtime = new TestRuntime([project], [untitledThread], []);
    render(<App runtime={runtime} />);

    const composer = await screen.findByRole("textbox", {
      name: "Task instruction",
    });
    fireEvent.change(composer, {
      target: {
        value: "Implement durable model selection\nKeep CLI behavior unchanged.",
      },
    });
    fireEvent.keyDown(composer, { key: "Enter" });

    await waitFor(() =>
      expect(
        screen.getByRole("heading", {
          name: "Implement durable model selection",
        }),
      ).toBeTruthy(),
    );
    expect(runtime.calls).toContain("thread/rename");
  });

  it("applies real per-Session model and shared permission settings", async () => {
    const runtime = new TestRuntime([project], [thread], []);
    render(<App runtime={runtime} />);

    const model = await screen.findByRole("combobox", { name: "Session model" });
    const permissions = screen.getByRole("combobox", {
      name: "Permission mode",
    });
    expect((permissions as HTMLSelectElement).value).toBe("default");

    fireEvent.change(model, { target: { value: "gpt-5-mini" } });
    await waitFor(() => {
      expect((model as HTMLSelectElement).value).toBe("gpt-5-mini");
      expect(runtime.calls).toContain("thread/model");
    });

    fireEvent.change(permissions, { target: { value: "plan" } });
    await waitFor(() => {
      expect((permissions as HTMLSelectElement).value).toBe("plan");
      expect(runtime.calls).toContain("settings/update");
    });
  });

  it("attaches only workspace files and sends relative context references", async () => {
    const runtime = new TestRuntime(
      [project],
      [thread],
      [],
      ["/workspace/deepcode/src/App.tsx", "/tmp/outside.txt"],
    );
    render(<App runtime={runtime} />);

    await screen.findByRole("heading", { name: "Recovered task" });
    fireEvent.click(
      screen.getByRole("button", { name: "Attach workspace files" }),
    );
    await screen.findByText("App.tsx");
    expect(
      screen.getByText("Only files inside this Session workspace can be attached."),
    ).toBeTruthy();

    const composer = screen.getByRole("textbox", { name: "Task instruction" });
    fireEvent.change(composer, { target: { value: "Review this component" } });
    fireEvent.keyDown(composer, { key: "Enter" });

    await waitFor(() => expect(runtime.calls).toContain("turn/start"));
    const request = runtime.requests.find(
      (candidate) => candidate.method === "turn/start",
    )?.params as MethodParams["turn/start"];
    expect(request.prompt).toContain("Review this component");
    expect(request.prompt).toContain("- src/App.tsx");
    expect(request.prompt).not.toContain("/tmp/outside.txt");
  });

  it("executes slash commands locally instead of sending fake Agent prompts", async () => {
    const runtime = new TestRuntime([project], [thread], []);
    render(<App runtime={runtime} />);
    const composer = await screen.findByRole("textbox", {
      name: "Task instruction",
    });

    fireEvent.change(composer, {
      target: { value: "/rename Command-driven Session" },
    });
    fireEvent.keyDown(composer, { key: "Enter" });
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "Command-driven Session" }),
      ).toBeTruthy(),
    );
    expect(runtime.calls).not.toContain("turn/start");

    fireEvent.change(composer, { target: { value: "/review" } });
    fireEvent.keyDown(composer, { key: "Enter" });
    await waitFor(() =>
      expect(screen.getByRole("complementary", { name: "Inspector" })).toBeTruthy(),
    );
    expect(runtime.calls).not.toContain("turn/start");
  });

  it("queues and cancels the next durable Turn while the Agent is active", async () => {
    const runningThread = { ...thread, status: "running" as const };
    const runtime = new TestRuntime([project], [runningThread], runningEvents);
    render(<App runtime={runtime} />);

    const composer = await screen.findByRole("textbox", {
      name: "Task instruction",
    });
    fireEvent.change(composer, {
      target: { value: "Run the next verification pass" },
    });
    fireEvent.keyDown(composer, { key: "Enter" });

    await waitFor(() => expect(runtime.calls).toContain("turn/enqueue"));
    expect(screen.getByText("Queued")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(runtime.calls).toContain("turn/interrupt"));
  });

  it("restores a waiting Paper2Code review without using the agent composer", async () => {
    const view = render(
      <App runtime={new TestRuntime([project], [paperThread], workflowEvents)} />,
    );

    await waitFor(() => {
      expect(screen.getByText("Review Implementation Plan")).toBeTruthy();
      expect(screen.getByText("65%")).toBeTruthy();
      expect(screen.getByRole("button", { name: "Approve & continue" })).toBeTruthy();
    });
    expect(view.container.querySelector("#turn-prompt")).toBeNull();
  });
});
