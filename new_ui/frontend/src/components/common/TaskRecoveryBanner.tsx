/**
 * Task Recovery Banner
 *
 * Shows a notification when a running task is recovered after page refresh.
 */

import { motion, AnimatePresence } from 'framer-motion';
import { RefreshCw, X, ExternalLink } from 'lucide-react';
import { useWorkflowStore } from '../../stores/workflowStore';
import { useNavigate } from 'react-router-dom';

interface TaskRecoveryBannerProps {
  isRecovering: boolean;
  recoveredTaskId: string | null;
  onDismiss: () => void;
}

export function TaskRecoveryBanner({
  isRecovering,
  recoveredTaskId,
  onDismiss,
}: TaskRecoveryBannerProps) {
  const navigate = useNavigate();
  const { workflowType, status } = useWorkflowStore();

  const handleGoToTask = () => {
    if (workflowType === 'chat-planning') {
      navigate('/chat-planning');
    } else if (workflowType === 'paper-to-code') {
      navigate('/paper-to-code');
    }
    onDismiss();
  };

  // Don't show if not recovering and no recovered task
  if (!isRecovering && !recoveredTaskId) {
    return null;
  }

  // Don't show if task is completed or has error
  if (status === 'completed' || status === 'error' || status === 'idle') {
    return null;
  }

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0, y: -50 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -50 }}
        className="fixed top-4 left-1/2 transform -translate-x-1/2 z-50"
      >
        <div className="bg-blue-50 border border-blue-200 rounded-lg shadow-lg px-4 py-3 flex items-center space-x-3">
          {isRecovering ? (
            <>
              <RefreshCw className="h-5 w-5 text-blue-500 animate-spin" />
              <span className="text-sm text-blue-700">
                Recovering task...
              </span>
            </>
          ) : (
            <>
              <RefreshCw className="h-5 w-5 text-blue-500" />
              <span className="text-sm text-blue-700">
                Task recovered! Your workflow is still running.
              </span>
              <button
                onClick={handleGoToTask}
                className="flex items-center text-sm font-medium text-blue-600 hover:text-blue-800"
              >
                View
                <ExternalLink className="h-3 w-3 ml-1" />
              </button>
              <button
                onClick={onDismiss}
                className="text-blue-400 hover:text-blue-600"
              >
                <X className="h-4 w-4" />
              </button>
            </>
          )}
        </div>
      </motion.div>
    </AnimatePresence>
  );
}
