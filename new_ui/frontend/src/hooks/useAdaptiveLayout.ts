import { useMemo } from 'react';
import type { TaskType, LayoutConfig } from '../types/common';

const layoutConfigs: Record<TaskType, LayoutConfig> = {
  'paper-to-code': {
    sidebarWidth: 320,
    showCodePreview: true,
    showWorkflowCanvas: true,
    splitRatio: 0.6,
  },
  'chat-planning': {
    sidebarWidth: 280,
    showCodePreview: true,
    showWorkflowCanvas: false,
    splitRatio: 0.5,
  },
  'workflow-editor': {
    sidebarWidth: 240,
    showCodePreview: false,
    showWorkflowCanvas: true,
    splitRatio: 0.7,
  },
  settings: {
    sidebarWidth: 280,
    showCodePreview: false,
    showWorkflowCanvas: false,
    splitRatio: 1,
  },
};

export function useAdaptiveLayout(taskType: TaskType): LayoutConfig {
  return useMemo(() => layoutConfigs[taskType], [taskType]);
}
