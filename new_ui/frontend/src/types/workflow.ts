// Workflow types

export type WorkflowStatus = 'idle' | 'running' | 'completed' | 'error' | 'cancelled';

export interface WorkflowStep {
  id: string;
  title: string;
  subtitle: string;
  progress: number;
  status: 'pending' | 'active' | 'completed' | 'error';
}

export interface WorkflowTask {
  taskId: string;
  status: WorkflowStatus;
  progress: number;
  message: string;
  result?: Record<string, unknown>;
  error?: string;
  startedAt?: string;
  completedAt?: string;
}

export interface WorkflowInput {
  type: 'paper-to-code' | 'chat-planning';
  inputSource: string;
  inputType: 'file' | 'url' | 'chat';
  enableIndexing: boolean;
}

// Workflow step definitions
export const PAPER_TO_CODE_STEPS: WorkflowStep[] = [
  { id: 'init', title: 'Initialize', subtitle: 'Load systems', progress: 5, status: 'pending' },
  { id: 'analyze', title: 'Analyze', subtitle: 'Parse paper', progress: 10, status: 'pending' },
  { id: 'download', title: 'Download', subtitle: 'Collect refs', progress: 25, status: 'pending' },
  { id: 'plan', title: 'Plan', subtitle: 'Blueprint', progress: 40, status: 'pending' },
  { id: 'references', title: 'References', subtitle: 'Key refs', progress: 50, status: 'pending' },
  { id: 'repos', title: 'Repos', subtitle: 'GitHub sync', progress: 60, status: 'pending' },
  { id: 'index', title: 'Index', subtitle: 'Vectorize', progress: 70, status: 'pending' },
  { id: 'implement', title: 'Implement', subtitle: 'Code gen', progress: 85, status: 'pending' },
];

export const CHAT_PLANNING_STEPS: WorkflowStep[] = [
  { id: 'init', title: 'Initialize', subtitle: 'Boot agents', progress: 5, status: 'pending' },
  { id: 'plan', title: 'Plan', subtitle: 'Analyze intent', progress: 30, status: 'pending' },
  { id: 'setup', title: 'Setup', subtitle: 'Workspace', progress: 50, status: 'pending' },
  { id: 'draft', title: 'Draft', subtitle: 'Generate plan', progress: 70, status: 'pending' },
  { id: 'implement', title: 'Implement', subtitle: 'Code gen', progress: 85, status: 'pending' },
];
