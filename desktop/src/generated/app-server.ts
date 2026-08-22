/* AUTO-GENERATED from protocol/app-server.schema.json. DO NOT EDIT. */

export type ClientSurface = "cli" | "desktop" | "headless" | "automation" | "app_server" | "internal";
export type TrustState = "untrusted" | "trusted";
export type JsonValue =
  | null
  | boolean
  | number
  | string
  | JsonValue[]
  | {
      [k: string]: JsonValue;
    };
export type ConfigScope = "user" | "project";
export type SkillReadParams = SkillReadParams1 & {
  projectId: string;
  skillId?: string;
  /**
   * Deprecated compatibility selector; use skillId.
   */
  name?: string;
};
export type SkillReadParams1 =
  | {
      skillId: string;
      [k: string]: unknown;
    }
  | {
      name: string;
      [k: string]: unknown;
    };
export type AutomationScheduleKind = "manual" | "interval";
/**
 * Controls interval scheduling. Manual Automations are always enabled.
 */
export type AutomationActivationStatus = "enabled" | "paused";
export type ThreadMode = "code" | "paper" | "brief" | "review" | "goal";
/**
 * User-facing tool access preset shared by Desktop and CLI.
 */
export type ExecutionAccessPreset = "ask" | "read_only" | "full_access" | "dangerous_skip";
export type ApprovalDecision = "approved_once" | "approved_session" | "denied";
export type ExecutionPermissionMode = "default" | "plan" | "full_auto";
export type AutomationStatus = "enabled" | "paused" | "retired";
export type AutomationTrigger = "manual" | "scheduled";
export type AutomationRunStatus =
  "queued" | "running" | "waiting" | "blocked" | "completed" | "failed" | "interrupted" | "skipped";
export type ThreadStatus = "idle" | "running" | "waiting" | "failed" | "archived";
export type TurnStatus = "queued" | "running" | "waiting_approval" | "completed" | "failed" | "interrupted";
export type GoalStatus = "active" | "paused" | "blocked" | "budget_limited" | "complete";
export type ItemKind =
  | "user_message"
  | "assistant_message"
  | "reasoning_summary"
  | "plan"
  | "tool_call"
  | "command_execution"
  | "file_change"
  | "diff"
  | "test_result"
  | "approval_request"
  | "workflow_stage"
  | "artifact"
  | "error"
  | "completion";
export type ItemStatus = "pending" | "in_progress" | "completed" | "failed" | "declined";
export type ApprovalStatus = "pending" | "approved_once" | "approved_session" | "denied" | "cancelled" | "expired";
export type WorkflowStatus = "queued" | "running" | "waiting" | "completed" | "failed" | "cancelled";
export type TurnPlanUpdatedEvent = Event & {
  payload: {
    plan: TurnPlan;
  };
  [k: string]: unknown;
};
export type PlanStepStatus = "pending" | "in_progress" | "completed";

/**
 * Canonical JSON-RPC data contracts for the DeepCode desktop client.
 */
