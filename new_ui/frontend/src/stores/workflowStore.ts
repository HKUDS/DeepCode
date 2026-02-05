import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type {
  WorkflowStatus,
  WorkflowStep,
  WorkflowTask,
  PAPER_TO_CODE_STEPS,
  CHAT_PLANNING_STEPS,
} from '../types/workflow';

// Activity log entry type
interface ActivityLogEntry {
  id: string;
  timestamp: Date;
  message: string;
  progress: number;
  type: 'info' | 'success' | 'warning' | 'error' | 'progress';
}

// User-in-Loop interaction types
export interface PendingInteraction {
  type: string;  // 'requirement_questions' | 'plan_review' | etc.
  title: string;
  description: string;
  data: {
    questions?: Array<{
      id: string;
      question: string;
      category?: string;
      importance?: string;
      hint?: string;
    }>;
    plan?: string;
    plan_preview?: string;
    original_input?: string;
    [key: string]: unknown;
  };
  options: Record<string, string>;
  required: boolean;
}

interface WorkflowState {
  // Current task
  activeTaskId: string | null;
  workflowType: 'paper-to-code' | 'chat-planning' | null;  // Track workflow type
  status: WorkflowStatus;
  progress: number;
  message: string;

  // Steps
  steps: WorkflowStep[];
  currentStepIndex: number;

  // Streaming data
  streamedCode: string;
  currentFile: string | null;
  generatedFiles: string[];

  // Activity logs
  activityLogs: ActivityLogEntry[];

  // User-in-Loop interaction
  pendingInteraction: PendingInteraction | null;
  isWaitingForInput: boolean;

  // Results
  result: Record<string, unknown> | null;
  error: string | null;

  // Recovery
  needsRecovery: boolean;  // Flag to indicate if we need to recover a task

  // Actions
  setActiveTask: (taskId: string | null, workflowType?: 'paper-to-code' | 'chat-planning') => void;
  setStatus: (status: WorkflowStatus) => void;
  updateProgress: (progress: number, message: string) => void;
  setSteps: (steps: WorkflowStep[]) => void;
  updateStepStatus: (stepId: string, status: WorkflowStep['status']) => void;
  appendStreamedCode: (chunk: string) => void;
  setCurrentFile: (filename: string | null) => void;
  addGeneratedFile: (filename: string) => void;
  addActivityLog: (message: string, progress: number, type?: ActivityLogEntry['type']) => void;
  setPendingInteraction: (interaction: PendingInteraction | null) => void;
  clearInteraction: () => void;
  setResult: (result: Record<string, unknown> | null) => void;
  setError: (error: string | null) => void;
  setNeedsRecovery: (needs: boolean) => void;
  reset: () => void;
}

const initialState = {
  activeTaskId: null,
  workflowType: null as 'paper-to-code' | 'chat-planning' | null,
  status: 'idle' as WorkflowStatus,
  progress: 0,
  message: '',
  steps: [],
  currentStepIndex: -1,
  streamedCode: '',
  currentFile: null,
  generatedFiles: [],
  activityLogs: [] as ActivityLogEntry[],
  pendingInteraction: null as PendingInteraction | null,
  isWaitingForInput: false,
  result: null,
  error: null,
  needsRecovery: false,
};

export const useWorkflowStore = create<WorkflowState>()(
  persist(
    (set, get) => ({
      ...initialState,

      setActiveTask: (taskId, workflowType) => set({
        activeTaskId: taskId,
        workflowType: workflowType ?? get().workflowType
      }),

  setStatus: (status) => {
    console.log('[workflowStore] setStatus:', status);
    set({ status });
  },

  updateProgress: (progress, message) => {
    const { steps } = get();

    // Find current step based on progress
    let currentStepIndex = -1;
    for (let i = steps.length - 1; i >= 0; i--) {
      if (progress >= steps[i].progress) {
        currentStepIndex = i;
        break;
      }
    }

    // Check if workflow is complete (progress >= 100)
    const isComplete = progress >= 100;

    // Update step statuses
    const updatedSteps = steps.map((step, index) => ({
      ...step,
      status:
        isComplete
          ? 'completed'  // All steps completed when progress >= 100
          : index < currentStepIndex
          ? 'completed'
          : index === currentStepIndex
          ? 'active'
          : 'pending',
    })) as WorkflowStep[];

    set({
      progress,
      message,
      currentStepIndex: isComplete ? steps.length - 1 : currentStepIndex,
      steps: updatedSteps,
    });
  },

  setSteps: (steps) => set({ steps }),

  updateStepStatus: (stepId, status) => {
    const { steps } = get();
    const updatedSteps = steps.map((step) =>
      step.id === stepId ? { ...step, status } : step
    );
    set({ steps: updatedSteps });
  },

  appendStreamedCode: (chunk) =>
    set((state) => ({
      streamedCode: state.streamedCode + chunk,
    })),

  setCurrentFile: (filename) => set({ currentFile: filename }),

  addGeneratedFile: (filename) =>
    set((state) => ({
      generatedFiles: [...state.generatedFiles, filename],
    })),

  addActivityLog: (message, progress, type = 'progress') =>
    set((state) => ({
      activityLogs: [
        ...state.activityLogs,
        {
          id: `log-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
          timestamp: new Date(),
          message,
          progress,
          type,
        },
      ],
    })),

  setPendingInteraction: (interaction) => {
    console.log('[workflowStore] setPendingInteraction:', interaction?.type);
    set({
      pendingInteraction: interaction,
      isWaitingForInput: interaction !== null,
    });
  },

  clearInteraction: () => {
    console.log('[workflowStore] clearInteraction');
    set({
      pendingInteraction: null,
      isWaitingForInput: false,
    });
  },

  setResult: (result) => {
    console.log('[workflowStore] setResult:', result);
    set({ result });
  },

  setError: (error) => set({ error, status: error ? 'error' : get().status }),

  setNeedsRecovery: (needs) => set({ needsRecovery: needs }),

  reset: () => {
    console.log('[workflowStore] Resetting state and clearing localStorage');
    // Clear localStorage explicitly to ensure clean state
    try {
      localStorage.removeItem('deepcode-workflow');
    } catch (e) {
      console.error('[workflowStore] Failed to clear localStorage:', e);
    }
    set(initialState);
  },
    }),
    {
      name: 'deepcode-workflow',
      // Only persist task-related data for recovery when task is running or waiting
      partialize: (state) => {
        const isActive = state.status === 'running' || state.isWaitingForInput;
        return {
          // Only persist activeTaskId if task is still running or waiting for input
          // This prevents trying to recover completed/errored tasks
          activeTaskId: isActive ? state.activeTaskId : null,
          workflowType: isActive ? state.workflowType : null,
          status: isActive ? state.status : 'idle',
          progress: isActive ? state.progress : 0,
          steps: isActive ? state.steps : [],
          isWaitingForInput: state.isWaitingForInput,
        };
      },
    }
  )
);
