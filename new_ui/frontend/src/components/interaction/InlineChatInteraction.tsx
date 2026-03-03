/**
 * InlineChatInteraction Component
 *
 * Displays User-in-Loop interactions inline within the chat flow.
 * Designed to look like an AI assistant message with interactive elements.
 */

import { useState, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Send,
  SkipForward,
  CheckCircle,
  XCircle,
  Edit,
  HelpCircle,
  Loader2,
  Bot
} from 'lucide-react';
import { Button } from '../common';
import { useWorkflowStore, type PendingInteraction } from '../../stores/workflowStore';
import { workflowsApi } from '../../services/api';
import { toast } from '../common/Toaster';

interface InlineChatInteractionProps {
  taskId: string;
  interaction: PendingInteraction;
  onComplete?: () => void;
}

export default function InlineChatInteraction({
  taskId,
  interaction,
  onComplete
}: InlineChatInteractionProps) {
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [feedback, setFeedback] = useState('');
  const [showModify, setShowModify] = useState(false);
  const { clearInteraction, addActivityLog } = useWorkflowStore();

  const handleSubmit = useCallback(async (action: string, data: Record<string, unknown> = {}) => {
    setIsSubmitting(true);
    try {
      await workflowsApi.respondToInteraction(taskId, action, data, false);
      addActivityLog(`✓ Submitted: ${action}`, 0, 'success');
      clearInteraction();
      onComplete?.();
    } catch (error) {
      console.error('Failed to submit response:', error);
      toast.error('Failed to submit', 'Please try again');
    } finally {
      setIsSubmitting(false);
    }
  }, [taskId, clearInteraction, addActivityLog, onComplete]);

  const handleSkip = useCallback(async () => {
    setIsSubmitting(true);
    try {
      await workflowsApi.respondToInteraction(taskId, 'skip', {}, true);
      addActivityLog('⏭️ Skipped interaction', 0, 'info');
      clearInteraction();
      onComplete?.();
    } catch (error) {
      console.error('Failed to skip:', error);
      toast.error('Failed to skip', 'Please try again');
    } finally {
      setIsSubmitting(false);
    }
  }, [taskId, clearInteraction, addActivityLog, onComplete]);

  // Render questions type
  const renderQuestions = () => {
    const questions = interaction.data?.questions || [];

    return (
      <div className="space-y-3">
        {questions.map((q: { id?: string; question: string; hint?: string; category?: string }, index: number) => (
          <motion.div
            key={q.id || index}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: index * 0.1 }}
            className="bg-white rounded-lg p-3 border border-gray-200 shadow-sm"
          >
            <div className="flex items-start space-x-2">
              <div className="flex-shrink-0 w-5 h-5 rounded-full bg-primary-100 flex items-center justify-center mt-0.5">
                <span className="text-xs font-semibold text-primary-600">{index + 1}</span>
              </div>
              <div className="flex-1 min-w-0">
                {q.category && (
                  <span className="inline-block px-2 py-0.5 text-xs font-medium text-primary-700 bg-primary-50 rounded mb-1">
                    {q.category}
                  </span>
                )}
                <p className="text-sm font-medium text-gray-900">{q.question}</p>
                {q.hint && (
                  <p className="text-xs text-gray-500 mt-1 flex items-center">
                    <HelpCircle className="h-3 w-3 mr-1 flex-shrink-0" />
                    <span>{q.hint}</span>
                  </p>
                )}
                <textarea
                  className="mt-2 w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent resize-none bg-gray-50"
                  rows={2}
                  placeholder="Type your answer here..."
                  value={answers[q.id || `q${index}`] || ''}
                  onChange={(e) => setAnswers(prev => ({
                    ...prev,
                    [q.id || `q${index}`]: e.target.value
                  }))}
                  disabled={isSubmitting}
                />
              </div>
            </div>
          </motion.div>
        ))}

        <div className="flex justify-end space-x-2 pt-3">
          {!interaction.required && (
            <Button
              variant="secondary"
              size="sm"
              onClick={handleSkip}
              disabled={isSubmitting}
            >
              <SkipForward className="h-3.5 w-3.5 mr-1.5" />
              Skip
            </Button>
          )}
          <Button
            variant="primary"
            size="sm"
            onClick={() => handleSubmit('submit', { answers })}
            disabled={isSubmitting}
          >
            {isSubmitting ? (
              <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
            ) : (
              <Send className="h-3.5 w-3.5 mr-1.5" />
            )}
            Submit Answers
          </Button>
        </div>
      </div>
    );
  };

  // Render plan review type
  const renderPlanReview = () => {
    const plan = interaction.data?.plan || interaction.data?.plan_preview || '';

    return (
      <div className="space-y-3">
        <div className="bg-gray-900 rounded-lg p-3 max-h-60 overflow-y-auto">
          <pre className="text-xs text-gray-300 font-mono whitespace-pre-wrap">
            {plan}
          </pre>
        </div>

        <AnimatePresence>
          {showModify && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
            >
              <textarea
                className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent resize-none bg-gray-50"
                rows={3}
                placeholder="Describe the changes you'd like to make..."
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
                disabled={isSubmitting}
              />
            </motion.div>
          )}
        </AnimatePresence>

        <div className="flex flex-wrap justify-end gap-2 pt-3">
          <Button
            variant="danger"
            size="sm"
            onClick={() => handleSubmit('cancel', { reason: 'User cancelled' })}
            disabled={isSubmitting}
          >
            <XCircle className="h-3.5 w-3.5 mr-1.5" />
            Cancel
          </Button>

          {!showModify ? (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setShowModify(true)}
              disabled={isSubmitting}
            >
              <Edit className="h-3.5 w-3.5 mr-1.5" />
              Modify
            </Button>
          ) : (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                if (feedback.trim()) {
                  handleSubmit('modify', { feedback });
                } else {
                  toast.warning('Please provide feedback', 'Describe what you want to change');
                }
              }}
              disabled={isSubmitting || !feedback.trim()}
            >
              <Send className="h-3.5 w-3.5 mr-1.5" />
              Submit Changes
            </Button>
          )}

          <Button
            variant="primary"
            size="sm"
            onClick={() => handleSubmit('confirm')}
            disabled={isSubmitting}
          >
            {isSubmitting ? (
              <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
            ) : (
              <CheckCircle className="h-3.5 w-3.5 mr-1.5" />
            )}
            Approve
          </Button>
        </div>
      </div>
    );
  };

  // Render generic interaction type
  const renderGenericInteraction = () => {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-600">{interaction.description}</p>

        <div className="flex flex-wrap justify-end gap-2 pt-3">
          {interaction.options && Object.entries(interaction.options).map(([action, label]) => (
            <Button
              key={action}
              variant={action === 'confirm' || action === 'submit' ? 'primary' : 'secondary'}
              size="sm"
              onClick={() => handleSubmit(action)}
              disabled={isSubmitting}
            >
              {isSubmitting ? (
                <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
              ) : null}
              {label as string}
            </Button>
          ))}
        </div>
      </div>
    );
  };

  // Render based on interaction type
  const renderContent = () => {
    switch (interaction.type) {
      case 'requirement_questions':
        return renderQuestions();
      case 'plan_review':
        return renderPlanReview();
      default:
        return renderGenericInteraction();
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="flex items-start space-x-3"
    >
      {/* Bot Avatar */}
      <div className="flex-shrink-0 w-8 h-8 rounded-full bg-primary-100 flex items-center justify-center">
        <Bot className="h-4 w-4 text-primary-600" />
      </div>

      {/* Interaction Content */}
      <div className="flex-1 max-w-[90%]">
        <div className="bg-gradient-to-br from-primary-50 to-blue-50 border border-primary-200 rounded-2xl px-4 py-3 shadow-sm">
          {/* Title */}
          <div className="mb-3">
            <h4 className="font-semibold text-gray-900 text-sm">{interaction.title}</h4>
            {interaction.description && interaction.type !== 'requirement_questions' && (
              <p className="text-xs text-gray-600 mt-0.5">{interaction.description}</p>
            )}
          </div>

          {/* Content */}
          {renderContent()}
        </div>
      </div>
    </motion.div>
  );
}
