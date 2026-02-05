import { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Card } from '../components/common';
import { ChatInput } from '../components/input';
import { ProgressTracker, ActivityLogViewer } from '../components/streaming';
import { FileTree } from '../components/results';
import { InlineChatInteraction } from '../components/interaction';
import { useWorkflowStore } from '../stores/workflowStore';
import { useSessionStore } from '../stores/sessionStore';
import { useStreaming } from '../hooks/useStreaming';
import { workflowsApi } from '../services/api';
import { toast } from '../components/common/Toaster';
import { CHAT_PLANNING_STEPS } from '../types/workflow';
import { MessageSquare, User, Bot, CheckCircle, XCircle, FolderOpen, StopCircle } from 'lucide-react';
import { ConfirmDialog } from '../components/common/ConfirmDialog';

export default function ChatPlanningPage() {
  const [enableIndexing, setEnableIndexing] = useState(false);
  const [showCancelDialog, setShowCancelDialog] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);
  const chatContainerRef = useRef<HTMLDivElement>(null);

  const {
    activeTaskId,
    status,
    progress,
    message,
    steps,
    generatedFiles,
    activityLogs,
    pendingInteraction,
    isWaitingForInput,
    result,
    error,
    setActiveTask,
    setSteps,
    setStatus,
    reset,
  } = useWorkflowStore();

  const { conversationHistory, addMessage } = useSessionStore();
  useStreaming(activeTaskId);

  // Debug: log status changes
  console.log('[ChatPlanningPage] status:', status, 'result:', result, 'error:', error);

  // Auto-scroll to bottom when new messages or interactions appear
  useEffect(() => {
    if (chatContainerRef.current) {
      chatContainerRef.current.scrollTop = chatContainerRef.current.scrollHeight;
    }
  }, [conversationHistory.length, pendingInteraction]);

  // Show toast and add message when workflow completes
  useEffect(() => {
    if (status === 'completed' && result) {
      toast.success('Code generation complete!', 'Your project has been generated successfully.');
      // Add completion message to chat
      const codeDir = result.repo_result && typeof result.repo_result === 'object'
        ? (result.repo_result as Record<string, unknown>).code_directory as string
        : null;
      addMessage({
        role: 'assistant',
        content: codeDir
          ? `Code generation complete! Your project has been generated at:\n\n${codeDir}`
          : 'Code generation complete! Your project has been successfully generated.',
      });
    } else if (status === 'error' && error) {
      toast.error('Generation failed', error);
      addMessage({
        role: 'assistant',
        content: `Sorry, code generation failed: ${error}`,
      });
    }
  }, [status, error, result, addMessage]);

  // Handle task cancellation
  const handleCancelTask = async () => {
    if (!activeTaskId) return;

    setIsCancelling(true);
    try {
      await workflowsApi.cancel(activeTaskId);
      setStatus('idle');
      reset();
      addMessage({
        role: 'assistant',
        content: 'Task cancelled. Feel free to start a new request.',
      });
      toast.info('Task cancelled', 'The workflow has been stopped.');
    } catch (err) {
      toast.error('Cancel failed', 'Could not cancel the task.');
      console.error('Cancel error:', err);
    } finally {
      setIsCancelling(false);
      setShowCancelDialog(false);
    }
  };

  const handleSubmit = async (message: string) => {
    try {
      // Add user message to history
      addMessage({ role: 'user', content: message });

      reset();
      setSteps(CHAT_PLANNING_STEPS);

      const response = await workflowsApi.startChatPlanning(
        message,
        enableIndexing
      );

      setActiveTask(response.task_id, 'chat-planning');
      addMessage({
        role: 'assistant',
        content: 'Starting code generation...',
        metadata: { taskId: response.task_id },
      });

      toast.info('Workflow started', 'Generating code from your requirements...');
    } catch (error) {
      toast.error('Failed to start workflow', 'Please try again');
      addMessage({
        role: 'assistant',
        content: 'Sorry, there was an error processing your request.',
      });
      console.error('Start error:', error);
    }
  };

  const isRunning = status === 'running';

  return (
    <div className="space-y-6">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
      >
        <h1 className="text-2xl font-bold text-gray-900">Chat Planning</h1>
        <p className="text-gray-500 mt-1">
          Describe your project and let AI generate the code for you
        </p>
      </motion.div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Left Column - Chat */}
        <div className="space-y-6">
          <Card padding="none" className="flex flex-col h-[600px]">
            {/* Chat Header */}
            <div className="px-4 py-3 border-b border-gray-100">
              <div className="flex items-center space-x-2">
                <MessageSquare className="h-5 w-5 text-primary-500" />
                <span className="font-medium text-gray-900">
                  Project Requirements
                </span>
              </div>
            </div>

            {/* Chat Messages */}
            <div ref={chatContainerRef} className="flex-1 overflow-y-auto p-4 space-y-4">
              {conversationHistory.length === 0 && !pendingInteraction ? (
                <div className="h-full flex items-center justify-center text-center text-gray-400">
                  <div>
                    <MessageSquare className="h-12 w-12 mx-auto mb-3 opacity-50" />
                    <p className="text-sm">
                      Describe your project requirements to get started
                    </p>
                  </div>
                </div>
              ) : (
                <>
                  {conversationHistory.map((msg) => (
                    <motion.div
                      key={msg.id}
                      initial={{ opacity: 0, y: 10 }}
                      animate={{ opacity: 1, y: 0 }}
                      className={`flex items-start space-x-3 ${
                        msg.role === 'user' ? 'flex-row-reverse space-x-reverse' : ''
                      }`}
                    >
                      <div
                        className={`flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center ${
                          msg.role === 'user'
                            ? 'bg-primary-100'
                            : 'bg-gray-100'
                        }`}
                      >
                        {msg.role === 'user' ? (
                          <User className="h-4 w-4 text-primary-600" />
                        ) : (
                          <Bot className="h-4 w-4 text-gray-600" />
                        )}
                      </div>
                      <div
                        className={`max-w-[80%] px-4 py-2 rounded-2xl ${
                          msg.role === 'user'
                            ? 'bg-primary-500 text-white'
                            : 'bg-gray-100 text-gray-900'
                        }`}
                      >
                        <p className="text-sm whitespace-pre-wrap">{msg.content}</p>
                      </div>
                    </motion.div>
                  ))}

                  {/* Inline Interaction - displayed in chat flow */}
                  <AnimatePresence>
                    {pendingInteraction && activeTaskId && (
                      <InlineChatInteraction
                        taskId={activeTaskId}
                        interaction={pendingInteraction}
                      />
                    )}
                  </AnimatePresence>
                </>
              )}
            </div>

            {/* Chat Input */}
            <div className="p-4 border-t border-gray-100">
              <ChatInput
                onSubmit={handleSubmit}
                isLoading={isRunning}
                placeholder="Describe your project requirements..."
              />
            </div>
          </Card>

          {/* Options */}
          <Card>
            <label className="flex items-center space-x-3 cursor-pointer">
              <input
                type="checkbox"
                checked={enableIndexing}
                onChange={(e) => setEnableIndexing(e.target.checked)}
                disabled={isRunning}
                className="w-4 h-4 text-primary-600 rounded focus:ring-primary-500 disabled:opacity-50"
              />
              <span className={`text-sm ${isRunning ? 'text-gray-400' : 'text-gray-700'}`}>
                Enable code indexing for better results
              </span>
            </label>

            {/* Cancel Button */}
            {isRunning && (
              <button
                onClick={() => setShowCancelDialog(true)}
                disabled={isCancelling}
                className="mt-4 w-full flex items-center justify-center space-x-2 px-4 py-2 text-sm font-medium text-red-600 bg-red-50 border border-red-200 rounded-lg hover:bg-red-100 transition-colors disabled:opacity-50"
              >
                <StopCircle className="h-4 w-4" />
                <span>Cancel Task</span>
              </button>
            )}
          </Card>
        </div>

        {/* Right Column - Results */}
        <div className="space-y-6">
          {/* Progress */}
          {status !== 'idle' && (
            <Card>
              <ProgressTracker steps={steps} currentProgress={progress} />
            </Card>
          )}

          {/* Activity Log */}
          <ActivityLogViewer
            logs={activityLogs}
            isRunning={isRunning && !isWaitingForInput}
            currentMessage={isWaitingForInput ? 'Waiting for your input...' : message}
          />

          {/* Generated Files */}
          {generatedFiles.length > 0 && (
            <FileTree files={generatedFiles} />
          )}

          {/* Completion Status */}
          {status === 'completed' && result && (
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
            >
              <Card className="border-green-200 bg-green-50">
                <div className="flex items-start space-x-3">
                  <CheckCircle className="h-6 w-6 text-green-500 flex-shrink-0" />
                  <div className="flex-1">
                    <h3 className="font-medium text-green-900">
                      Code Generation Complete!
                    </h3>
                    <p className="text-sm text-green-700 mt-1">
                      Your code has been successfully generated.
                    </p>
                    {result.repo_result && typeof result.repo_result === 'object' && 'code_directory' in (result.repo_result as Record<string, unknown>) && (
                      <div className="mt-3 flex items-center text-sm text-green-600">
                        <FolderOpen className="h-4 w-4 mr-2" />
                        <span className="font-mono text-xs">
                          {(result.repo_result as Record<string, unknown>).code_directory as string}
                        </span>
                      </div>
                    )}
                  </div>
                </div>
              </Card>
            </motion.div>
          )}

          {/* Error Status */}
          {status === 'error' && error && (
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
            >
              <Card className="border-red-200 bg-red-50">
                <div className="flex items-start space-x-3">
                  <XCircle className="h-6 w-6 text-red-500 flex-shrink-0" />
                  <div className="flex-1">
                    <h3 className="font-medium text-red-900">
                      Generation Failed
                    </h3>
                    <p className="text-sm text-red-700 mt-1">
                      {error}
                    </p>
                  </div>
                </div>
              </Card>
            </motion.div>
          )}
        </div>
      </div>

      {/* Cancel Confirmation Dialog */}
      <ConfirmDialog
        isOpen={showCancelDialog}
        title="Cancel Task?"
        message="Are you sure you want to cancel this task? Any progress will be lost and you'll need to start over."
        confirmLabel="Yes, Cancel"
        cancelLabel="Keep Running"
        variant="danger"
        onConfirm={handleCancelTask}
        onCancel={() => setShowCancelDialog(false)}
      />
    </div>
  );
}
