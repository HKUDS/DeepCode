/* AUTO-GENERATED from protocol/app-server.schema.json. DO NOT EDIT. */

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
export type AutomationScheduleKind = "manual" | "interval";
export type AutomationStatus = "enabled" | "paused";
export type ThreadMode = "code" | "paper" | "brief" | "review" | "goal";
export type ApprovalDecision = "approved_once" | "approved_session" | "denied";
export type AutomationTrigger = "manual" | "scheduled";
export type AutomationRunStatus = "queued" | "running" | "waiting" | "completed" | "failed" | "interrupted" | "skipped";
export type ThreadStatus = "idle" | "running" | "waiting" | "failed" | "archived";
export type TurnStatus = "queued" | "running" | "waiting_approval" | "completed" | "failed" | "interrupted";
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
  "skills/list": ProjectReadParams;
  "skill/read": SkillReadParams;
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
  "thread/archive": ThreadReadParams;
  "thread/fork": ThreadForkParams;
  "turn/start": TurnStartParams;
  "turn/enqueue": TurnStartParams;
  "turn/read": TurnReadParams;
  "turn/interrupt": TurnReadParams;
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
export interface SkillReadParams {
  projectId: string;
  name: string;
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
  model?: string;
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
}
export interface ThreadForkParams {
  threadId: string;
  title?: string;
}
export interface TurnStartParams {
  threadId: string;
  prompt: string;
}
export interface TurnReadParams {
  turnId: string;
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
  "skills/list": {
    skills: SkillInfo[];
    warnings: string[];
  };
  "skill/read": {
    skill: SkillDetail;
  };
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
  "thread/archive": {
    thread: Thread;
  };
  "thread/fork": {
    thread: Thread;
  };
  "turn/start": TurnSnapshotResult;
  "turn/enqueue": TurnSnapshotResult;
  "turn/read": TurnSnapshotResult;
  "turn/interrupt": {
    accepted: boolean;
    turn: Turn;
  };
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
export interface SkillInfo {
  name: string;
  description: string;
  allowedTools: string[];
  directory: string;
  source: string;
}
export interface SkillDetail {
  name: string;
  description: string;
  allowedTools: string[];
  directory: string;
  source: string;
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
  status: TurnStatus;
  stopReason: string | null;
  errorCode: string | null;
  errorMessage: string | null;
  startedAt: string | null;
  completedAt: string | null;
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
  "item.updated": Event;
  "approval.requested": Event;
  "approval.resolved": Event;
  "workflow.started": Event;
  "workflow.updated": Event;
  "workflow.interaction_requested": Event;
  "workflow.completed": Event;
  "artifact.created": Event;
  "automation.updated": Event;
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
