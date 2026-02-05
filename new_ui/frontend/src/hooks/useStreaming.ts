import { useEffect, useCallback, useRef } from 'react';
import { useWebSocket } from './useWebSocket';
import { useWorkflowStore } from '../stores/workflowStore';
import type {
  WSProgressMessage,
  WSCompleteMessage,
  WSErrorMessage,
  WSCodeChunkMessage,
  WSInteractionMessage,
} from '../types/api';

type WSMessage = WSProgressMessage | WSCompleteMessage | WSErrorMessage | WSCodeChunkMessage | WSInteractionMessage;

export function useStreaming(taskId: string | null) {
  const {
    status,
    updateProgress,
    setStatus,
    setResult,
    setError,
    appendStreamedCode,
    setCurrentFile,
    addGeneratedFile,
    addActivityLog,
    setPendingInteraction,
    clearInteraction,
  } = useWorkflowStore();

  // Track previous taskId to detect changes
  const prevTaskIdRef = useRef<string | null>(null);

  // Determine if finished based on store status (persisted state)
  const isFinished = status === 'completed' || status === 'error';

  const handleMessage = useCallback(
    (message: WSMessage) => {
      console.log('[useStreaming] Received message:', message.type, message);

      switch (message.type) {
        case 'progress':
          if ('progress' in message && message.progress !== undefined) {
            updateProgress(message.progress, message.message || '');
            // Add to activity log if there's a meaningful message
            if (message.message && message.message.trim()) {
              addActivityLog(message.message, message.progress, 'progress');
            }
          }
          break;

        case 'status':
          // Handle status messages - check if task is already completed
          if ('progress' in message && message.progress !== undefined) {
            updateProgress(message.progress, message.message || '');
            // Add initial status to activity log
            if (message.message && message.message.trim()) {
              addActivityLog(message.message, message.progress, 'info');
            }
          }
          // Check if the status indicates completion (for reconnection after task finished)
          if ('status' in message) {
            const taskStatus = (message as unknown as { status: string }).status;
            if (taskStatus === 'completed') {
              console.log('[useStreaming] Task already completed (from status message)');
              // Don't set finished here - wait for the complete message with result
            } else if (taskStatus === 'error') {
              console.log('[useStreaming] Task already errored (from status message)');
            } else if (taskStatus === 'waiting_for_input') {
              console.log('[useStreaming] Task waiting for input');
              // The interaction details will come in a separate interaction_required message
            }
          }
          break;

        case 'interaction_required':
          // User-in-Loop: workflow is requesting user input
          console.log('[useStreaming] Interaction required:', message.interaction_type);
          addActivityLog(`⏸️ Waiting for input: ${message.title}`, 0, 'info');
          setPendingInteraction({
            type: message.interaction_type,
            title: message.title,
            description: message.description,
            data: message.data,
            options: message.options,
            required: message.required,
          });
          break;

        case 'complete':
          console.log('[useStreaming] Workflow complete!');
          console.log('[useStreaming] Result:', JSON.stringify(message.result, null, 2));
          setStatus('completed');  // This will make isFinished = true
          setResult(message.result);
          clearInteraction(); // Clear any pending interaction
          // Update progress to 100% to mark all steps as complete
          updateProgress(100, 'Workflow completed successfully');
          addActivityLog('✅ Workflow completed successfully!', 100, 'success');
          break;

        case 'error':
          // Handle "Task not found" - clear state and stop reconnecting
          if (message.error === 'Task not found') {
            console.log('[useStreaming] Task not found, clearing persisted state...');
            // Reset the entire workflow state (this also clears localStorage)
            useWorkflowStore.getState().reset();
          } else {
            // Real error - mark as error state
            setStatus('error');  // This will make isFinished = true
            setError(message.error);
            clearInteraction(); // Clear any pending interaction
            addActivityLog(`❌ Error: ${message.error}`, 0, 'error');
          }
          break;

        case 'code_chunk':
          if (message.content) {
            appendStreamedCode(message.content);
          }
          break;

        case 'file_start':
          if (message.filename) {
            setCurrentFile(message.filename);
          }
          break;

        case 'file_end':
          if (message.filename) {
            addGeneratedFile(message.filename);
            setCurrentFile(null);
          }
          break;

        case 'heartbeat':
          // Ignore heartbeat messages
          break;
      }
    },
    [updateProgress, setStatus, setResult, setError, appendStreamedCode, setCurrentFile, addGeneratedFile, addActivityLog, setPendingInteraction, clearInteraction]
  );

  // Compute effective URL - null if finished to stop WebSocket
  const workflowUrl = taskId && !isFinished ? `/ws/workflow/${taskId}` : null;
  const codeStreamUrl = taskId && !isFinished ? `/ws/code-stream/${taskId}` : null;

  const workflowWs = useWebSocket(workflowUrl, {
    onMessage: handleMessage as (message: unknown) => void,
    reconnect: true,
  });

  const codeStreamWs = useWebSocket(codeStreamUrl, {
    onMessage: handleMessage as (message: unknown) => void,
    reconnect: true,
  });

  // Reset status to running only when taskId actually changes to a new value
  useEffect(() => {
    if (taskId && taskId !== prevTaskIdRef.current) {
      console.log('[useStreaming] taskId changed from', prevTaskIdRef.current, 'to', taskId, '- resetting to running');
      prevTaskIdRef.current = taskId;
      setStatus('running');
    } else if (!taskId) {
      prevTaskIdRef.current = null;
    }
  }, [taskId, setStatus]);

  return {
    isConnected: workflowWs.isConnected || codeStreamWs.isConnected,
    isFinished,
    disconnect: () => {
      workflowWs.disconnect();
      codeStreamWs.disconnect();
    },
  };
}
