// Workflow types

export type WorkflowStatus =
  | 'idle'
  | 'running'
  | 'waiting_for_input'
  | 'completed'
  | 'completed_with_warnings'
  | 'incomplete'
  | 'interrupted'
  | 'error'
  | 'cancelled';

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
  { id: 'init', title: 'Initialize', subtitle: 'Start workflow', progress: 5, status: 'pending' },
  { id: 'input', title: 'Input acquisition', subtitle: 'Copy and convert PDF', progress: 25, status: 'pending' },
  { id: 'workspace', title: 'Workspace setup', subtitle: 'Prepare task directory', progress: 40, status: 'pending' },
  { id: 'preprocess', title: 'Document preprocessing', subtitle: 'Segment paper content', progress: 50, status: 'pending' },
  { id: 'planning', title: 'Planning', subtitle: 'Generate implementation plan', progress: 60, status: 'pending' },
  { id: 'references', title: 'Reference research', subtitle: 'Analyze related work', progress: 70, status: 'pending' },
  { id: 'implementation', title: 'Implementation', subtitle: 'Generate code files', progress: 85, status: 'pending' },
];

export const CHAT_PLANNING_STEPS: WorkflowStep[] = [
  { id: 'init', title: 'Initialize', subtitle: 'Boot agents', progress: 5, status: 'pending' },
  { id: 'plan', title: 'Plan', subtitle: 'Analyze intent', progress: 30, status: 'pending' },
  { id: 'setup', title: 'Setup', subtitle: 'Workspace', progress: 50, status: 'pending' },
  { id: 'draft', title: 'Draft', subtitle: 'Generate plan', progress: 70, status: 'pending' },
  { id: 'implement', title: 'Implement', subtitle: 'Code gen', progress: 85, status: 'pending' },
];