export interface DeepCodeAppServerProtocol {
  methodParams: MethodParams;
  methodResults: MethodResults;
  notifications: Notifications;
}
export interface MethodParams {
  initialize: InitializeParams;
  shutdown: EmptyParams;
  "project/list": ProjectListParams;
  "project/add": ProjectAddParams;
  "project/read": ProjectReadParams;
  "project/update": ProjectUpdateParams;
  "project/remove": ProjectReadParams;
  "settings/read": OptionalProjectParams;
  "settings/update": SettingsUpdateParams;
  "provider/list": OptionalProjectParams;
  "provider/upsert": ProviderUpsertParams;
  "provider/remove": ConnectionIdentityParams;
  "provider/test": ProviderTestParams;
  "provider/discover": ProviderDiscoverParams;
  "model/list": ModelListParams;
  "preset/list": PresetListParams;
  "preset/current": PresetCurrentParams;
  "preset/select": PresetSelectParams;
  "skills/list": SkillListParams;
  "skill/read": SkillReadParams;
  "skills/import": SkillImportParams;
  "skills/set-enabled": SkillSetEnabledParams;
  "skills/delete": SkillIdentityParams;
  "skills/reload": ProjectReadParams;
  "plugins/list": EmptyParams;
  "plugins/add": PluginAddParams;
  "plugins/set-enabled": PluginSetEnabledParams;
  "plugins/remove": PluginIdentityParams;
  "hooks/list": ProjectReadParams;
  "mcp/list": OptionalProjectParams;
  "mcp/upsert": McpUpsertParams;
  "mcp/remove": McpRemoveParams;
  "mcp/presets": OptionalProjectParams;
  "mcp/preset/add": McpPresetAddParams;
  "mcp/set-enabled": McpSetEnabledParams;
  "mcp/probe": McpIdentityParams;
  "mcp/oauth/start": McpOAuthStartParams;
  "mcp/oauth/cancel": McpIdentityParams;
  "mcp/oauth/logout": McpIdentityParams;
  "diagnostics/read": OptionalProjectParams;
  "automation/list": AutomationListParams;
  "automation/create": AutomationCreateParams;
  "automation/update": AutomationUpdateParams;
  "automation/remove": AutomationIdentityParams;
  "automation/run": AutomationRunParams;
  "automation/runs": AutomationRunsParams;
  "thread/start": ThreadStartParams;
  "thread/list": ThreadListParams;
  "thread/read": ThreadReadParams;
  "thread/rename": ThreadRenameParams;
  "thread/model": ThreadModelParams;
  "thread/execution/update": ThreadExecutionParams;
  "thread/permission/update": ThreadPermissionUpdateParams;
  "thread/archive": ThreadReadParams;
  "thread/delete": ThreadReadParams;
  "thread/fork": ThreadForkParams;
  "thread/goal/get": ThreadReadParams;
  "thread/goal/set": GoalSetParams;
  "thread/goal/pause": GoalIdentityParams;
  "thread/goal/resume": GoalIdentityParams;
  "thread/goal/continue": GoalIdentityParams;
  "thread/goal/clear": GoalIdentityParams;
  "turn/start": TurnStartParams;
  "turn/enqueue": TurnStartParams;
  "turn/steer": TurnSteerParams;
  "turn/read": TurnReadParams;
  "turn/interrupt": TurnInterruptParams;
  "turn/retry": TurnRetryParams;
  "workflow/start": WorkflowStartParams;
  "workflow/read": WorkflowRunParams;
  "workflow/list": ThreadReadParams;
  "workflow/interrupt": WorkflowRunParams;
  "workflow/retry": WorkflowRunParams;
  "workflow/respond": WorkflowRespondParams;
  "artifact/list": ThreadReadParams;
  "artifact/read": ArtifactReadParams;
  "approval/respond": ApprovalRespondParams;
  "event/replay": EventReplayParams;
  "file/list": FileListParams;
  "file/read": FileReadParams;
  "file/write": FileWriteParams;
  "git/status": ThreadReadParams;
  "git/diff": GitDiffParams;
  "git/discard": GitDiscardParams;
  "git/worktree/create": ThreadReadParams;
  "git/worktree/remove": WorktreeRemoveParams;
  "terminal/create": TerminalCreateParams;
  "terminal/write": TerminalWriteParams;
  "terminal/resize": TerminalResizeParams;
  "terminal/close": TerminalIdentityParams;
  "test/discover": ThreadReadParams;
  "test/run": TestRunParams;
  "thread/resume": ThreadResumeParams;
}
export interface InitializeParams {
  protocolVersion: "1.0";
  clientInfo: ClientInfo;
}
export interface ClientInfo {
  name: string;
  version: string;
  surface?: ClientSurface;
}
export interface EmptyParams {}
export interface ProjectListParams {
  limit?: number;
  offset?: number;
}
export interface ProjectAddParams {
  path: string;
  displayName?: string;
  trustState?: TrustState;
}
export interface ProjectReadParams {
  projectId: string;
}
export interface ProjectUpdateParams {
  projectId: string;
  displayName?: string;
  trustState?: TrustState;
  settings?: JsonObject;
}
export interface JsonObject {
  [k: string]: JsonValue;
}
export interface OptionalProjectParams {
  projectId?: string;
}
export interface SettingsUpdateParams {
  patch: JsonObject;
  scope?: ConfigScope;
  projectId?: string;
  riskAcknowledged?: boolean;
  expectedRevision?: string;
}
export interface ProviderUpsertParams {
  expectedRevision?: string;
  connection: {
    id: string;
    label?: string;
    template?: string;
    adapter?: "openai_compat" | "anthropic" | null;
    apiBase?: string | null;
    apiKeyEnv?: string | null;
    apiKey?: string;
    clearApiKey?: boolean;
    extraHeaders?: JsonObject;
    modelCatalog?: "auto" | "openrouter" | "openai" | "anthropic" | "manual";
    manualModels?: (string | ManualModelEntry)[];
    enabled?: boolean;
  };
}
export interface ManualModelEntry {
  id: string;
  label?: string | null;
  contextWindow?: number | null;
  maxOutputTokens?: number | null;
  reasoningEfforts?: string[] | false | null;
}
export interface ConnectionIdentityParams {
  connectionId: string;
  expectedRevision?: string;
}
export interface ProviderTestParams {
  connectionId: string;
  projectId?: string;
  model?: string;
}
export interface ProviderDiscoverParams {
  connectionId?: string;
  template?: string;
  apiBase?: string;
  apiKey?: string;
  projectId?: string;
}
export interface ModelListParams {
  connectionId: string;
  projectId?: string;
  refresh?: boolean;
}
export interface PresetListParams {
  projectId: string;
}
export interface PresetCurrentParams {
  threadId: string;
}
export interface PresetSelectParams {
  threadId: string;
  agentPreset: string | null;
}
export interface SkillListParams {
  projectId: string;
  refresh?: boolean;
}
export interface SkillImportParams {
  projectId: string;
  path: string;
  scope: ConfigScope;
}
export interface SkillSetEnabledParams {
  projectId: string;
  skillId: string;
  enabled: boolean;
  scope: ConfigScope;
}
export interface SkillIdentityParams {
  projectId: string;
  skillId: string;
}
export interface PluginAddParams {
  path: string;
}
export interface PluginSetEnabledParams {
  pluginId: string;
  enabled: boolean;
}
export interface PluginIdentityParams {
  pluginId: string;
}
export interface McpUpsertParams {
  projectId?: string;
  scope: ConfigScope;
  name: string;
  server: JsonObject;
}
export interface McpRemoveParams {
  projectId?: string;
  scope: ConfigScope;
  name: string;
}
export interface McpPresetAddParams {
  projectId?: string;
  presetId: string;
  enabled?: boolean;
}
export interface McpSetEnabledParams {
  projectId?: string;
  name: string;
  enabled: boolean;
}
export interface McpIdentityParams {
  projectId?: string;
  name: string;
}
export interface McpOAuthStartParams {
  projectId?: string;
  name: string;
  openBrowser?: boolean;
  resetCredentials?: boolean;
}
export interface AutomationListParams {
  projectId?: string;
  limit?: number;
  offset?: number;
}
export interface AutomationCreateParams {
  projectId: string;
  name: string;
  prompt: string;
  scheduleKind: AutomationScheduleKind;
  intervalSeconds?: number;
  /**
   * Controls interval scheduling. Manual Automations must omit this field or set it to true.
   */
  enabled?: boolean;
}
export interface AutomationUpdateParams {
  automationId: string;
  name?: string;
  prompt?: string;
  status?: AutomationActivationStatus;
  scheduleKind?: AutomationScheduleKind;
  intervalSeconds?: number;
}
export interface AutomationIdentityParams {
  automationId: string;
}
export interface AutomationRunParams {
  automationId: string;
  requestId?: string;
}
export interface AutomationRunsParams {
  automationId: string;
  limit?: number;
  offset?: number;
}
export interface ThreadStartParams {
  projectId: string;
  title: string;
  mode?: ThreadMode;
  connectionId?: string;
  model?: string;
  reasoningEffort?: string;
  workspacePath?: string;
  parentThreadId?: string;
  agentPreset?: string;
}
export interface ThreadListParams {
  projectId?: string;
  includeArchived?: boolean;
  limit?: number;
  offset?: number;
  cwd?: string;
}
export interface ThreadReadParams {
  threadId: string;
}
export interface ThreadRenameParams {
  threadId: string;
  title: string;
}
export interface ThreadModelParams {
  threadId: string;
  model: string | null;
  connectionId?: string | null;
}
export interface ThreadExecutionParams {
  threadId: string;
  connectionId: string | null;
  model: string | null;
  reasoningEffort: string | null;
}
export interface ThreadPermissionUpdateParams {
  threadId: string;
  /**
   * Null clears the Session override and inherits Settings.
   */
  accessPreset: ExecutionAccessPreset | null;
  /**
   * Required true when selecting full_access.
   */
  riskAcknowledged?: boolean;
}
export interface ThreadForkParams {
  threadId: string;
  title?: string;
}
export interface GoalSetParams {
  threadId: string;
  objective?: string;
  tokenBudget?: number | null;
  /**
   * @maxItems 8
   */
  skills?:
    | []
    | [string]
    | [string, string]
    | [string, string, string]
    | [string, string, string, string]
    | [string, string, string, string, string]
    | [string, string, string, string, string, string]
    | [string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string];
  expectedGoalId?: string | null;
  start?: boolean;
}
export interface GoalIdentityParams {
  threadId: string;
  expectedGoalId: string;
}
export interface TurnStartParams {
  threadId: string;
  prompt: string;
  messageId: string;
  /**
   * @maxItems 8
   */
  skills?:
    | []
    | [string]
    | [string, string]
    | [string, string, string]
    | [string, string, string, string]
    | [string, string, string, string, string]
    | [string, string, string, string, string, string]
    | [string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string];
  connectionId?: string;
  model?: string;
  reasoningEffort?: string;
}
export interface TurnSteerParams {
  threadId: string;
  expectedTurnId: string;
  prompt: string;
  messageId: string;
}
export interface TurnReadParams {
  turnId: string;
}
export interface TurnInterruptParams {
  threadId: string;
  turnId: string;
}
export interface TurnRetryParams {
  turnId: string;
  useCurrentSelection?: boolean;
}
export interface WorkflowStartParams {
  threadId: string;
  kind: "paper2code";
  sourceType: "local" | "url" | "repository" | "requirement";
  source: string;
  options?: {
    enableIndexing?: boolean;
    planReview?: boolean;
  };
}
export interface WorkflowRunParams {
  workflowRunId: string;
}
export interface WorkflowRespondParams {
  workflowRunId: string;
  interactionId: string;
  response: JsonObject;
}
export interface ArtifactReadParams {
  artifactId: string;
  maxBytes?: number;
}
export interface ApprovalRespondParams {
  approvalId: string;
  decision: ApprovalDecision;
  message?: string;
}
export interface EventReplayParams {
  threadId: string;
  after?: number;
  limit?: number;
}
export interface FileListParams {
  threadId: string;
  path?: string;
  depth?: number;
  limit?: number;
}
export interface FileReadParams {
  threadId: string;
  path: string;
  maxBytes?: number;
}
export interface FileWriteParams {
  threadId: string;
  path: string;
  content: string;
  expectedSha256: string | null;
}
export interface GitDiffParams {
  threadId: string;
  scope?: "all" | "staged" | "working";
  path?: string;
}
export interface GitDiscardParams {
  threadId: string;
  path: string;
  expectedRevision: string;
}
export interface WorktreeRemoveParams {
  threadId: string;
  disposition: "keep" | "clean";
  force?: boolean;
  deleteBranch?: boolean;
}
export interface TerminalCreateParams {
  threadId: string;
  columns?: number;
  rows?: number;
}
export interface TerminalWriteParams {
  threadId: string;
  terminalId: string;
  data: string;
}
export interface TerminalResizeParams {
  threadId: string;
  terminalId: string;
  columns: number;
  rows: number;
}
export interface TerminalIdentityParams {
  threadId: string;
  terminalId: string;
}
export interface TestRunParams {
  threadId: string;
  turnId: string;
  commandId: string;
  timeoutSeconds?: number;
}
export interface ThreadResumeParams {
  sessionId: string;
  workspacePath?: string;
}
export interface MethodResults {
  initialize: InitializeResult;
  shutdown: {
    accepted: boolean;
  };
  "provider/list": ConnectionCatalogResult;
  "provider/upsert": ConnectionCatalogResult;
  "provider/remove": ConnectionRemoveResult;
  "provider/test": ProviderTestResult;
  "provider/discover": ProviderDiscoverResult;
  "model/list": ModelCatalogResult;
  "project/list": {
    projects: Project[];
  };
  "project/add": {
    project: Project;
  };
  "project/read": {
    project: Project;
  };
  "project/update": {
    project: Project;
  };
  "project/remove": {
    removed: boolean;
  };
  "settings/read": {
    settings: SettingsSnapshot;
  };
  "settings/update": {
    settings: SettingsSnapshot;
  };
  "preset/list": PresetListResult;
  "preset/current": PresetCurrentResult;
  "preset/select": PresetSelectResult;
  "skills/list": SkillCatalogResult;
  "skill/read": {
    skill: SkillDetail;
  };
  "skills/import": {
    skill: SkillDetail;
  };
  "skills/set-enabled": SkillCatalogResult;
  "skills/delete": {
    removed: boolean;
  };
  "skills/reload": SkillCatalogResult;
  "plugins/list": PluginCatalogResult;
  "plugins/add": PluginCatalogResult;
  "plugins/set-enabled": PluginCatalogResult;
  "plugins/remove": {
    removed: boolean;
    plugin: PluginInfo;
  };
  "hooks/list": {
    hooks: HookInfo[];
    warnings: string[];
    truncated: boolean;
  };
  "mcp/list": McpInventory;
  "mcp/upsert": McpInventory;
  "mcp/remove": McpInventory;
  "mcp/presets": McpPresetInventory;
  "mcp/preset/add": McpInventory;
  "mcp/set-enabled": McpInventory;
  "mcp/probe": McpProbeResult;
  "mcp/oauth/start": McpOAuthFlow;
  "mcp/oauth/cancel": {
    cancelled: boolean;
  };
  "mcp/oauth/logout": {
    removed: boolean;
  };
  "diagnostics/read": {
    diagnostics: DiagnosticsSnapshot;
  };
  "automation/list": {
    automations: Automation[];
    latestRuns: AutomationRun[];
    schedulerActive: boolean;
    executionMode: "requires_live_runtime";
    hasMore: boolean;
    nextOffset: number | null;
  };
  "automation/create": {
    automation: Automation;
    thread: Thread;
  };
  "automation/update": {
    automation: Automation;
  };
  "automation/remove": {
    removed: boolean;
  };
  "automation/run": {
    run: AutomationRun;
    turn: Turn | null;
  };
  "automation/runs": {
    runs: AutomationRun[];
    hasMore: boolean;
    nextOffset: number | null;
  };
  "thread/start": {
    thread: Thread;
  };
  "thread/list": {
    threads: Thread[];
  };
  "thread/read": {
    thread: Thread;
  };
  "thread/rename": {
    thread: Thread;
  };
  "thread/model": {
    thread: Thread;
  };
  "thread/execution/update": {
    thread: Thread;
  };
  "thread/permission/update": {
    thread: Thread;
  };
  "thread/archive": {
    thread: Thread;
  };
  "thread/delete": {
    threadId: string;
    cleanupPending: boolean;
  };
  "thread/fork": {
    thread: Thread;
  };
  "thread/goal/get": GoalResult;
  "thread/goal/set": GoalResult;
  "thread/goal/pause": GoalResult;
  "thread/goal/resume": GoalResult;
  "thread/goal/continue": GoalContinueResult;
  "thread/goal/clear": GoalResult;
  "turn/start": TurnSnapshotResult;
  "turn/enqueue": TurnSnapshotResult;
  "turn/steer": TurnSteerResult;
  "turn/read": TurnSnapshotResult;
  "turn/interrupt": {
    accepted: boolean;
    turn: Turn;
  };
  "turn/retry": TurnSnapshotResult;
  "workflow/start": WorkflowSnapshotResult;
  "workflow/read": WorkflowSnapshotResult;
  "workflow/list": {
    workflows: WorkflowRun[];
  };
  "workflow/interrupt": {
    accepted: boolean;
    workflow: WorkflowRun;
  };
  "workflow/retry": WorkflowSnapshotResult;
  "workflow/respond": {
    workflow: WorkflowRun;
  };
  "artifact/list": {
    artifacts: Artifact[];
  };
  "artifact/read": {
    artifact: Artifact;
    content: string | null;
    truncated: boolean;
    directory: boolean;
  };
  "approval/respond": {
    approval: Approval;
  };
  "event/replay": {
    events: Event[];
    nextAfter: number | null;
    hasMore: boolean;
  };
  "file/list": {
    entries: FileEntry[];
    truncated: boolean;
  };
  "file/read": {
    file: FileContent;
  };
  "file/write": {
    file: FileContent;
  };
  "git/status": {
    status: GitStatus;
  };
  "git/diff": {
    files: FileDiff[];
  };
  "git/discard": {
    discarded: boolean;
    path: string;
  };
  "git/worktree/create": WorktreeResult;
  "git/worktree/remove": WorktreeResult;
  "terminal/create": {
    terminal: TerminalInfo;
  };
  "terminal/write": {
    written: number;
  };
  "terminal/resize": {
    terminal: TerminalInfo;
  };
  "terminal/close": {
    accepted: boolean;
  };
  "test/discover": {
    commands: TestCommand[];
  };
  "test/run": {
    item: Item;
    command: TestCommand;
    exitCode: number | null;
    timedOut: boolean;
    durationMs: number;
    stdout: string;
    stderr: string;
    outputTruncated: boolean;
  };
  "thread/resume": {
    thread: Thread;
  };
}
export interface InitializeResult {
  protocolVersion: "1.0";
  serverInfo: ClientInfo;
  clientInfo: ClientInfo;
  capabilities: {
    methods: string[];
    eventReplay: boolean;
    liveEvents: boolean;
    maxMessageBytes: number;
  };
}
export interface ConnectionCatalogResult {
  connections: ConnectionInfo[];
  templates: ConnectionTemplate[];
  configPath: string;
  credentialPath: string;
}
export interface ConnectionInfo {
  id: string;
  label: string;
  providerName: string;
  adapter: "openai_compat" | "anthropic";
  apiBase: string | null;
  apiKeyEnv: string | null;
  modelCatalog: "auto" | "openrouter" | "openai" | "anthropic" | "manual";
  manualModels: string[];
  manualModelEntries: ManualModelEntry[];
  configured: boolean;
  credentialSource: "environment" | "credential_store" | "legacy_config" | "not_required" | "missing";
  local: boolean;
  enabled: boolean;
  explicit: boolean;
}
export interface ConnectionTemplate {
  name: string;
  label: string;
  adapter: string;
  defaultApiBase: string | null;
  apiKeyEnv: string | null;
  requiresApiBase: boolean;
  local: boolean;
}
export interface ConnectionRemoveResult {
  removed: boolean;
  connections: ConnectionInfo[];
  templates: ConnectionTemplate[];
  configPath: string;
  credentialPath: string;
}
export interface ProviderTestResult {
  connectionId: string;
  status: "connected" | "ready" | "limited" | "error";
  ok: boolean;
  latencyMs: number;
  modelCount: number;
  error: string | null;
  /**
   * @minItems 3
   * @maxItems 3
   */
  stages: [ProviderVerificationStage, ProviderVerificationStage, ProviderVerificationStage];
}
export interface ProviderVerificationStage {
  id: "credential" | "catalog" | "model";
  status: "passed" | "failed" | "skipped" | "not_run";
  detail: string;
  latencyMs: number | null;
  modelCount: number | null;
  modelId: string | null;
}
export interface ProviderDiscoverResult {
  models: CatalogModel[];
  error: string | null;
}
export interface CatalogModel {
  id: string;
  name: string;
  contextWindow: number;
  maxOutputTokens: number;
  supportedParameters: string[];
  reasoning: ReasoningCapabilities | null;
}
export interface ReasoningCapabilities {
  supportedEfforts: string[];
  defaultEffort: string | null;
  defaultEnabled: boolean;
  mandatory: boolean;
  supportsSummary: boolean;
}
export interface ModelCatalogResult {
  connectionId: string;
  models: CatalogModel[];
  source: string;
  stale: boolean;
  error: string | null;
  refreshedAt: number | null;
}
export interface Project {
  id: string;
  canonicalPath: string;
  displayName: string;
  trustState: TrustState;
  settings: JsonObject;
  createdAt: string;
  updatedAt: string;
  lastOpenedAt: string;
}
export interface SettingsSnapshot {
  configPath: string;
  configRevision: string;
  agents: JsonObject;
  security: JsonObject;
  permissionModeExplicit: boolean;
  userAccessPreset: ExecutionAccessPreset | null;
  projectAccessPreset: ExecutionAccessPreset | null;
  resolvedDefaultSecurityProfile: ExecutionSecurityProfile;
  resolvedDefaultSecuritySource: "user" | "project" | "environment" | "user_legacy" | "project_legacy" | "built_in";
  providers: SettingsProvider[];
  models: SettingsModel[];
}
/**
 * Immutable, resolved tool-security snapshot used by one Turn.
 */
