// API types

export interface TaskResponse {
  task_id: string;
  status: string;
  message: string;
  created_at?: string;
}

export interface WorkflowStatusResponse {
  task_id: string;
  status: string;
  progress: number;
  message: string;
  result?: Record<string, unknown>;
  error?: string;
  started_at?: string;
  completed_at?: string;
}

export interface QuestionsResponse {
  questions: Question[];
  status: string;
}

export interface Question {
  id: string;
  question: string;
  category?: string;
  importance?: string;
  hint?: string;
}

export interface RequirementsSummaryResponse {
  summary: string;
  status: string;
}

export interface ConfigResponse {
  llm_provider: string;
  available_providers: string[];
  models: Record<string, string>;
  indexing_enabled: boolean;
}

export interface SettingsResponse {
  llm_provider: string;
  models: Record<string, string>;
  indexing_enabled: boolean;
  document_segmentation: Record<string, unknown>;
}

export interface FileUploadResponse {
  file_id: string;
  filename: string;
  path: string;
  size: number;
}

export interface ErrorResponse {
  error: string;
  detail?: string;
  code?: string;
}

// WebSocket message types
export interface WSProgressMessage {
  type: 'progress' | 'status' | 'heartbeat';
  task_id: string;
  progress?: number;
  message?: string;
  status?: string;
  timestamp: string;
}

export interface WSCompleteMessage {
  type: 'complete';
  task_id: string;
  status: string;
  result: Record<string, unknown>;
  timestamp: string;
}

export interface WSErrorMessage {
  type: 'error';
  task_id: string;
  error: string;
  timestamp: string;
}

export interface WSCodeChunkMessage {
  type: 'code_chunk' | 'file_start' | 'file_end';
  task_id: string;
  content?: string;
  filename?: string;
  timestamp: string;
}

export interface WSLogMessage {
  type: 'log';
  level: 'INFO' | 'WARNING' | 'ERROR' | 'DEBUG';
  message: string;
  namespace: string;
  timestamp: string;
}

// User-in-Loop interaction message
export interface WSInteractionMessage {
  type: 'interaction_required';
  task_id: string;
  interaction_type: 'requirement_questions' | 'plan_review' | 'code_review' | string;
  title: string;
  description: string;
  data: {
    questions?: Question[];
    plan?: string;
    plan_preview?: string;
    original_input?: string;
    [key: string]: unknown;
  };
  options: Record<string, string>;
  required: boolean;
  timestamp: string;
}

export type WSMessage =
  | WSProgressMessage
  | WSCompleteMessage
  | WSErrorMessage
  | WSCodeChunkMessage
  | WSLogMessage
  | WSInteractionMessage;
