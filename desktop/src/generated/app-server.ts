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
export type AutomationStatus = "enabled" | "paused";
export type ThreadMode = "code" | "paper" | "brief" | "review" | "goal";
export type ApprovalDecision = "approved_once" | "approved_session" | "denied";
export type AutomationTrigger = "manual" | "scheduled";
export type AutomationRunStatus = "queued" | "running" | "waiting" | "completed" | "failed" | "interrupted" | "skipped";
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
  "provider/test": ConnectionIdentityParams;
  "model/list": ModelListParams;
  "skills/list": SkillListParams;
  "skill/read": SkillReadParams;
  "skills/import": SkillImportParams;
  "skills/set-enabled": SkillSetEnabledParams;
  "skills/delete": SkillIdentityParams;
  "skills/reload": ProjectReadParams;
  "hooks/list": ProjectReadParams;
  "mcp/list": OptionalProjectParams;
  "mcp/upsert": McpUpsertParams;
  "mcp/remove": McpRemoveParams;
  "diagnostics/read": OptionalProjectParams;
  "automation/list": OptionalProjectParams;
  "automation/create": AutomationCreateParams;
  "automation/update": AutomationUpdateParams;
  "automation/remove": AutomationIdentityParams;
  "automation/run": AutomationIdentityParams;
  "automation/runs": AutomationRunsParams;
  "thread/start": ThreadStartParams;
  "thread/list": ThreadListParams;
  "thread/read": ThreadReadParams;
  "thread/rename": ThreadRenameParams;
  "thread/model": ThreadModelParams;
  "thread/execution/update": ThreadExecutionParams;
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
}
export interface ProviderUpsertParams {
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
    manualModels?: string[];
    enabled?: boolean;
  };
}
export interface ConnectionIdentityParams {
  connectionId: string;
}
export interface ModelListParams {
  connectionId: string;
  projectId?: string;
  refresh?: boolean;
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
export interface AutomationCreateParams {
  projectId: string;
  name: string;
  prompt: string;
  scheduleKind: AutomationScheduleKind;
  intervalSeconds?: number;
  enabled?: boolean;
}
export interface AutomationUpdateParams {
  automationId: string;
  name?: string;
  prompt?: string;
  status?: AutomationStatus;
  scheduleKind?: AutomationScheduleKind;
  intervalSeconds?: number;
}
export interface AutomationIdentityParams {
  automationId: string;
}
export interface AutomationRunsParams {
  automationId: string;
  limit?: number;
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
  "hooks/list": {
    hooks: HookInfo[];
    warnings: string[];
    truncated: boolean;
  };
  "mcp/list": McpInventory;
  "mcp/upsert": McpInventory;
  "mcp/remove": McpInventory;
  "diagnostics/read": {
    diagnostics: DiagnosticsSnapshot;
  };
  "automation/list": {
    automations: Automation[];
    latestRuns: AutomationRun[];
    schedulerActive: boolean;
    executionMode: "while_app_running";
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
  modelCatalog: "openrouter" | "openai" | "anthropic" | "manual";
  manualModels: string[];
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
  ok: boolean;
  latencyMs: number;
  modelCount: number;
  error: string | null;
}
export interface ModelCatalogResult {
  connectionId: string;
  models: CatalogModel[];
  source: string;
  stale: boolean;
  error: string | null;
  refreshedAt: number | null;
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
  agents: JsonObject;
  security: JsonObject;
  permissionModeExplicit: boolean;
  providers: SettingsProvider[];
  models: SettingsModel[];
}
export interface SettingsProvider {
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
export interface SkillCatalogResult {
  skills: SkillInfo[];
  warnings: string[];
  catalogRevision: string;
}
export interface SkillInfo {
  id: string;
  name: string;
  description: string;
  allowedTools: string[];
  scope: "user" | "project";
  sourceRoot: "deepcode" | "claude";
  source: string;
  location: string;
  status: "active" | "shadowed" | "disabled" | "invalid";
  enabled: boolean;
  selectable: boolean;
  revision: string;
  byteSize: number;
  shadowedBy: string | null;
  error: string | null;
}
export interface SkillDetail {
  id: string;
  name: string;
  description: string;
  allowedTools: string[];
  scope: "user" | "project";
  sourceRoot: "deepcode" | "claude";
  source: string;
  location: string;
  status: "active" | "shadowed" | "disabled" | "invalid";
  enabled: boolean;
  selectable: boolean;
  revision: string;
  byteSize: number;
  shadowedBy: string | null;
  error: string | null;
  instructions: string;
  truncated: boolean;
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
  name: string;
  transport: "stdio" | "sse" | "streamableHttp";
  command: string | null;
  args: string[];
  url: string | null;
  enabledTools: string[];
  toolTimeout: number;
  description: string | null;
  envKeys: string[];
  headerKeys: string[];
  source: "user" | "project" | "default";
  configurationState: "configured" | "invalid";
  configurationMessage: string;
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
  threadId: string;
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