export interface ExecutionSecurityProfile {
  accessPreset: ExecutionAccessPreset | null;
  permissionMode: ExecutionPermissionMode;
  commandSandbox: boolean;
  filesystemScope: "workspace" | "unrestricted";
  approvalPolicy: "on_request" | "never";
  /**
   * Ordered, immutable rules evaluated within the preset safety boundary.
   */
  permissionRules: ExecutionPermissionRule[];
}
/**
 * One immutable tool-permission rule captured for a Turn.
 */
export interface ExecutionPermissionRule {
  permission: string;
  pattern: string;
  action: "allow" | "ask" | "deny";
}
export interface SettingsProvider {
  id: string;
  name: string;
  label: string;
  configured: boolean;
  credentialSource: "environment" | "config" | "not_required" | "missing";
  apiBase: string | null;
  local: boolean;
}
export interface SettingsModel {
  id: string;
  contextWindow: number;
  maxOutputTokens: number;
  source: string;
}
export interface PresetListResult {
  presets: AgentPresetEntry[];
}
export interface AgentPresetEntry {
  id: string;
  trust: "system" | "user" | "project";
  name: string;
  description: string;
  tools: string[] | null;
  broken: string | null;
}
export interface PresetCurrentResult {
  agentPreset: string | null;
}
export interface PresetSelectResult {
  agentPreset: string | null;
}
export interface SkillCatalogResult {
  skills: SkillInfo[];
  warnings: string[];
  catalogRevision: string;
  authoringSkillId: string | null;
}
export interface SkillInfo {
  id: string;
  name: string;
  description: string;
  allowedTools: string[];
  scope: "user" | "project" | "system";
  sourceRoot: "agents" | "deepcode" | "claude" | "system";
  source: string;
  location: string;
  originKind: "local" | "bundled" | "provider";
  originLabel: string;
  providerKind: "local" | "executor" | "orchestrator" | "custom";
  providerId: string;
  packageId: string;
  status: "active" | "shadowed" | "disabled" | "invalid";
  enabled: boolean;
  selectable: boolean;
  revision: string;
  byteSize: number;
  shadowedBy: string | null;
  error: string | null;
  displayName: string | null;
  shortDescription: string | null;
  iconSmall: string | null;
  iconLarge: string | null;
  brandColor: string | null;
  defaultPrompt: string | null;
  allowImplicitInvocation: boolean;
  configurableScopes: ConfigScope[];
  deletable: boolean;
}
export interface SkillDetail {
  id: string;
  name: string;
  description: string;
  allowedTools: string[];
  scope: "user" | "project" | "system";
  sourceRoot: "agents" | "deepcode" | "claude" | "system";
  source: string;
  location: string;
  originKind: "local" | "bundled" | "provider";
  originLabel: string;
  providerKind: "local" | "executor" | "orchestrator" | "custom";
  providerId: string;
  packageId: string;
  status: "active" | "shadowed" | "disabled" | "invalid";
  enabled: boolean;
  selectable: boolean;
  revision: string;
  byteSize: number;
  shadowedBy: string | null;
  error: string | null;
  displayName: string | null;
  shortDescription: string | null;
  iconSmall: string | null;
  iconLarge: string | null;
  brandColor: string | null;
  defaultPrompt: string | null;
  allowImplicitInvocation: boolean;
  configurableScopes: ConfigScope[];
  deletable: boolean;
  instructions: string;
  truncated: boolean;
}
export interface PluginCatalogResult {
  plugins: PluginInfo[];
  diagnostics: PluginDiagnostic[];
  revision: string;
}
export interface PluginInfo {
  id: string;
  name: string;
  version: string | null;
  description: string;
  status: "active" | "disabled" | "invalid";
  enabled: boolean;
  source: "linked-directory";
  path: string;
  schema: "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json" | null;
  manifestPath: string;
  manifestRevision: string | null;
  components: PluginComponent[];
  diagnostics: PluginDiagnostic[];
  error: string | null;
}
export interface PluginComponent {
  kind: "skills" | "mcp";
  status: "ready" | "unsupported" | "invalid";
  resource: string | null;
  itemCount: number | null;
  diagnostics: PluginDiagnostic[];
}
export interface PluginDiagnostic {
  code: string;
  severity: "warning" | "error";
  message: string;
  component: "skills" | "mcp" | null;
  resource: string | null;
}
export interface HookInfo {
  eventName: string;
  matcher: string | null;
  command: string;
  timeoutSeconds: number;
  source: "user" | "project";
  sourcePath: string;
  displayOrder: number;
  statusMessage: string | null;
}
export interface McpInventory {
  servers: McpServerInfo[];
  userConfigPath: string;
  projectConfigPath: string | null;
}
export interface McpServerInfo {
  id: string;
  name: string;
  pluginId: string | null;
  policyKey: string | null;
  transport: "stdio" | "sse" | "streamableHttp";
  command: string | null;
  args: string[];
  cwd: string | null;
  url: string | null;
  auth: "oauth" | null;
  enabled: boolean;
  required: boolean;
  enabledTools: string[] | null;
  disabledTools: string[];
  startupTimeoutSeconds: number;
  toolTimeoutSeconds: number;
  approvalMode: "auto" | "prompt" | "writes" | "approve";
  description: string | null;
  envKeys: string[];
  forwardedEnvKeys: string[];
  requiredEnvKeys: string[];
  missingEnvKeys: string[];
  credentialEnvKeys: string[];
  headerKeys: string[];
  source: "user" | "project" | "plugin";
  configurationState: "configured" | "invalid" | "disabled" | "blocked" | "missing_credentials";
  configurationMessage: string;
  authState: "not_required" | "login_required" | "authorizing" | "authenticated";
  runtimeState: "stopped" | "connecting" | "tested" | "connected" | "failed";
  runtimeMessage: string;
  toolCount: number;
  resourceCount: number;
  promptCount: number;
}
export interface McpPresetInventory {
  presets: McpPresetInfo[];
  source: string;
  sourceRevision: string;
}
export interface McpPresetInfo {
  id: string;
  displayName: string;
  category: string;
  description: string;
  docsUrl: string;
  transport: "stdio" | "sse" | "streamableHttp";
  auth: "oauth" | null;
  requires: string;
  note: string;
  requiredEnvironment: string[];
  missingEnvironment: string[];
  configured: boolean;
}
export interface McpProbeResult {
  serverId: string;
  name: string;
  ok: boolean;
  transport: "stdio" | "sse" | "streamableHttp";
  toolCount: number;
  resourceCount: number;
  promptCount: number;
  elapsedSeconds: number;
  error: string | null;
}
export interface McpOAuthFlow {
  flowId: string;
  serverId: string;
  name: string;
  status:
    "starting" | "authorization_required" | "connecting" | "authenticated" | "failed" | "cancelled" | "logged_out";
  authorizationUrl: string | null;
  expiresInSeconds: number;
  error: string | null;
}
export interface DiagnosticsSnapshot {
  appVersion: string;
  pythonVersion: string;
  pythonExecutable: string;
  platform: string;
  architecture: string;
  processId: number;
  databasePath: string;
  databaseSchemaVersion: number;
  databaseBytes: number;
  sessionStorePath: string;
  sessionCount: number;
  projectCount: number;
  threadCount: number;
  workflowCount: number;
  automationCount: number;
  userConfigPath: string;
  projectConfigPath: string | null;
  projectPath: string | null;
  projectTrust: TrustState | null;
  configError: string | null;
  checks: DiagnosticsCheck[];
}
export interface DiagnosticsCheck {
  id: string;
  label: string;
  status: "ok" | "warning" | "error";
  detail: string;
}
export interface Automation {
  id: string;
  projectId: string;
  threadId: string;
  name: string;
  currentRevisionId: string;
  prompt: string;
  status: AutomationStatus;
  scheduleKind: AutomationScheduleKind;
  intervalSeconds: number | null;
  nextRunAt: string | null;
  lastRunAt: string | null;
  createdAt: string;
  updatedAt: string;
}
export interface AutomationRun {
  id: string;
  automationId: string;
  revisionId: string;
  occurrenceId: string;
  threadId: string;
  goalId: string | null;
  turnId: string | null;
  trigger: AutomationTrigger;
  status: AutomationRunStatus;
  scheduledFor: string;
  detail: string;
  createdAt: string;
  updatedAt: string;
  startedAt: string | null;
  completedAt: string | null;
}
export interface Thread {
  id: string;
  projectId: string;
  parentThreadId: string | null;
  title: string;
  mode: ThreadMode;
  status: ThreadStatus;
  model: string | null;
  connectionId: string | null;
  reasoningEffort: string | null;
  /**
   * Session override. Null inherits the effective Settings default.
   */
  accessPresetOverride: ExecutionAccessPreset | null;
  workspacePath: string;
  worktreePath: string | null;
  createdAt: string;
  updatedAt: string;
  archivedAt: string | null;
}
export interface Turn {
  id: string;
  threadId: string;
  ordinal: number;
  prompt: string;
  /**
   * @maxItems 8
   */
  skillIds?:
    | []
    | [string]
    | [string, string]
    | [string, string, string]
    | [string, string, string, string]
    | [string, string, string, string, string]
    | [string, string, string, string, string, string]
    | [string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string];
  executionProfile?: ExecutionProfile | null;
  executionPermissionMode?: ExecutionPermissionMode | null;
  executionSecurityProfile?: ExecutionSecurityProfile | null;
  goalId?: string | null;
  status: TurnStatus;
  stopReason: string | null;
  errorCode: string | null;
  errorMessage: string | null;
  startedAt: string | null;
  completedAt: string | null;
}
export interface ExecutionProfile {
  connectionId: string;
  providerName: string;
  adapter: "openai_compat" | "anthropic";
  modelId: string;
  contextWindow: number;
  maxOutputTokens: number;
  maxTokens: number;
  temperature: number;
  reasoningEffort: string | null;
  configRevision: string;
}
export interface GoalResult {
  goal: Goal | null;
  outcome: GoalOutcome | null;
}
export interface Goal {
  id: string;
  threadId: string;
  objective: string;
  status: GoalStatus;
  tokenBudget: number | null;
  tokensUsed: number;
  timeUsedSeconds: number;
  /**
   * @maxItems 8
   */
  skillIds:
    | []
    | [string]
    | [string, string]
    | [string, string, string]
    | [string, string, string, string]
    | [string, string, string, string, string]
    | [string, string, string, string, string, string]
    | [string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string];
  createdAt: string;
  updatedAt: string;
}
export interface GoalOutcome {
  status: "complete" | "blocked";
  reason: string;
  source: "user" | "agent" | "runtime" | "migration";
  decidedByTurnId: string | null;
  decidedAt: string;
  /**
   * @maxItems 12
   */
  evidenceRefs:
    | []
    | [GoalEvidenceRef]
    | [GoalEvidenceRef, GoalEvidenceRef]
    | [GoalEvidenceRef, GoalEvidenceRef, GoalEvidenceRef]
    | [GoalEvidenceRef, GoalEvidenceRef, GoalEvidenceRef, GoalEvidenceRef]
    | [GoalEvidenceRef, GoalEvidenceRef, GoalEvidenceRef, GoalEvidenceRef, GoalEvidenceRef]
    | [GoalEvidenceRef, GoalEvidenceRef, GoalEvidenceRef, GoalEvidenceRef, GoalEvidenceRef, GoalEvidenceRef]
    | [
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef
      ]
    | [
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef
      ]
    | [
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef
      ]
    | [
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef
      ]
    | [
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef
      ]
    | [
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef,
        GoalEvidenceRef
      ];
}
export interface GoalEvidenceRef {
  itemId: string;
  turnId: string;
  kind: ItemKind;
  status: ItemStatus;
  summary: string;
}
export interface GoalContinueResult {
  goal: Goal;
  disposition: "started" | "alreadyRunning";
  turnId: string;
  outcome: GoalOutcome | null;
}
export interface TurnSnapshotResult {
  turn: Turn;
  items: Item[];
  approvals: Approval[];
}
export interface Item {
  id: string;
  threadId: string;
  turnId: string;
  ordinal: number;
  kind: ItemKind;
  status: ItemStatus;
  summary: string;
  payload: JsonObject;
  createdAt: string;
  updatedAt: string;
}
export interface Approval {
  id: string;
  threadId: string;
  turnId: string;
  itemId: string;
  category: "command" | "file_write" | "network" | "external_tool" | "destructive";
  status: ApprovalStatus;
  request: JsonObject;
  decision: JsonObject | null;
  requestedAt: string;
  resolvedAt: string | null;
}
export interface TurnSteerResult {
  messageId: string;
  delivery: "current_turn";
  duplicate: boolean;
  turn: Turn;
}
export interface WorkflowSnapshotResult {
  workflow: WorkflowRun;
  turn: Turn;
  items: Item[];
  artifacts: Artifact[];
}
export interface WorkflowRun {
  id: string;
  threadId: string;
  turnId: string;
  kind: "paper2code";
  status: WorkflowStatus;
  input: JsonObject;
  result: JsonObject;
  attempt: number;
  retryOf: string | null;
  currentStage: string | null;
  progressCurrent: number;
  progressTotal: number | null;
  checkpoint: JsonObject;
  createdAt: string;
  updatedAt: string;
  startedAt: string | null;
  completedAt: string | null;
  errorCode: string | null;
  errorMessage: string | null;
}
export interface Artifact {
  id: string;
  threadId: string;
  turnId: string | null;
  workflowRunId: string | null;
  kind: string;
  name: string;
  mediaType: string;
  storagePath: string;
  byteSize: number | null;
  metadata: JsonObject;
  createdAt: string;
}
export interface Event {
  eventId: string;
  sequence: number;
  type: string;
  threadId: string;
  turnId: string | null;
  itemId: string | null;
  timestamp: string;
  payload: JsonObject;
}
export interface FileEntry {
  path: string;
  name: string;
  kind: "file" | "directory" | "symlink";
  size: number | null;
  modifiedAt: string | null;
  hidden: boolean;
}
export interface FileContent {
  path: string;
  content: string;
  byteSize: number;
  sha256: string | null;
  lineCount: number;
  truncated: boolean;
}
export interface GitStatus {
  repositoryRoot: string;
  branch: string | null;
  upstream: string | null;
  ahead: number;
  behind: number;
  detached: boolean;
  entries: GitStatusEntry[];
}
export interface GitStatusEntry {
  path: string;
  originalPath: string | null;
  indexStatus: string;
  worktreeStatus: string;
  kind: string;
}
export interface FileDiff {
  path: string;
  originalPath: string | null;
  status: string;
  binary: boolean;
  additions: number;
  deletions: number;
  revision: string;
  hunks: DiffHunk[];
}
export interface DiffHunk {
  oldStart: number;
  oldLines: number;
  newStart: number;
  newLines: number;
  heading: string;
  lines: DiffLine[];
}
export interface DiffLine {
  kind: "context" | "addition" | "deletion" | "meta";
  text: string;
  oldLine: number | null;
  newLine: number | null;
}
export interface WorktreeResult {
  thread: Thread;
  path: string;
  branch: string;
  disposition: "created" | "reclaimed" | "kept" | "cleaned";
  dirty: boolean;
}
export interface TerminalInfo {
  terminalId: string;
  threadId: string;
  pid: number;
  columns: number;
  rows: number;
  workspacePath: string;
}
export interface TestCommand {
  id: string;
  label: string;
  argv: string[];
}
export interface Notifications {
  "thread.updated": Event;
  "turn.started": Event;
  "turn.updated": Event;
  "turn.completed": Event;
  "turn.recovered": Event;
  "item.created": Event;
  "item.delta": Event;
  "item.updated": Event;
  "approval.requested": Event;
  "approval.resolved": Event;
  "workflow.started": Event;
  "workflow.updated": Event;
  "workflow.interaction_requested": Event;
  "workflow.completed": Event;
  "artifact.created": Event;
  "automation.updated": Event;
  "goal.updated": Event;
  "turn.plan.updated": TurnPlanUpdatedEvent;
  "terminal.output": {
    terminalId: string;
    threadId: string;
    data: string;
  };
  "terminal.exit": {
    terminalId: string;
    threadId: string;
    exitCode: number | null;
  };
  "skills.changed": {
    projectId: string;
  };
  "plugins.changed": {};
  "settings.changed": {
    configRevision: string;
  };
  "mcp.changed": {};
  "server.warning": {
    code: string;
    dropped: number;
    replayRequired: true;
  };
}
export interface TurnPlan {
  explanation: string | null;
  steps: TurnPlanStep[];
}
export interface TurnPlanStep {
  step: string;
  status: PlanStepStatus;
}
