/**
 * Task Recovery Hook
 *
 * Handles automatic recovery of running tasks after page refresh.
 *
 * Flow:
 * 1. On mount, check if there's a persisted activeTaskId
 * 2. If yes, query the backend to verify task status
 * 3. If task is still running, reconnect WebSocket
 * 4. If task is completed/error, sync the final state
 * 5. If task not found, clear the persisted state
 */

import { useEffect, useCallback, useState } from 'react';
import { useWorkflowStore } from '../stores/workflowStore';
import { workflowsApi } from '../services/api';
import { PAPER_TO_CODE_STEPS, CHAT_PLANNING_STEPS } from '../types/workflow';

interface RecoveryState {
  isRecovering: boolean;
  recoveredTaskId: string | null;
  error: string | null;
}

export function useTaskRecovery() {
  const {
    activeTaskId,
    workflowType,
    status,
    setActiveTask,
    setStatus,
    setSteps,
    updateProgress,
    setResult,
    setError,
    setNeedsRecovery,
    reset,
  } = useWorkflowStore();

  const [recoveryState, setRecoveryState] = useState<RecoveryState>({
    isRecovering: false,
    recoveredTaskId: null,
    error: null,
  });

  const recoverTask = useCallback(async () => {
    // Only recover if there's a persisted task and it was running
    if (!activeTaskId || status === 'idle' || status === 'completed' || status === 'error') {
      return;
    }

    console.log('[TaskRecovery] Attempting to recover task:', activeTaskId);
    setRecoveryState({ isRecovering: true, recoveredTaskId: null, error: null });

    try {
      // Query backend for task status
      const taskStatus = await workflowsApi.getStatus(activeTaskId);
      console.log('[TaskRecovery] Task status from backend:', taskStatus);

      if (taskStatus.status === 'running') {
        // Task is still running - restore steps and let WebSocket reconnect
        console.log('[TaskRecovery] Task still running, reconnecting...');

        // Restore steps based on workflow type
        if (workflowType === 'paper-to-code') {
          setSteps(PAPER_TO_CODE_STEPS);
        } else if (workflowType === 'chat-planning') {
          setSteps(CHAT_PLANNING_STEPS);
        }

        // Update progress from backend
        updateProgress(taskStatus.progress, taskStatus.message);
        setStatus('running');
        setNeedsRecovery(false);

        setRecoveryState({
          isRecovering: false,
          recoveredTaskId: activeTaskId,
          error: null,
        });

      } else if (taskStatus.status === 'completed') {
        // Task completed while we were away
        console.log('[TaskRecovery] Task completed, syncing final state...');

        if (workflowType === 'paper-to-code') {
          setSteps(PAPER_TO_CODE_STEPS);
        } else if (workflowType === 'chat-planning') {
          setSteps(CHAT_PLANNING_STEPS);
        }

        updateProgress(100, 'Completed');
        setStatus('completed');
        setResult(taskStatus.result || null);
        setNeedsRecovery(false);

        setRecoveryState({
          isRecovering: false,
          recoveredTaskId: activeTaskId,
          error: null,
        });

      } else if (taskStatus.status === 'error') {
        // Task errored while we were away
        console.log('[TaskRecovery] Task errored, syncing error state...');

        setStatus('error');
        setError(taskStatus.error || 'Unknown error');
        setNeedsRecovery(false);

        setRecoveryState({
          isRecovering: false,
          recoveredTaskId: activeTaskId,
          error: taskStatus.error || null,
        });

      } else {
        // Unknown status, reset
        console.log('[TaskRecovery] Unknown task status, resetting...');
        reset();
        setRecoveryState({
          isRecovering: false,
          recoveredTaskId: null,
          error: null,
        });
      }

    } catch (error) {
      // Task not found or API error
      console.error('[TaskRecovery] Failed to recover task:', error);

      // Always reset on any error - the task is no longer valid
      // This handles 404 (task not found) and any other API errors
      console.log('[TaskRecovery] Task not recoverable, clearing state...');
      reset();

      setRecoveryState({
        isRecovering: false,
        recoveredTaskId: null,
        error: null, // Don't show error - just clear state
      });
    }
  }, [activeTaskId, workflowType, status, setActiveTask, setStatus, setSteps, updateProgress, setResult, setError, setNeedsRecovery, reset]);

  // Run recovery on mount
  useEffect(() => {
    // Only run once on initial mount if there's a persisted running task
    if (activeTaskId && (status === 'running' || status === 'pending')) {
      setNeedsRecovery(true);
      recoverTask();
    }
  }, []); // Empty deps - only run on mount

  return {
    ...recoveryState,
    recoverTask,
  };
}
