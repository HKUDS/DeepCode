import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  Approval,
  Automation,
  AutomationRun,
  DiagnosticsSnapshot,
  Event,
  ExecutionSecurityProfile,
  Goal,
  GoalOutcome,
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
import { __resetComposerBehaviorForTests } from "./app/composerBehavior";
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

const askSecurityProfile: ExecutionSecurityProfile = {
  accessPreset: "ask",
  permissionMode: "default",
  commandSandbox: true,
  filesystemScope: "workspace",
  approvalPolicy: "on_request",
  permissionRules: [],
};

const fullAccessSecurityProfile: ExecutionSecurityProfile = {
  accessPreset: "full_access",
  permissionMode: "full_auto",
  commandSandbox: false,
  filesystemScope: "unrestricted",
  approvalPolicy: "never",
  permissionRules: [],
};

const readOnlySecurityProfile: ExecutionSecurityProfile = {
  accessPreset: "read_only",
  permissionMode: "plan",
  commandSandbox: true,
  filesystemScope: "workspace",
  approvalPolicy: "never",
  permissionRules: [],
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
  userAccessPreset: null,
  projectAccessPreset: null,
  resolvedDefaultSecurityProfile: askSecurityProfile,
  resolvedDefaultSecuritySource: "built_in",
  providers: [
    {
      id: "openai",
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

const SKILL_ID = "sk_0123456789abcdef01234567";
const VERIFY_SKILL_ID = "sk_89abcdef0123456701234567";
const CREATOR_SKILL_ID = "sk_111111111111111111111111";
const SKILL_REVISION = `sha256:${"a".repeat(64)}`;
const CATALOG_REVISION = `sha256:${"b".repeat(64)}`;
const reviewSkill = {
  id: SKILL_ID,
  name: "review",
  description: "Review a change carefully",
  allowedTools: ["read", "grep"],
  scope: "project",
  sourceRoot: "agents",
  source: "project:agents",
  location: "project/.agents/skills/review",
  originKind: "local",
  originLabel: "project:agents",
  providerKind: "local",
  providerId: "local",
  packageId: SKILL_ID,
  status: "active",
  enabled: true,
  selectable: true,
  revision: SKILL_REVISION,
  byteSize: 137,
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
} as const;
const verifySkill = {
  ...reviewSkill,
  id: VERIFY_SKILL_ID,
  packageId: VERIFY_SKILL_ID,
  name: "verify",
  description: "Run the focused verification",
  allowedTools: ["bash"],
  revision: `sha256:${"c".repeat(64)}`,
  byteSize: 121,
  location: "project/.agents/skills/verify",
} as const;
const creatorSkill = {
  ...reviewSkill,
  id: CREATOR_SKILL_ID,
  name: "skill-creator",
  displayName: "Skill Creator",
  description: "Create reusable Agent Skills",
  shortDescription: "Create and validate reusable Agent Skills",
  scope: "system",
  sourceRoot: "system",
  source: "system:system",
  location: "system/bundled/skills/skill-creator",
  originKind: "bundled",
  originLabel: "DeepCode bundled",
  packageId: CREATOR_SKILL_ID,
  revision: `sha256:${"d".repeat(64)}`,
  defaultPrompt:
    "Use $skill-creator to create a focused reusable Skill for this project.",
  deletable: false,
} as const;

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
  readonly openedPaths: string[] = [];
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
  private goalState: Goal | null;
  private goalOutcomeState: GoalOutcome | null;
  private readonly disabledSkillIds = new Set<string>();
  private readonly deletedSkillIds = new Set<string>();

  constructor(
    private readonly projects: Project[] = [],
    threads: Thread[] = [],
    private readonly events: Event[] = [],
    private readonly contextFiles: string[] = [],
    private readonly availableUpdate: DesktopUpdateInfo | null = null,
    initialGoal: Goal | null = null,
    initialGoalOutcome: GoalOutcome | null = null,
  ) {
    this.threadState = threads.map((candidate) => ({ ...candidate }));
    this.goalState = initialGoal;
    this.goalOutcomeState = initialGoalOutcome;
  }

  readonly presetState = new Map<string, string>();

  async request<M extends RpcMethod>(
    method: M,
    params: MethodParams[M],
  ): Promise<MethodResults[M]> {
    void params;
    this.calls.push(method);
    this.requests.push({ method, params });
    switch (method) {
      case "provider/list":
        return {
          connections: [
            {
              id: "openai",
              label: "OpenAI",
              providerName: "openai",
              adapter: "openai_compat",
              apiBase: "https://api.openai.com/v1",
              apiKeyEnv: "OPENAI_API_KEY",
              modelCatalog: "openai",
              manualModels: [],
              configured: true,
              credentialSource: "environment",
              local: false,
              enabled: true,
              explicit: false,
            },
            {
              id: "anthropic",
              label: "Anthropic",
              providerName: "anthropic",
              adapter: "anthropic",
              apiBase: "https://api.anthropic.com",
              apiKeyEnv: "ANTHROPIC_API_KEY",
              modelCatalog: "anthropic",
              manualModels: [],
              configured: false,
              credentialSource: "missing",
              local: false,
              enabled: true,
              explicit: false,
            },
          ],
          templates: [
            {
              name: "openai",
              label: "OpenAI",
              adapter: "openai_compat",
              defaultApiBase: "https://api.openai.com/v1",
              apiKeyEnv: "OPENAI_API_KEY",
              requiresApiBase: false,
              local: false,
            },
            {
              name: "openrouter",
              label: "OpenRouter",
              adapter: "openai_compat",
              defaultApiBase: "https://openrouter.ai/api/v1",
              apiKeyEnv: "OPENROUTER_API_KEY",
              requiresApiBase: false,
              local: false,
            },
            {
              name: "anthropic",
              label: "Anthropic",
              adapter: "anthropic",
              defaultApiBase: "https://api.anthropic.com",
              apiKeyEnv: "ANTHROPIC_API_KEY",
              requiresApiBase: false,
              local: false,
            },
          ],
          configPath: "/tmp/deepcode_config.json",
          credentialPath: "/tmp/credentials.json",
        } as unknown as MethodResults[M];
      case "model/list": {
        const request = params as MethodParams["model/list"];
        return {
          connectionId: request.connectionId,
          models: desktopSettings.models.map((model) => ({
            id: model.id,
            name: model.id,
            contextWindow: model.contextWindow,
            maxOutputTokens: model.maxOutputTokens,
            supportedParameters: [],
            reasoning: {
              supportedEfforts: ["low", "medium", "high"],
              defaultEffort: "medium",
              defaultEnabled: true,
              mandatory: false,
              supportsSummary: true,
            },
          })),
          source: "test",
          stale: false,
          error: null,
          refreshedAt: 1_768_000_000,
        } as unknown as MethodResults[M];
      }
      case "provider/upsert": {
        const request = params as MethodParams["provider/upsert"];
        const connection = request.connection;
        return {
          connections: [
            {
              id: "openai",
              label: "OpenAI",
              providerName: "openai",
              adapter: "openai_compat",
              apiBase: "https://api.openai.com/v1",
              apiKeyEnv: "OPENAI_API_KEY",
              modelCatalog: "openai",
              manualModels: [],
              configured: true,
              credentialSource: "environment",
              local: false,
              enabled: true,
              explicit: false,
            },
            {
              id: connection.id,
              label: connection.label ?? connection.id,
              providerName: connection.template ?? "custom",
              adapter: connection.adapter ?? "openai_compat",
              apiBase: connection.apiBase ?? null,
              apiKeyEnv: connection.apiKeyEnv ?? null,
              modelCatalog:
                connection.modelCatalog === "auto" ||
                connection.modelCatalog === undefined
                  ? "openrouter"
                  : connection.modelCatalog,
              manualModels: connection.manualModels ?? [],
              configured: Boolean(connection.apiKey || connection.apiKeyEnv),
              credentialSource: connection.apiKey
                ? "credential_store"
                : "environment",
              local: false,
              enabled: connection.enabled ?? true,
              explicit: true,
            },
          ],
          templates: [],
          configPath: "/tmp/deepcode_config.json",
          credentialPath: "/tmp/credentials.json",
        } as unknown as MethodResults[M];
      }
      case "provider/test": {
        const request = params as MethodParams["provider/test"];
        return {
          connectionId: request.connectionId,
          status: request.model ? "ready" : "connected",
          ok: true,
          latencyMs: 42,
          modelCount: 2,
          error: null,
          stages: [
            {
              id: "credential",
              status: "passed",
              detail: "Credential loaded from DeepCode private storage",
              latencyMs: null,
              modelCount: null,
              modelId: null,
            },
            {
              id: "catalog",
              status: "passed",
              detail: "Discovered 2 models",
              latencyMs: 12,
              modelCount: 2,
              modelId: null,
            },
            {
              id: "model",
              status: request.model ? "passed" : "not_run",
              detail: request.model
                ? "The provider accepted a real inference request"
                : "Choose a model to run a minimal verification request",
              latencyMs: request.model ? 30 : null,
              modelCount: null,
              modelId: request.model ?? null,
            },
          ],
        } as unknown as MethodResults[M];
      }
      case "provider/remove":
        return {
          removed: true,
          connections: [],
          templates: [],
          configPath: "/tmp/deepcode_config.json",
          credentialPath: "/tmp/credentials.json",
        } as unknown as MethodResults[M];
      case "project/list":
        return { projects: this.projects } as MethodResults[M];
      case "project/update": {
        const request = params as MethodParams["project/update"];
        const index = this.projects.findIndex(
          (candidate) => candidate.id === request.projectId,
        );
        if (index === -1) {
          throw new Error(`Missing test project: ${request.projectId}`);
        }
        this.projects[index] = {
          ...this.projects[index],
          ...(request.displayName ? { displayName: request.displayName } : {}),
          ...(request.trustState ? { trustState: request.trustState } : {}),
        };
        return { project: this.projects[index] } as MethodResults[M];
      }
      case "settings/read":
        return { settings: this.settingsState } as MethodResults[M];
      case "settings/update": {
        const request = params as MethodParams["settings/update"];
        const security = request.patch.security;
        const accessPreset =
          typeof security === "object" &&
          security !== null &&
          !Array.isArray(security) &&
          Object.hasOwn(security, "accessPreset")
            ? security.accessPreset
            : undefined;
        const updatesPermissionMode =
          typeof security === "object" &&
          security !== null &&
          !Array.isArray(security) &&
          Object.hasOwn(security, "permissionMode");
        this.settingsState = {
          ...this.settingsState,
          security:
            typeof security === "object" &&
            security !== null &&
            !Array.isArray(security)
              ? { ...this.settingsState.security, ...security }
              : this.settingsState.security,
          permissionModeExplicit:
            this.settingsState.permissionModeExplicit || updatesPermissionMode,
          ...(accessPreset !== undefined
            ? {
                [request.scope === "project"
                  ? "projectAccessPreset"
                  : "userAccessPreset"]:
                  accessPreset === "ask" ||
                  accessPreset === "read_only" ||
                  accessPreset === "full_access"
                    ? accessPreset
                    : null,
                resolvedDefaultSecurityProfile:
                  accessPreset === "full_access"
                    ? fullAccessSecurityProfile
                    : accessPreset === "read_only"
                      ? readOnlySecurityProfile
                      : askSecurityProfile,
                resolvedDefaultSecuritySource:
                  request.scope === "project" ? "project" : "user",
              }
            : {}),
        };
        return { settings: this.settingsState } as MethodResults[M];
      }
      case "preset/list":
        return {
          presets: [
            {
              id: "code-reader",
              trust: "system",
              name: "Code reader",
              description: "Read-only investigator",
              tools: ["read", "grep", "glob", "skill"],
              broken: null,
            },
            {
              id: "damaged",
              trust: "project",
              name: "damaged",
              description: "",
              tools: null,
              broken: "missing YAML frontmatter block",
            },
          ],
        } as unknown as MethodResults[M];
      case "preset/current": {
        const request = params as MethodParams["preset/current"];
        return {
          agentPreset: this.presetState.get(request.threadId) ?? null,
        } as MethodResults[M];
      }
      case "preset/select": {
        const request = params as MethodParams["preset/select"];
        if (request.agentPreset === null) {
          this.presetState.delete(request.threadId);
        } else {
          this.presetState.set(request.threadId, request.agentPreset);
        }
        return { agentPreset: request.agentPreset } as MethodResults[M];
      }
      case "skills/list":
        return this.skillCatalog() as unknown as MethodResults[M];
      case "skill/read": {
        const request = params as MethodParams["skill/read"];
        const skill =
          [reviewSkill, verifySkill, creatorSkill].find(
            (candidate) =>
              candidate.id === request.skillId ||
              candidate.name === request.name,
          ) ?? reviewSkill;
        return {
          skill: {
            ...skill,
            ...(this.disabledSkillIds.has(skill.id)
              ? {
                  status: "disabled" as const,
                  enabled: false,
                  selectable: false,
                }
              : {}),
            instructions:
              "Inspect the change and report **concrete evidence**.",
            truncated: false,
          },
        } as unknown as MethodResults[M];
      }
      case "skills/set-enabled": {
        const request = params as MethodParams["skills/set-enabled"];
        if (request.enabled) {
          this.disabledSkillIds.delete(request.skillId);
        } else {
          this.disabledSkillIds.add(request.skillId);
        }
        return this.skillCatalog() as unknown as MethodResults[M];
      }
      case "skills/delete": {
        const request = params as MethodParams["skills/delete"];
        this.deletedSkillIds.add(request.skillId);
        return { removed: true } as MethodResults[M];
      }
      case "skills/reload":
        return this.skillCatalog() as unknown as MethodResults[M];
      case "plugins/list":
        return {
          plugins: [],
          diagnostics: [],
          revision: `sha256:${"0".repeat(64)}`,
        } as unknown as MethodResults[M];
      case "mcp/list":
        return {
          servers: [],
          userConfigPath: "/tmp/deepcode_config.json",
          projectConfigPath: "/workspace/deepcode/deepcode_config.json",
        } as unknown as MethodResults[M];
      case "diagnostics/read":
        return { diagnostics } as MethodResults[M];
      case "automation/list":
        return {
          automations: [{ ...automation, status: this.automationStatus }],
          latestRuns: [automationRun],
          schedulerActive: true,
          executionMode: "requires_live_runtime",
          hasMore: false,
          nextOffset: null,
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
        return {
          runs: [automationRun],
          hasMore: false,
          nextOffset: null,
        } as unknown as MethodResults[M];
      case "thread/list":
        return {
          threads: this.threadState.filter(
            (candidate) => candidate.status !== "archived",
          ),
        } as MethodResults[M];
      case "thread/start": {
        const request = params as MethodParams["thread/start"];
        const created = {
          ...thread,
          id: `thread-created-${this.threadState.length + 1}`,
          projectId: request.projectId,
          workspacePath:
            this.projects.find(
              (candidate) => candidate.id === request.projectId,
            )?.canonicalPath ?? thread.workspacePath,
          title: request.title,
          mode: request.mode ?? "code",
        };
        this.threadState.push(created);
        return { thread: created } as MethodResults[M];
      }
      case "thread/resume": {
        const sessionId = (params as MethodParams["thread/resume"]).sessionId;
        const resumed = this.threadState.find(
          (candidate) => candidate.id === sessionId,
        );
        if (!resumed) throw new Error(`Missing test thread: ${sessionId}`);
        return { thread: resumed } as MethodResults[M];
      }
      case "thread/rename": {
        const request = params as MethodParams["thread/rename"];
        const index = this.threadState.findIndex(
          (candidate) => candidate.id === request.threadId,
        );
        if (index === -1)
          throw new Error(`Missing test thread: ${request.threadId}`);
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
        if (index === -1)
          throw new Error(`Missing test thread: ${request.threadId}`);
        this.threadState[index] = {
          ...this.threadState[index],
          connectionId:
            request.connectionId === undefined
              ? this.threadState[index].connectionId
              : request.connectionId,
          model: request.model,
        };
        return { thread: this.threadState[index] } as MethodResults[M];
      }
      case "thread/execution/update": {
        const request = params as MethodParams["thread/execution/update"];
        const index = this.threadState.findIndex(
          (candidate) => candidate.id === request.threadId,
        );
        if (index === -1)
          throw new Error(`Missing test thread: ${request.threadId}`);
        this.threadState[index] = {
          ...this.threadState[index],
          connectionId: request.connectionId,
          model: request.model,
          reasoningEffort: request.reasoningEffort,
        };
        return { thread: this.threadState[index] } as MethodResults[M];
      }
      case "thread/permission/update": {
        const request = params as MethodParams["thread/permission/update"];
        const index = this.threadState.findIndex(
          (candidate) => candidate.id === request.threadId,
        );
        if (index === -1)
          throw new Error(`Missing test thread: ${request.threadId}`);
        this.threadState[index] = {
          ...this.threadState[index],
          accessPresetOverride: request.accessPreset,
        };
        return { thread: this.threadState[index] } as MethodResults[M];
      }
      case "thread/goal/get":
        return {
          goal: this.goalState,
          outcome: this.goalOutcomeState,
        } as MethodResults[M];
      case "thread/goal/set": {
        const request = params as MethodParams["thread/goal/set"];
        const now = "2026-07-16T02:00:00Z";
        this.goalState = {
          id: this.goalState?.id ?? "goal-desktop",
          threadId: request.threadId,
          objective: request.objective ?? this.goalState?.objective ?? "Goal",
          status: this.goalState?.status ?? "active",
          tokenBudget:
            request.tokenBudget !== undefined
              ? request.tokenBudget
              : (this.goalState?.tokenBudget ?? null),
          tokensUsed: this.goalState?.tokensUsed ?? 0,
          timeUsedSeconds: this.goalState?.timeUsedSeconds ?? 0,
          skillIds: request.skills ?? this.goalState?.skillIds ?? [],
          createdAt: this.goalState?.createdAt ?? now,
          updatedAt: now,
        } as Goal;
        return {
          goal: this.goalState,
          outcome: this.goalOutcomeState,
        } as MethodResults[M];
      }
      case "thread/goal/pause":
      case "thread/goal/resume": {
        if (!this.goalState) {
          throw new Error("Missing test Goal");
        }
        this.goalState = {
          ...this.goalState,
          status: method === "thread/goal/pause" ? "paused" : "active",
          updatedAt: "2026-07-16T02:01:00Z",
        };
        this.goalOutcomeState = null;
        return {
          goal: this.goalState,
          outcome: this.goalOutcomeState,
        } as MethodResults[M];
      }
      case "thread/goal/continue": {
        if (!this.goalState) {
          throw new Error("No Goal");
        }
        return {
          goal: this.goalState,
          disposition: "started",
          turnId: "turn-goal-continuation",
          outcome: this.goalOutcomeState,
        } as MethodResults[M];
      }
      case "thread/goal/clear":
        this.goalState = null;
        this.goalOutcomeState = null;
        return { goal: null, outcome: null } as MethodResults[M];
      case "thread/archive": {
        const request = params as MethodParams["thread/archive"];
        const index = this.threadState.findIndex(
          (candidate) => candidate.id === request.threadId,
        );
        if (index === -1)
          throw new Error(`Missing test thread: ${request.threadId}`);
        this.threadState[index] = {
          ...this.threadState[index],
          status: "archived",
          archivedAt: "2026-07-16T02:00:00Z",
        };
        return { thread: this.threadState[index] } as MethodResults[M];
      }
      case "thread/delete": {
        const request = params as MethodParams["thread/delete"];
        const index = this.threadState.findIndex(
          (candidate) => candidate.id === request.threadId,
        );
        if (index === -1)
          throw new Error(`Missing test thread: ${request.threadId}`);
        this.threadState.splice(index, 1);
        return {
          threadId: request.threadId,
          cleanupPending: false,
        } as MethodResults[M];
      }
      case "turn/start": {
        const request = params as MethodParams["turn/start"];
        const startedTurn: Turn = {
          id: "turn-retry",
          threadId: request.threadId,
          ordinal: 2,
          prompt: request.prompt,
          skillIds: request.skills,
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
          payload: {
            text: request.prompt,
            skillIds: request.skills ?? [],
            skills: (request.skills ?? []).map((skillId) => ({
              skillId,
              name:
                skillId === SKILL_ID
                  ? reviewSkill.name
                  : skillId === VERIFY_SKILL_ID
                    ? verifySkill.name
                    : skillId,
              revision: SKILL_REVISION,
              invocation: "explicit",
            })),
          },
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
          skillIds: request.skills,
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
          payload: {
            text: request.prompt,
            skillIds: request.skills ?? [],
            skills: (request.skills ?? []).map((skillId) => ({
              skillId,
              name:
                skillId === SKILL_ID
                  ? reviewSkill.name
                  : skillId === VERIFY_SKILL_ID
                    ? verifySkill.name
                    : skillId,
              revision: SKILL_REVISION,
              invocation: "explicit",
            })),
          },
          createdAt: "2026-07-16T02:00:00Z",
          updatedAt: "2026-07-16T02:00:00Z",
        };
        return {
          turn: queuedTurn,
          items: [userItem],
          approvals: [],
        } as unknown as MethodResults[M];
      }
      case "turn/steer": {
        const request = params as MethodParams["turn/steer"];
        return {
          messageId: request.messageId ?? "desktop-steer",
          delivery: "current_turn",
          duplicate: false,
          turn: runningTurn,
        } as MethodResults[M];
      }
      case "turn/retry": {
        const request = params as MethodParams["turn/retry"];
        return {
          turn: {
            ...failedTurn,
            id: "turn-retry",
            ordinal: failedTurn.ordinal + 1,
            status: "queued",
            stopReason: null,
            errorCode: null,
            errorMessage: null,
            startedAt: null,
            completedAt: null,
          },
          items: [],
          approvals: [],
          originalTurnId: request.turnId,
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
        return {
          events: this.events,
          nextAfter: null,
          hasMore: false,
        } as MethodResults[M];
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

  async openPath(path: string) {
    this.openedPaths.push(path);
  }

  async checkForUpdate() {
    return this.availableUpdate;
  }

  async installUpdate(listener: (progress: DesktopUpdateProgress) => void) {
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

  private skillCatalog() {
    return {
      skills: [reviewSkill, verifySkill, creatorSkill]
        .filter((skill) => !this.deletedSkillIds.has(skill.id))
        .map((skill) =>
          this.disabledSkillIds.has(skill.id)
            ? {
                ...skill,
                status: "disabled" as const,
                enabled: false,
                selectable: false,
              }
            : skill,
        ),
      warnings: [],
      catalogRevision: CATALOG_REVISION,
      authoringSkillId: CREATOR_SKILL_ID,
    };
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
  connectionId: null,
  reasoningEffort: null,
  accessPresetOverride: null,
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
  currentRevisionId: "arev-test",
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
  revisionId: automation.currentRevisionId,
  occurrenceId: "aocc-test",
  goalId: "goal-automation",
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

const presentationItems: Item[] = [
  {
    id: "item-presentation-user",
    threadId: thread.id,
    turnId: turn.id,
    ordinal: 1,
    kind: "user_message",
    status: "completed",
    summary: "Inspect the repository",
    payload: { text: "Inspect the repository" },
    createdAt: "2026-07-16T00:00:01Z",
    updatedAt: "2026-07-16T00:00:01Z",
  },
  {
    id: "item-presentation-plan",
    threadId: thread.id,
    turnId: turn.id,
    ordinal: 2,
    kind: "plan",
    status: "completed",
    summary: "Execution plan",
    payload: { resultPreview: "- Inspect files\n- Run tests" },
    createdAt: "2026-07-16T00:00:01Z",
    updatedAt: "2026-07-16T00:00:01Z",
  },
  {
    id: "item-presentation-tool",
    threadId: thread.id,
    turnId: turn.id,
    ordinal: 3,
    kind: "command_execution",
    status: "completed",
    summary: "Ran the focused tests",
    payload: { resultPreview: "3 tests passed" },
    createdAt: "2026-07-16T00:00:02Z",
    updatedAt: "2026-07-16T00:00:02Z",
  },
  {
    id: "item-presentation-answer",
    threadId: thread.id,
    turnId: turn.id,
    ordinal: 4,
    kind: "assistant_message",
    status: "completed",
    summary: "Repository findings",
    payload: {
      text: "## Repository findings\n\nThe implementation is ready for review.",
    },
    createdAt: "2026-07-16T00:00:03Z",
    updatedAt: "2026-07-16T00:00:03Z",
  },
  {
    id: "item-presentation-completion",
    threadId: thread.id,
    turnId: turn.id,
    ordinal: 5,
    kind: "completion",
    status: "completed",
    summary: "Turn complete",
    payload: { stopReason: "completed" },
    createdAt: "2026-07-16T00:00:03Z",
    updatedAt: "2026-07-16T00:00:03Z",
  },
];

const presentationEvents: Event[] = [
  {
    eventId: "event-presentation-turn",
    sequence: 1,
    type: "turn.completed",
    threadId: thread.id,
    turnId: turn.id,
    itemId: null,
    timestamp: "2026-07-16T00:00:03Z",
    payload: { turn: turn as unknown as JsonValue },
  },
  ...presentationItems.map((candidate, index): Event => ({
    eventId: `event-presentation-${candidate.id}`,
    sequence: index + 2,
    type: "item.created",
    threadId: thread.id,
    turnId: turn.id,
    itemId: candidate.id,
    timestamp: candidate.updatedAt,
    payload: { item: candidate as unknown as JsonValue },
  })),
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
  input: {
    sourceType: "url",
    source: "https://example.com/paper.pdf",
    options: {},
  },
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
  beforeEach(() => {
    localStorage.clear();
    __resetComposerBehaviorForTests();
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders an honest empty state backed by the ready runtime", async () => {
    render(<App runtime={new TestRuntime()} />);

    expect(
      screen.getByRole("heading", { name: "Start a local coding thread" }),
    ).toBeTruthy();
    expect(
      screen.getAllByRole("button", { name: "Open project folder" }),
    ).toHaveLength(2);
    await waitFor(() =>
      expect(screen.getByText("Local agent ready")).toBeTruthy(),
    );
  });

  it("navigates to the shared Skill inventory for the selected project", async () => {
    const runtime = new TestRuntime([project], [thread], []);
    render(<App runtime={runtime} />);

    await screen.findByRole("heading", { name: "Recovered task" });
    fireEvent.click(screen.getByRole("button", { name: "Skills" }));

    await screen.findByRole("heading", { name: "Skills" });
    await waitFor(() => {
      expect(runtime.calls).toContain("skills/list");
    });
    expect(runtime.calls).not.toContain("hooks/list");
    expect(screen.queryByRole("tab", { name: /Hooks/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /review/i }));
    expect(await screen.findByText("concrete evidence")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Disable" }));
    await waitFor(() =>
      expect(
        runtime.requests.some(
          (candidate) =>
            candidate.method === "skills/set-enabled" &&
            (candidate.params as MethodParams["skills/set-enabled"]).skillId ===
              SKILL_ID,
        ),
      ).toBe(true),
    );
    const policyRequest = runtime.requests.find(
      (candidate) => candidate.method === "skills/set-enabled",
    );
    expect(policyRequest?.params).toEqual({
      projectId: project.id,
      skillId: SKILL_ID,
      enabled: false,
      scope: "project",
    });

    const skillsRequest = runtime.requests.find(
      (candidate) => candidate.method === "skills/list",
    );
    expect(skillsRequest?.params).toEqual({ projectId: project.id });
    const detailRequest = runtime.requests.find(
      (candidate) => candidate.method === "skill/read",
    );
    expect(detailRequest?.params).toEqual({
      projectId: project.id,
      skillId: SKILL_ID,
    });
  });

  it("starts Skill creation through the shared skill-creator Turn", async () => {
    const runtime = new TestRuntime([project], [thread], []);
    render(<App runtime={runtime} />);

    await screen.findByRole("heading", { name: "Recovered task" });
    fireEvent.click(screen.getByRole("button", { name: "Skills" }));
    await screen.findByRole("heading", { name: "Skills" });
    fireEvent.click(await screen.findByRole("button", { name: "Create Skill" }));

    // Creating a Skill crosses an async navigation and a thread/start RPC.
    // Synchronize on that observable boundary before asserting the destination UI.
    await waitFor(
      () =>
        expect(
          runtime.requests.some((request) => request.method === "thread/start"),
        ).toBe(true),
      { timeout: 5_000 },
    );
    const composer = (await screen.findByRole(
      "textbox",
      { name: "Task instruction" },
      { timeout: 5_000 },
    )) as HTMLTextAreaElement;
    await waitFor(() =>
      expect(composer.value).toContain("$skill-creator"),
    );
    expect(
      runtime.requests.find((request) => request.method === "thread/start")
        ?.params,
    ).toMatchObject({
      projectId: project.id,
      title: "Create a Skill",
      mode: "code",
    });
    expect(
      screen.getByLabelText("Selected Skills").textContent,
    ).toContain("Skill Creator");
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
      screen.getByText(/runs while a compatible DeepCode runtime is active/),
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

  it("creates and pauses a durable Session Goal with selected Skills", async () => {
    const runtime = new TestRuntime([project], [thread], []);
    render(<App runtime={runtime} />);

    await screen.findByRole("heading", { name: "Recovered task" });
    fireEvent.click(await screen.findByRole("button", { name: /Set a Goal/ }));
    fireEvent.change(screen.getByLabelText("Outcome"), {
      target: {
        value:
          "Ship the implementation. Focused tests pass and the change is reviewed.",
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "review" }));
    fireEvent.click(screen.getByRole("button", { name: "Start Goal" }));

    await waitFor(() => {
      expect(
        screen.getByText(
          "Ship the implementation. Focused tests pass and the change is reviewed.",
        ),
      ).toBeTruthy();
      expect(screen.getByText(/Ready to continue/)).toBeTruthy();
    });
    const setRequest = runtime.requests.find(
      (candidate) => candidate.method === "thread/goal/set",
    );
    expect(setRequest?.params).toMatchObject({
      threadId: thread.id,
      objective:
        "Ship the implementation. Focused tests pass and the change is reviewed.",
      tokenBudget: null,
      skills: [SKILL_ID],
      start: true,
    });

    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() =>
      expect(
        runtime.requests.find(
          (candidate) => candidate.method === "thread/goal/continue",
        )?.params,
      ).toMatchObject({
        threadId: thread.id,
        expectedGoalId: "goal-desktop",
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Edit Goal" }));
    const revisedOutcome = screen.getByLabelText(
      "Outcome",
    ) as HTMLTextAreaElement;
    expect(revisedOutcome.value).toContain("Ship the implementation");
    fireEvent.change(revisedOutcome, {
      target: { value: "Ship the revised implementation and verify it." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Goal" }));
    await waitFor(() =>
      expect(
        screen.getByText("Ship the revised implementation and verify it."),
      ).toBeTruthy(),
    );
    const revisions = runtime.requests.filter(
      (candidate) => candidate.method === "thread/goal/set",
    );
    expect(revisions.at(-1)?.params).toMatchObject({
      expectedGoalId: "goal-desktop",
      objective: "Ship the revised implementation and verify it.",
    });

    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    await waitFor(() => {
      expect(screen.getByText(/Paused/)).toBeTruthy();
    });
    expect(
      runtime.requests.find(
        (candidate) => candidate.method === "thread/goal/pause",
      )?.params,
    ).toMatchObject({
      threadId: thread.id,
      expectedGoalId: "goal-desktop",
    });
  });

  it("edits and resumes a complete Goal without changing its identity", async () => {
    const completedGoal: Goal = {
      id: "goal-completed",
      threadId: thread.id,
      objective: "Ship version one",
      status: "complete",
      tokenBudget: null,
      tokensUsed: 42,
      timeUsedSeconds: 8,
      skillIds: [],
      createdAt: "2026-07-16T02:00:00Z",
      updatedAt: "2026-07-16T02:01:00Z",
    };
    const decidingTurn: Turn = {
      ...turn,
      goalId: completedGoal.id,
    };
    const evidenceItem: Item = {
      id: "item-goal-evidence",
      threadId: thread.id,
      turnId: decidingTurn.id,
      ordinal: 2,
      kind: "test_result",
      status: "completed",
      summary: "Focused tests passed",
      payload: { command: "pytest -q", exitCode: 0 },
      createdAt: "2026-07-16T02:00:30Z",
      updatedAt: "2026-07-16T02:00:31Z",
    };
    const goalEvents: Event[] = [
      {
        eventId: "event-goal-turn",
        sequence: 1,
        type: "turn.completed",
        threadId: thread.id,
        turnId: decidingTurn.id,
        itemId: null,
        timestamp: decidingTurn.completedAt ?? "2026-07-16T02:00:31Z",
        payload: { turn: decidingTurn as unknown as JsonValue },
      },
      {
        eventId: "event-goal-evidence",
        sequence: 2,
        type: "item.created",
        threadId: thread.id,
        turnId: decidingTurn.id,
        itemId: evidenceItem.id,
        timestamp: evidenceItem.updatedAt,
        payload: { item: evidenceItem as unknown as JsonValue },
      },
    ];
    const goalOutcome: GoalOutcome = {
      status: "complete",
      reason: "The requested change is implemented and focused tests pass.",
      source: "agent",
      decidedByTurnId: decidingTurn.id,
      decidedAt: "2026-07-16T02:00:31Z",
      evidenceRefs: [
        {
          itemId: evidenceItem.id,
          turnId: decidingTurn.id,
          kind: evidenceItem.kind,
          status: evidenceItem.status,
          summary: evidenceItem.summary,
        },
      ],
    };
    const runtime = new TestRuntime(
      [project],
      [thread],
      goalEvents,
      [],
      null,
      completedGoal,
      goalOutcome,
    );
    render(<App runtime={runtime} />);

    await screen.findByText("Complete");
    fireEvent.click(screen.getByText("Ship version one").closest("button")!);
    expect(
      screen.getByText(
        "The requested change is implemented and focused tests pass.",
      ),
    ).toBeTruthy();
    expect(screen.getByText(/Deciding Turn/)).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Focused tests passed" }),
    );
    expect(
      await screen.findByRole("heading", { name: "Focused tests passed" }),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Edit & reopen" }));
    const outcome = screen.getByLabelText("Outcome") as HTMLTextAreaElement;
    expect(outcome.value).toBe("Ship version one");
    fireEvent.change(outcome, {
      target: { value: "Ship version two" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save & resume" }));

    await waitFor(() =>
      expect(screen.getByText("Ship version two")).toBeTruthy(),
    );
    expect(
      runtime.requests.find(
        (candidate) =>
          candidate.method === "thread/goal/set" &&
          (candidate.params as MethodParams["thread/goal/set"])
            .expectedGoalId === "goal-completed",
      )?.params,
    ).toMatchObject({
      objective: "Ship version two",
      expectedGoalId: "goal-completed",
    });
    expect(
      runtime.requests.find(
        (candidate) => candidate.method === "thread/goal/resume",
      )?.params,
    ).toMatchObject({ expectedGoalId: "goal-completed" });
  });

  it("opens MCP management without reviving the dormant Hooks surface", async () => {
    const runtime = new TestRuntime([project], [thread], []);
    render(<App runtime={runtime} />);

    await screen.findByRole("heading", { name: "Recovered task" });
    fireEvent.click(screen.getByRole("button", { name: "MCP" }));
    await screen.findByRole("heading", { name: "MCP Servers" });
    expect(runtime.calls).toContain("mcp/list");

    fireEvent.click(screen.getByRole("button", { name: "Skills" }));
    await screen.findByRole("heading", { name: "Skills" });
    expect(screen.queryByRole("tab", { name: /Hooks/ })).toBeNull();
    expect(runtime.calls).not.toContain("hooks/list");
  });

  it("saves the default agent preset for new Sessions from Settings", async () => {
    const runtime = new TestRuntime([project], [thread], []);
    render(<App runtime={runtime} />);

    await screen.findByRole("heading", { name: "Recovered task" });
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    const dialog = await screen.findByRole("dialog", { name: "Settings" });
    const picker = await within(dialog).findByRole("combobox", {
      name: "Default for new Sessions",
    });
    await within(dialog).findByRole("option", {
      name: "Code reader [system]",
    });
    fireEvent.change(picker, { target: { value: "code-reader" } });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save preset default" }),
    );

    await waitFor(() =>
      expect(
        runtime.requests.find(
          (request) =>
            request.method === "settings/update" &&
            (request.params as MethodParams["settings/update"]).patch.agents !==
              undefined,
        )?.params,
      ).toMatchObject({
        patch: { agents: { defaults: { defaultPreset: "code-reader" } } },
      }),
    );
  });

  it("switches the appearance mode from the tri-cards", async () => {
    const runtime = new TestRuntime([project], [thread], []);
    render(<App runtime={runtime} />);

    await screen.findByRole("heading", { name: "Recovered task" });
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    const dialog = await screen.findByRole("dialog", { name: "Settings" });
    const dark = within(dialog).getByRole("button", { name: "Dark" });
    expect(dark.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(dark);
    expect(dark.getAttribute("aria-pressed")).toBe("true");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    fireEvent.click(within(dialog).getByRole("button", { name: "System" }));
    expect(document.documentElement.getAttribute("data-theme")).toBeNull();
  });

  it("lets plain Enter queue while busy when the preference says queue", async () => {
    localStorage.setItem(
      "deepcode.desktop.composer.v1",
      JSON.stringify({ busyEnter: "queue" }),
    );
    __resetComposerBehaviorForTests();
    const runningThread = { ...thread, status: "running" as const };
    const runtime = new TestRuntime([project], [runningThread], runningEvents);
    render(<App runtime={runtime} />);

    const composer = await screen.findByRole("textbox", {
      name: "Task instruction",
    });
    fireEvent.change(composer, { target: { value: "run later" } });
    fireEvent.keyDown(composer, { key: "Enter" });
    await waitFor(() => expect(runtime.calls).toContain("turn/enqueue"));
    expect(runtime.calls).not.toContain("turn/steer");

    // Cmd/Ctrl+Enter performs the other verb: steer.
    fireEvent.change(composer, { target: { value: "steer now" } });
    fireEvent.keyDown(composer, { key: "Enter", metaKey: true });
    await waitFor(() => expect(runtime.calls).toContain("turn/steer"));
  });

  it("opens Settings as a sectioned dialog and closes it again", async () => {
    const runtime = new TestRuntime([project], [thread], []);
    render(<App runtime={runtime} />);

    await screen.findByRole("heading", { name: "Recovered task" });
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));

    const dialog = await screen.findByRole("dialog", { name: "Settings" });
    const nav = within(dialog).getByRole("navigation", {
      name: "Settings sections",
    });
    expect(
      within(nav)
        .getAllByRole("button")
        .map((button) => button.textContent),
    ).toEqual(["General", "Models", "Plugins", "Agent presets"]);

    // General is active by default and carries the permission card.
    expect(
      within(dialog).getByRole("combobox", { name: "Default Session access" }),
    ).toBeTruthy();

    // Sections switch without leaving the dialog.
    fireEvent.click(within(nav).getByRole("button", { name: "Agent presets" }));
    expect(
      await within(dialog).findByRole("heading", { name: "Agent presets" }),
    ).toBeTruthy();

    fireEvent.click(
      within(dialog).getByRole("button", { name: "Close settings" }),
    );
    expect(screen.queryByRole("dialog", { name: "Settings" })).toBeNull();
  });

  it("opens the configuration file from the dialog header", async () => {
    const runtime = new TestRuntime([project], [thread], []);
    render(<App runtime={runtime} />);

    await screen.findByRole("heading", { name: "Recovered task" });
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    const dialog = await screen.findByRole("dialog", { name: "Settings" });
    const openButton = within(dialog).getByRole("button", {
      name: "Open configuration file",
    });
    await waitFor(() =>
      expect((openButton as HTMLButtonElement).disabled).toBe(false),
    );
    fireEvent.click(openButton);
    await waitFor(() =>
      expect(runtime.openedPaths).toEqual(["/tmp/deepcode_config.json"]),
    );
  });

  it("loads effective Settings and sanitized diagnostics for the selected project", async () => {
    const runtime = new TestRuntime([project], [thread], []);
    render(<App runtime={runtime} />);

    await screen.findByRole("heading", { name: "Recovered task" });
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));

    await screen.findByRole("heading", { name: "Settings" });
    expect(
      await screen.findByText("SQLite integrity check passed"),
    ).toBeTruthy();
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

  it("saves one coherent product access default without rewriting sandbox compatibility", async () => {
    const runtime = new TestRuntime([project], [thread], []);
    render(<App runtime={runtime} />);

    await screen.findByRole("heading", { name: "Recovered task" });
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    const access = await screen.findByRole("combobox", {
      name: "Default Session access",
    });
    expect((access as HTMLSelectElement).value).toBe("");
    expect(
      screen.getByRole("option", { name: "Use resolved fallback · Ask" }),
    ).toBeTruthy();
    expect(screen.queryByLabelText("Enable command sandbox")).toBeNull();

    fireEvent.change(access, { target: { value: "read_only" } });
    fireEvent.click(
      screen.getByRole("button", { name: "Save safety settings" }),
    );

    await waitFor(() =>
      expect(
        runtime.requests.find(
          (request) =>
            request.method === "settings/update" &&
            (request.params as MethodParams["settings/update"]).patch
              .security !== undefined,
        )?.params,
      ).toMatchObject({
        patch: { security: { accessPreset: "read_only" } },
      }),
    );
    const securityPatch = (
      runtime.requests.find(
        (request) =>
          request.method === "settings/update" &&
          (request.params as MethodParams["settings/update"]).patch.security !==
            undefined,
      )?.params as MethodParams["settings/update"]
    ).patch.security;
    expect(securityPatch).toEqual({ accessPreset: "read_only" });
  });

  it("requires acknowledgement for a Full access Settings default", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const runtime = new TestRuntime([project], [thread], []);
    render(<App runtime={runtime} />);

    await screen.findByRole("heading", { name: "Recovered task" });
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    const access = await screen.findByRole("combobox", {
      name: "Default Session access",
    });
    fireEvent.change(access, { target: { value: "full_access" } });
    fireEvent.click(
      screen.getByRole("button", { name: "Save safety settings" }),
    );
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
    expect(
      runtime.requests.some(
        (request) =>
          request.method === "settings/update" &&
          (request.params as MethodParams["settings/update"]).patch.security !==
            undefined,
      ),
    ).toBe(false);

    confirm.mockReturnValue(true);
    fireEvent.click(
      screen.getByRole("button", { name: "Save safety settings" }),
    );
    await waitFor(() =>
      expect(
        runtime.requests.find(
          (request) =>
            request.method === "settings/update" &&
            (request.params as MethodParams["settings/update"]).patch
              .security !== undefined,
        )?.params,
      ).toMatchObject({
        patch: { security: { accessPreset: "full_access" } },
        scope: "user",
        riskAcknowledged: true,
      }),
    );
  });

  it("edits the selected Settings scope and can restore project inheritance", async () => {
    const runtime = new TestRuntime([project], [thread], []);
    render(<App runtime={runtime} />);

    await screen.findByRole("heading", { name: "Recovered task" });
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    const scope = await screen.findByRole("combobox", {
      name: "Write to",
    });
    fireEvent.change(scope, { target: { value: "project" } });
    const access = screen.getByRole("combobox", {
      name: "Default Session access",
    });
    expect((access as HTMLSelectElement).value).toBe("");
    expect(
      screen.getByRole("option", { name: "Inherit user default" }),
    ).toBeTruthy();

    fireEvent.change(access, { target: { value: "read_only" } });
    fireEvent.click(
      screen.getByRole("button", { name: "Save safety settings" }),
    );
    await waitFor(() =>
      expect((access as HTMLSelectElement).value).toBe("read_only"),
    );

    fireEvent.change(access, { target: { value: "" } });
    fireEvent.click(
      screen.getByRole("button", { name: "Save safety settings" }),
    );
    await waitFor(() => {
      const updates = runtime.requests.filter(
        (request) => request.method === "settings/update",
      );
      expect(updates.at(-1)?.params).toMatchObject({
        patch: { security: { accessPreset: null } },
        scope: "project",
      });
    });
  });

  it("configures one shared credential-safe LLM connection from Settings", async () => {
    const runtime = new TestRuntime([project], [thread], []);
    render(<App runtime={runtime} />);

    await screen.findByRole("heading", { name: "Recovered task" });
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    fireEvent.click(await screen.findByRole("button", { name: "Models" }));
    await screen.findByRole("heading", { name: "AI providers" });
    fireEvent.change(
      await screen.findByRole("combobox", { name: "Add provider" }),
      { target: { value: "openrouter" } },
    );

    fireEvent.change(screen.getByLabelText("Display name"), {
      target: { value: "Router Desktop" },
    });
    fireEvent.change(screen.getByLabelText("API key"), {
      target: { value: "desktop-test-secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save and check" }));

    const connectionName = await screen.findByText("Router Desktop", {
      selector: "strong",
    });
    const card = connectionName.closest("article");
    expect(card).toBeTruthy();
    const request = runtime.requests.find(
      (candidate) => candidate.method === "provider/upsert",
    )?.params as MethodParams["provider/upsert"];
    expect(request.connection).toMatchObject({
      id: "openrouter",
      label: "Router Desktop",
      template: "openrouter",
      apiKey: "desktop-test-secret",
    });

    // Saving runs the staged check automatically; its result lands on the
    // row card without a separate Check action.
    expect(
      await within(card as HTMLElement).findByText("Credential"),
    ).toBeTruthy();
    expect(within(card as HTMLElement).getByText("Model catalog")).toBeTruthy();

    fireEvent.click(
      within(card as HTMLElement).getByRole("button", { name: "Edit" }),
    );
    expect((screen.getByLabelText("API key") as HTMLInputElement).value).toBe(
      "",
    );
    fireEvent.click(screen.getByLabelText("Remove saved API key"));
    fireEvent.click(screen.getByRole("button", { name: "Save and check" }));
    await waitFor(() => {
      expect(
        runtime.requests.filter(
          (candidate) => candidate.method === "provider/upsert",
        ),
      ).toHaveLength(2);
    });
    const clearRequest = runtime.requests.filter(
      (candidate) => candidate.method === "provider/upsert",
    )[1].params as MethodParams["provider/upsert"];
    expect(clearRequest.connection.clearApiKey).toBe(true);
    expect(clearRequest.connection.apiKey).toBeUndefined();
  });

  it("warns when the launch environment shadows a pasted API key", async () => {
    const runtime = new TestRuntime([project], [thread], []);
    render(<App runtime={runtime} />);

    await screen.findByRole("heading", { name: "Recovered task" });
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    fireEvent.click(await screen.findByRole("button", { name: "Models" }));
    await screen.findByRole("heading", { name: "AI providers" });

    // The stubbed openai connection resolves its key from the environment.
    const connectionName = await screen.findByText("OpenAI", {
      selector: "strong",
    });
    const card = connectionName.closest("article");
    fireEvent.click(
      within(card as HTMLElement).getByRole("button", { name: "Edit" }),
    );
    expect(
      screen.getByText(/OPENAI_API_KEY currently provides this key/),
    ).toBeTruthy();
    // The dsh posture: an environment-provided key locks the field instead
    // of accepting a paste that cannot take effect.
    const keyInput = screen.getByLabelText("API key") as HTMLInputElement;
    expect(keyInput.disabled).toBe(true);
    expect(keyInput.placeholder).toMatch(/launch environment/);
  });

  it("offers the provider directory through the Add provider dropdown", async () => {
    const runtime = new TestRuntime([project], [thread], []);
    render(<App runtime={runtime} />);

    await screen.findByRole("heading", { name: "Recovered task" });
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    fireEvent.click(await screen.findByRole("button", { name: "Models" }));
    await screen.findByRole("heading", { name: "AI providers" });

    const picker = (await screen.findByRole("combobox", {
      name: "Add provider",
    })) as HTMLSelectElement;
    const labels = Array.from(picker.options).map((option) => option.text);
    expect(labels.some((label) => label.includes("Anthropic"))).toBe(true);
    fireEvent.change(picker, { target: { value: "anthropic" } });
    // One pick opens the editor prefilled from the template.
    expect(
      await screen.findByRole("dialog", { name: /Connect a provider/ }),
    ).toBeTruthy();
    expect(
      (screen.getByLabelText("Display name") as HTMLInputElement).placeholder,
    ).toBe("Anthropic");
  });

  it("fetches a connection's live models and adopts picks into its manual list", async () => {
    const runtime = new TestRuntime([project], [thread], []);
    render(<App runtime={runtime} />);

    await screen.findByRole("heading", { name: "Recovered task" });
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    fireEvent.click(await screen.findByRole("button", { name: "Models" }));
    await screen.findByRole("heading", { name: "AI providers" });
    const connectionName = await screen.findByText("OpenAI", {
      selector: "strong",
    });
    const card = connectionName.closest("article");
    fireEvent.click(
      within(card as HTMLElement).getByRole("button", { name: "Edit" }),
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Fetch models from provider" }),
    );
    const discovered = await screen.findByRole("list", {
      name: "Discovered models",
    });
    const option = within(discovered).getByText("gpt-5-mini", {
      selector: "strong",
    });
    fireEvent.click(option.closest("label") as HTMLElement);
    fireEvent.click(
      screen.getByRole("button", { name: /Add 1 model to this connection/ }),
    );
    // Adoption switches the connection to a manual list (the picked models
    // REPLACE the provider catalog), visible in the summary line.
    expect(screen.getByText(/Manual list: 1 model/)).toBeTruthy();
    expect(runtime.calls).toContain("model/list");
  });

  it("saves and verifies the selected Agent model with the project context", async () => {
    const runtime = new TestRuntime([project], [thread], []);
    render(<App runtime={runtime} />);

    await screen.findByRole("heading", { name: "Recovered task" });
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    fireEvent.click(await screen.findByRole("button", { name: "Models" }));
    const providersHeading = await screen.findByRole("heading", {
      name: "AI providers",
    });
    const modelHeading = screen.getByRole("heading", { name: "Agent model" });
    expect(
      providersHeading.compareDocumentPosition(modelHeading) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    const verifyModel = screen.getByRole("button", {
      name: "Save and verify model",
    }) as HTMLButtonElement;
    await waitFor(() => expect(verifyModel.disabled).toBe(false));
    fireEvent.click(verifyModel);

    await waitFor(() => {
      const request = runtime.requests.find(
        (candidate) => candidate.method === "provider/test",
      );
      expect(request?.params).toEqual({
        connectionId: "openai",
        projectId: project.id,
        model: "gpt-5",
      });
    });
    expect(await screen.findByText("Ready for agent work")).toBeTruthy();
    const methods = runtime.requests.map((request) => request.method);
    expect(methods.indexOf("settings/update")).toBeLessThan(
      methods.indexOf("provider/test"),
    );
  });

  it("offers the default model's published effort ladder and saves the pick", async () => {
    const runtime = new TestRuntime([project], [thread], []);
    render(<App runtime={runtime} />);

    await screen.findByRole("heading", { name: "Recovered task" });
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    fireEvent.click(await screen.findByRole("button", { name: "Models" }));
    const effort = (await screen.findByRole("combobox", {
      name: "Reasoning effort",
    })) as HTMLSelectElement;

    // Adapter-owned options: auto/off plus the model's published levels.
    await waitFor(() => {
      const values = Array.from(effort.options).map((option) => option.value);
      expect(values).toEqual(["", "auto", "none", "low", "medium", "high"]);
    });

    fireEvent.change(effort, { target: { value: "high" } });
    fireEvent.click(screen.getByRole("button", { name: "Save defaults" }));

    await waitFor(() =>
      expect(
        runtime.requests.find(
          (request) =>
            request.method === "settings/update" &&
            (request.params as MethodParams["settings/update"]).patch.agents !==
              undefined,
        )?.params,
      ).toMatchObject({
        patch: {
          agents: { defaults: { model: "gpt-5", reasoningEffort: "high" } },
        },
      }),
    );
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
    fireEvent.click(screen.getByRole("button", { name: "Check for updates" }));

    expect(await screen.findByText(/DeepCode 0.2.0 is available/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Install 0.2.0" }));
    await waitFor(() => expect(runtime.updateInstallCount).toBe(1));
  });

  it("restores a thread and its final durable item from event replay", async () => {
    const runtime = new TestRuntime([project], [thread], recoveryEvents);
    render(<App runtime={runtime} />);

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: "Recovered task" }),
      ).toBeTruthy();
      expect(screen.getByText("Recovered final answer")).toBeTruthy();
    });
    expect(runtime.calls.slice(0, 6)).toEqual([
      "project/list",
      "thread/list",
      "thread/resume",
      "event/replay",
      "thread/goal/get",
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
    const runtime = new TestRuntime([recoveredProject], [recoveredThread], []);
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
      (
        screen.getByRole("textbox", {
          name: "Task instruction",
        }) as HTMLTextAreaElement
      ).disabled,
    ).toBe(true);
    expect(
      (
        screen.getByRole("button", {
          name: /New thread/,
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(screen.queryByText("Trusted")).toBeNull();
  });

  it("keeps a restored untrusted Session editable and runs only after trust", async () => {
    const discoveredProject: Project = {
      ...project,
      id: "project-discovered",
      canonicalPath: "/workspace/existing-cli-project",
      displayName: "Existing CLI project",
      trustState: "untrusted",
      settings: { sessionDiscovered: true },
    };
    const restoredThread: Thread = {
      ...thread,
      id: "session-restored",
      projectId: discoveredProject.id,
      workspacePath: discoveredProject.canonicalPath,
      title: "Existing CLI Session",
    };
    const runtime = new TestRuntime([discoveredProject], [restoredThread], []);
    render(<App runtime={runtime} />);

    const composer = await screen.findByRole("textbox", {
      name: "Task instruction",
    });
    expect((composer as HTMLTextAreaElement).disabled).toBe(false);
    expect(
      screen.getByText("Trust this folder before agent execution."),
    ).toBeTruthy();

    fireEvent.change(composer, {
      target: { value: "Continue this existing Session" },
    });
    fireEvent.keyDown(composer, { key: "Enter" });
    expect(runtime.calls).not.toContain("turn/start");
    expect((composer as HTMLTextAreaElement).value).toBe(
      "Continue this existing Session",
    );

    fireEvent.click(screen.getByRole("button", { name: "Trust folder" }));
    await waitFor(() => expect(runtime.calls).toContain("project/update"));
    fireEvent.keyDown(composer, { key: "Enter" });
    await waitFor(() => expect(runtime.calls).toContain("turn/start"));
  });

  it("reveals Session rows through project disclosure instead of expanding every project", async () => {
    const secondProject: Project = {
      ...project,
      id: "project-collapsed",
      canonicalPath: "/workspace/collapsed",
      displayName: "Collapsed project",
    };
    const secondThread: Thread = {
      ...thread,
      id: "thread-collapsed",
      projectId: secondProject.id,
      title: "Hidden until expanded",
      workspacePath: secondProject.canonicalPath,
    };
    const runtime = new TestRuntime(
      [project, secondProject],
      [thread, secondThread],
      [],
    );
    render(<App runtime={runtime} />);

    await screen.findByRole("heading", { name: "Recovered task" });
    expect(
      screen.getByRole("button", { name: "Open Session Recovered task" }),
    ).toBeTruthy();
    expect(
      screen.queryByRole("button", {
        name: "Open Session Hidden until expanded",
      }),
    ).toBeNull();

    const projectDisclosure = screen.getByRole("button", {
      name: "Collapsed project",
    });
    expect(projectDisclosure.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(projectDisclosure);

    await screen.findByRole("heading", { name: "Hidden until expanded" });
    expect(projectDisclosure.getAttribute("aria-expanded")).toBe("true");
    expect(
      screen.getByRole("button", {
        name: "Open Session Hidden until expanded",
      }),
    ).toBeTruthy();

    fireEvent.click(projectDisclosure);
    expect(projectDisclosure.getAttribute("aria-expanded")).toBe("false");
    expect(
      screen.queryByRole("button", {
        name: "Open Session Hidden until expanded",
      }),
    ).toBeNull();
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
      expect(
        screen.getByRole("heading", { name: "Recovered task" }),
      ).toBeTruthy(),
    );
    fireEvent.change(screen.getByPlaceholderText("Search Sessions"), {
      target: { value: "Cross-project" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Open Session Cross-project task" }),
    );

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: "Cross-project task" }),
      ).toBeTruthy();
      expect(screen.getAllByText("Another repo").length).toBeGreaterThanOrEqual(
        2,
      );
    });
  });

  it("renames and archives the canonical Session through the App Server", async () => {
    const runtime = new TestRuntime([project], [thread], recoveryEvents);
    render(<App runtime={runtime} />);

    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "Recovered task" }),
      ).toBeTruthy(),
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
    fireEvent.click(screen.getByRole("button", { name: "Archive" }));

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

  it("permanently deletes a Session only after explicit destructive confirmation", async () => {
    const runtime = new TestRuntime([project], [thread], recoveryEvents);
    render(<App runtime={runtime} />);

    await screen.findByRole("heading", { name: "Recovered task" });
    fireEvent.click(
      screen.getByRole("button", {
        name: "Session actions for Recovered task",
      }),
    );
    fireEvent.click(
      screen.getByRole("menuitem", { name: "Delete permanently" }),
    );

    const dialog = screen.getByRole("alertdialog", {
      name: "Delete Recovered task",
    });
    expect(
      within(dialog).getByText("Workspace files stay untouched.", {
        exact: false,
      }),
    ).toBeTruthy();
    expect(runtime.calls).not.toContain("thread/delete");

    fireEvent.click(
      within(dialog).getByRole("button", { name: "Delete permanently" }),
    );

    await waitFor(() => expect(runtime.calls).toContain("thread/delete"));
    expect(
      screen.queryByRole("button", { name: "Open Session Recovered task" }),
    ).toBeNull();
    expect(
      runtime.requests.find((request) => request.method === "thread/delete")
        ?.params,
    ).toEqual({ threadId: thread.id });
  });

  it("retries a turn that was interrupted by App Server recovery", async () => {
    const runtime = new TestRuntime([project], [thread], failedRecoveryEvents);
    render(<App runtime={runtime} />);

    const retry = await screen.findByRole("button", { name: "Retry" });
    expect(
      screen.getByText(
        "The previous process stopped. Retry from the same prompt.",
      ),
    ).toBeTruthy();
    fireEvent.click(retry);

    await waitFor(() => expect(runtime.calls).toContain("turn/retry"));
    expect(
      runtime.requests.find((request) => request.method === "turn/retry")
        ?.params,
    ).toEqual({
      turnId: failedTurn.id,
      useCurrentSelection: false,
    });
  });

  it("presents legacy Turn items in their durable ordinal order", async () => {
    const runtime = new TestRuntime([project], [thread], presentationEvents);
    render(<App runtime={runtime} />);

    const answer = await screen.findByRole("heading", {
      name: "Repository findings",
    });
    const plan = screen.getByText("Execution plan", { selector: "strong" });
    const tool = screen.getByText("Ran the focused tests");

    expect(screen.getByText("Worked for 2s")).toBeTruthy();
    expect(
      plan.compareDocumentPosition(tool) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      tool.compareDocumentPosition(answer) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.getByText("Inspect files")).toBeTruthy();
    expect(screen.queryByText("Turn complete")).toBeNull();
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
    expect(screen.getByText("Started a new Turn.")).toBeTruthy();
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
        value:
          "Implement durable model selection\nKeep CLI behavior unchanged.",
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

  it("applies real per-Session model and access settings", async () => {
    const runtime = new TestRuntime([project], [thread], []);
    render(<App runtime={runtime} />);

    const model = await screen.findByRole("button", { name: "Session model" });
    const permissions = screen.getByRole("combobox", {
      name: "New submissions access",
    });
    expect((permissions as HTMLSelectElement).value).toBe("");
    expect(screen.getByRole("option", { name: "Default · Ask" })).toBeTruthy();

    fireEvent.click(model);
    const option = await screen.findByRole("option", { name: /gpt-5-mini/ });
    fireEvent.click(option);
    const highEffort = screen.getByRole("radio", { name: "High" });
    fireEvent.click(highEffort);
    expect(highEffort.getAttribute("aria-checked")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => {
      expect(runtime.calls).toContain("thread/execution/update");
      expect(screen.getByText("gpt-5-mini")).toBeTruthy();
    });
    expect(
      runtime.requests.find(
        (request) => request.method === "thread/execution/update",
      )?.params,
    ).toEqual({
      threadId: thread.id,
      connectionId: "openai",
      model: "gpt-5-mini",
      reasoningEffort: "high",
    });

    fireEvent.change(permissions, { target: { value: "read_only" } });
    await waitFor(() => {
      expect((permissions as HTMLSelectElement).value).toBe("read_only");
      expect(runtime.calls).toContain("thread/permission/update");
    });
    expect(
      runtime.requests.find(
        (request) => request.method === "thread/permission/update",
      )?.params,
    ).toEqual({
      threadId: thread.id,
      accessPreset: "read_only",
    });
  });

  it("selects an agent preset for a blank Session and hides broken ones", async () => {
    const runtime = new TestRuntime([project], [thread], []);
    render(<App runtime={runtime} />);

    const trigger = await screen.findByRole("button", {
      name: "Agent preset",
    });
    expect(trigger.textContent).toContain("Default");
    fireEvent.click(trigger);
    expect(
      await screen.findByRole("option", { name: /Code reader/ }),
    ).toBeTruthy();
    // A broken preset cannot compose a session, so it is never offered.
    expect(screen.queryByRole("option", { name: /damaged/ })).toBeNull();

    fireEvent.click(screen.getByRole("option", { name: /Code reader/ }));
    await waitFor(() => {
      expect(runtime.calls).toContain("preset/select");
      expect(trigger.textContent).toContain("Code reader");
    });
    expect(
      runtime.requests.find((request) => request.method === "preset/select")
        ?.params,
    ).toEqual({
      threadId: thread.id,
      agentPreset: "code-reader",
    });
    expect((trigger as HTMLButtonElement).disabled).toBe(false);
  });

  it("requires confirmation before enabling persistent Full access", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const runtime = new TestRuntime([project], [thread], []);
    render(<App runtime={runtime} />);

    const permissions = await screen.findByRole("combobox", {
      name: "New submissions access",
    });
    fireEvent.change(permissions, { target: { value: "full_access" } });
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
    expect((permissions as HTMLSelectElement).value).toBe("");
    expect(permissions.closest("label")?.getAttribute("data-access")).toBe(
      "ask",
    );
    expect(runtime.calls).not.toContain("thread/permission/update");

    confirm.mockReturnValue(true);
    fireEvent.change(permissions, { target: { value: "full_access" } });
    await waitFor(() => {
      expect((permissions as HTMLSelectElement).value).toBe("full_access");
      expect(runtime.calls).toContain("thread/permission/update");
    });
    expect(permissions.closest("label")?.getAttribute("data-access")).toBe(
      "full_access",
    );
    expect(
      runtime.requests.find(
        (request) => request.method === "thread/permission/update",
      )?.params,
    ).toEqual({
      threadId: thread.id,
      accessPreset: "full_access",
      riskAcknowledged: true,
    });
  });

  it("keeps frozen Current and Queued access separate from new submissions", async () => {
    const executingTurn: Turn = {
      ...runningTurn,
      executionSecurityProfile: fullAccessSecurityProfile,
    };
    const queuedTurn: Turn = {
      ...turn,
      id: "turn-frozen-queued",
      ordinal: 2,
      prompt: "Run the queued verification",
      status: "queued",
      executionSecurityProfile: askSecurityProfile,
      startedAt: null,
      completedAt: null,
    };
    const events: Event[] = [executingTurn, queuedTurn].map(
      (candidate, index) => ({
        eventId: `event-frozen-access-${candidate.id}`,
        sequence: index + 1,
        type: "turn.updated",
        threadId: thread.id,
        turnId: candidate.id,
        itemId: null,
        timestamp: "2026-07-16T00:00:02Z",
        payload: { turn: candidate as unknown as JsonValue },
      }),
    );
    const runtime = new TestRuntime(
      [project],
      [{ ...thread, status: "running" }],
      events,
    );
    render(<App runtime={runtime} />);

    const currentAccess = await screen.findByLabelText(
      "Current Turn access: Full access",
    );
    const queuedAccess = screen.getByLabelText(
      "Queued Turn access: Ask",
    );
    expect(currentAccess.getAttribute("data-access")).toBe("full_access");
    expect(queuedAccess.getAttribute("data-access")).toBe("ask");
    expect(
      screen.getByText(
        "Active and queued Turns keep their frozen model and access.",
      ),
    ).toBeTruthy();

    const newSubmissions = screen.getByRole("combobox", {
      name: "New submissions access",
    });
    fireEvent.change(newSubmissions, { target: { value: "read_only" } });
    await waitFor(() =>
      expect((newSubmissions as HTMLSelectElement).value).toBe("read_only"),
    );

    expect(currentAccess.getAttribute("data-access")).toBe("full_access");
    expect(currentAccess.textContent).toContain(
      "Current Turn\u00b7Full access",
    );
    expect(queuedAccess.textContent).toContain(
      "Queued (1)\u00b7Ask",
    );
    expect(screen.queryByText("Model & access apply next Turn")).toBeNull();
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
      screen.getByText(
        "Only files inside this Session workspace can be attached.",
      ),
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

  it("attaches an opaque Skill selection to exactly one Desktop turn", async () => {
    const runtime = new TestRuntime([project], [thread], []);
    render(<App runtime={runtime} />);

    const skillButton = await screen.findByRole("button", {
      name: "Select Skills for this turn",
    });
    await waitFor(() =>
      expect((skillButton as HTMLButtonElement).disabled).toBe(false),
    );
    fireEvent.click(skillButton);
    fireEvent.click(await screen.findByRole("option", { name: /verify/i }));
    fireEvent.click(screen.getByRole("option", { name: /review/i }));

    expect(screen.getByRole("button", { name: "Remove review" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Remove verify" })).toBeTruthy();
    const composer = screen.getByRole("textbox", { name: "Task instruction" });
    fireEvent.change(composer, { target: { value: "Audit this patch" } });
    fireEvent.keyDown(composer, { key: "Enter" });

    await waitFor(() => expect(runtime.calls).toContain("turn/start"));
    const request = runtime.requests.find(
      (candidate) => candidate.method === "turn/start",
    )?.params as MethodParams["turn/start"];
    expect(request.skills).toEqual([VERIFY_SKILL_ID, SKILL_ID]);
    expect(screen.queryByRole("button", { name: "Remove review" })).toBeNull();
    expect(
      (await screen.findByLabelText("Skills used in this turn")).textContent,
    ).toContain("review");
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
      expect(
        screen.getByRole("complementary", { name: "Inspector" }),
      ).toBeTruthy(),
    );
    expect(runtime.calls).not.toContain("turn/start");
  });

  it("steers the active Turn instead of creating a competing Turn", async () => {
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

    await waitFor(() => expect(runtime.calls).toContain("turn/steer"));
    expect(
      screen.getByText("Update delivered to the active Turn."),
    ).toBeTruthy();
    expect(runtime.calls).not.toContain("turn/enqueue");
    const request = runtime.requests.find(
      (candidate) => candidate.method === "turn/steer",
    )?.params as MethodParams["turn/steer"];
    expect(request.expectedTurnId).toBe(runningTurn.id);
    expect(request.messageId).toMatch(/^desktop-/);
  });

  it("queues a next Turn only through the explicit Queue action", async () => {
    const runningThread = { ...thread, status: "running" as const };
    const runtime = new TestRuntime([project], [runningThread], runningEvents);
    render(<App runtime={runtime} />);

    const composer = await screen.findByRole("textbox", {
      name: "Task instruction",
    });
    fireEvent.change(composer, {
      target: { value: "Run this after the current work" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Queue next" }));

    await waitFor(() => expect(runtime.calls).toContain("turn/enqueue"));
    expect(screen.getByText("Queued for the next Turn.")).toBeTruthy();
    expect(runtime.calls).not.toContain("turn/steer");
    const request = runtime.requests.find(
      (candidate) => candidate.method === "turn/enqueue",
    )?.params as MethodParams["turn/enqueue"];
    expect(request.prompt).toBe("Run this after the current work");
    expect(request.messageId).toMatch(/^desktop-/);
  });

  it("restores a waiting Paper2Code review without using the agent composer", async () => {
    const view = render(
      <App
        runtime={new TestRuntime([project], [paperThread], workflowEvents)}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Review Implementation Plan")).toBeTruthy();
      expect(screen.getByText("65%")).toBeTruthy();
      expect(
        screen.getByRole("button", { name: "Approve & continue" }),
      ).toBeTruthy();
    });
    expect(view.container.querySelector("#turn-prompt")).toBeNull();
  });
});
