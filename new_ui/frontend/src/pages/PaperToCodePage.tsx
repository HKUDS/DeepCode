import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Card, Button } from '../components/common';
import { FileUploader, UrlInput } from '../components/input';
import { ProgressTracker, ActivityLogViewer } from '../components/streaming';
import { FileTree } from '../components/results';
import { InteractionPanel } from '../components/interaction';
import { useWorkflowStore } from '../stores/workflowStore';
import { useStreaming } from '../hooks/useStreaming';
import { workflowsApi } from '../services/api';
import { toast } from '../components/common/Toaster';
import { PAPER_TO_CODE_STEPS } from '../types/workflow';
import { CheckCircle, XCircle, FolderOpen, StopCircle } from 'lucide-react';
import { ConfirmDialog } from '../components/common/ConfirmDialog';

type InputMethod = 'file' | 'url';

export default function PaperToCodePage() {
  const [inputMethod, setInputMethod] = useState<InputMethod>('file');
  const [uploadedFilePath, setUploadedFilePath] = useState<string | null>(null);
  const [enableIndexing, setEnableIndexing] = useState(false);
  const [showCancelDialog, setShowCancelDialog] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);

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

  useStreaming(activeTaskId);

  // Show toast when workflow completes
  useEffect(() => {
    if (status === 'completed' && result) {
      toast.success('Paper processing complete!', 'Code has been generated successfully.');
    } else if (status === 'error' && error) {
      toast.error('Processing failed', error);
    }
  }, [status, error, result]);

  // Handle task cancellation
  const handleCancelTask = async () => {
    if (!activeTaskId) return;

    setIsCancelling(true);
    try {
      await workflowsApi.cancel(activeTaskId);
      setStatus('idle');
      reset();
      toast.info('Task cancelled', 'The workflow has been stopped.');
    } catch (err) {
      toast.error('Cancel failed', 'Could not cancel the task.');
      console.error('Cancel error:', err);
    } finally {
      setIsCancelling(false);
      setShowCancelDialog(false);
    }
  };

  const handleStart = async (inputSource: string, inputType: 'file' | 'url') => {
    try {
      reset();
      setSteps(PAPER_TO_CODE_STEPS);

      const response = await workflowsApi.startPaperToCode(
        inputSource,
        inputType,
        enableIndexing
      );

      setActiveTask(response.task_id, 'paper-to-code');
      toast.info('Workflow started', 'Processing your paper...');
    } catch (error) {
      toast.error('Failed to start workflow', 'Please try again');
      console.error('Start error:', error);
    }
  };

  const handleFileUploaded = (_fileId: string, path: string) => {
    setUploadedFilePath(path);
  };

  const handleUrlSubmit = (url: string) => {
    handleStart(url, 'url');
  };

  const handleStartWithFile = () => {
    if (uploadedFilePath) {
      handleStart(uploadedFilePath, 'file');
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
        <h1 className="text-2xl font-bold text-gray-900">Paper to Code</h1>
        <p className="text-gray-500 mt-1">
          Upload a research paper and convert it to a working implementation
        </p>
      </motion.div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Left Column - Input */}
        <div className="space-y-6">
          <Card>
            <h3 className="font-semibold text-gray-900 mb-4">Input Source</h3>

            {/* Input Method Tabs */}
            <div className="flex space-x-2 mb-4">
              <button
                onClick={() => setInputMethod('file')}
                className={`flex-1 px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
                  inputMethod === 'file'
                    ? 'bg-primary-50 text-primary-600'
                    : 'text-gray-600 hover:bg-gray-50'
                }`}
              >
                Upload PDF
              </button>
              <button
                onClick={() => setInputMethod('url')}
                className={`flex-1 px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
                  inputMethod === 'url'
                    ? 'bg-primary-50 text-primary-600'
                    : 'text-gray-600 hover:bg-gray-50'
                }`}
              >
                URL Link
              </button>
            </div>

            {/* Input Components */}
            {inputMethod === 'file' ? (
              <div className="space-y-4">
                <FileUploader onFileUploaded={handleFileUploaded} disabled={isRunning} />
                {uploadedFilePath && !isRunning && (
                  <Button
                    onClick={handleStartWithFile}
                    isLoading={isRunning}
                    className="w-full"
                  >
                    Start Processing
                  </Button>
                )}
              </div>
            ) : (
              <UrlInput onSubmit={handleUrlSubmit} isLoading={isRunning} disabled={isRunning} />
            )}

            {/* Cancel Button */}
            {isRunning && (
              <div className="mt-4">
                <button
                  onClick={() => setShowCancelDialog(true)}
                  disabled={isCancelling}
                  className="w-full flex items-center justify-center space-x-2 px-4 py-2 text-sm font-medium text-red-600 bg-red-50 border border-red-200 rounded-lg hover:bg-red-100 transition-colors disabled:opacity-50"
                >
                  <StopCircle className="h-4 w-4" />
                  <span>Cancel Task</span>
                </button>
              </div>
            )}

            {/* Options */}
            <div className="mt-6 pt-4 border-t border-gray-100">
              <label className="flex items-center space-x-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={enableIndexing}
                  onChange={(e) => setEnableIndexing(e.target.checked)}
                  className="w-4 h-4 text-primary-600 rounded focus:ring-primary-500"
                />
                <span className="text-sm text-gray-700">
                  Enable code indexing
                </span>
              </label>
              <p className="text-xs text-gray-400 mt-1 ml-7">
                Improves code quality but takes longer
              </p>
            </div>
          </Card>
        </div>

        {/* Right Column - Progress & Results */}
        <div className="space-y-6">
          {/* Progress */}
          {status !== 'idle' && (
            <Card>
              <ProgressTracker steps={steps} currentProgress={progress} />
            </Card>
          )}

          {/* User-in-Loop Interaction Panel */}
          <AnimatePresence>
            {pendingInteraction && activeTaskId && (
              <InteractionPanel
                taskId={activeTaskId}
                interaction={pendingInteraction}
              />
            )}
          </AnimatePresence>

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
                      Your code has been successfully generated from the paper.
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
                      Processing Failed
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
