// Common types

export interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
  metadata?: Record<string, unknown>;
}

export interface Notification {
  id: string;
  type: 'success' | 'error' | 'warning' | 'info';
  title: string;
  description?: string;
  duration?: number;
}

export interface LayoutConfig {
  sidebarWidth: number;
  showCodePreview: boolean;
  showWorkflowCanvas: boolean;
  splitRatio: number;
}

export type TaskType = 'paper-to-code' | 'chat-planning' | 'workflow-editor' | 'settings';
