/**
 * InteractionPanel Component
 *
 * Displays User-in-Loop interactions from the workflow.
 * Supports different interaction types:
 * - requirement_questions: Show questions and collect answers
 * - plan_review: Show plan and allow confirm/modify/cancel
 */

import { useState, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  MessageCircle,
  Send,
  SkipForward,
  CheckCircle,
  XCircle,
  Edit,
  HelpCircle,
  Loader2
} from 'lucide-react';
import { Button, Card } from '../common';
import { useWorkflowStore, type PendingInteraction } from '../../stores/workflowStore';
import { workflowsApi } from '../../services/api';
import { toast } from '../common/Toaster';

interface InteractionPanelProps {
  taskId: string;
  interaction: PendingInteraction;
  onComplete?: () => void;
}

export default function InteractionPanel({
  taskId,
  interaction,
  onComplete
}: InteractionPanelProps) {
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

  const renderQuestions = () => {
    const questions = interaction.data.questions || [];

    return (
      <div className="space-y-4">
        {questions.map((q, index) => (
          <motion.div
            key={q.id || index}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: index * 0.1 }}
            className="bg-gray-50 rounded-lg p-4"
          >
            <div className="flex items-start space-x-3">
              <div className="flex-shrink-0 w-6 h-6 rounded-full bg-primary-100 flex items-center justify-center">
                <span className="text-xs font-semibold text-primary-600">{index + 1}</span>
              </div>
              <div className="flex-1">
                <p className="text-sm font-medium text-gray-900">{q.question}</p>
                {q.hint && (
                  <p className="text-xs text-gray-500 mt-1 flex items-center">
                    <HelpCircle className="h-3 w-3 mr-1" />
                    {q.hint}
                  </p>
                )}
                <textarea
                  className="mt-2 w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent resize-none"
                  rows={2}
                  placeholder="Your answer..."
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

        <div className="flex justify-end space-x-3 pt-4 border-t border-gray-100">
          {!interaction.required && (
            <Button
              variant="secondary"
              onClick={handleSkip}
              disabled={isSubmitting}
            >
              <SkipForward className="h-4 w-4 mr-2" />
              Skip
            </Button>
          )}
          <Button
            variant="primary"
            onClick={() => handleSubmit('submit', { answers })}
            disabled={isSubmitting}
          >
            {isSubmitting ? (
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
            ) : (
              <Send className="h-4 w-4 mr-2" />
            )}
            Submit Answers
          </Button>
        </div>
      </div>
    );
  };

  const renderPlanReview = () => {
    const plan = interaction.data.plan || interaction.data.plan_preview || '';

    return (
      <div className="space-y-4">
        <div className="bg-gray-900 rounded-lg p-4 max-h-80 overflow-y-auto">
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
                className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent resize-none"
                rows={3}
                placeholder="Describe the changes you'd like to make..."
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
                disabled={isSubmitting}
              />
            </motion.div>
          )}
        </AnimatePresence>

        <div className="flex justify-end space-x-3 pt-4 border-t border-gray-100">
          <Button
            variant="danger"
            onClick={() => handleSubmit('cancel', { reason: 'User cancelled' })}
            disabled={isSubmitting}
          >
            <XCircle className="h-4 w-4 mr-2" />
            Cancel
          </Button>

          {!showModify ? (
            <Button
              variant="secondary"
              onClick={() => setShowModify(true)}
              disabled={isSubmitting}
            >
              <Edit className="h-4 w-4 mr-2" />
              Modify
            </Button>
          ) : (
            <Button
              variant="secondary"
              onClick={() => {
                if (feedback.trim()) {
                  handleSubmit('modify', { feedback });
                } else {
                  toast.warning('Please provide feedback', 'Describe what you want to change');
                }
              }}
              disabled={isSubmitting || !feedback.trim()}
            >
              <Send className="h-4 w-4 mr-2" />
              Submit Changes
            </Button>
          )}

          <Button
            variant="primary"
            onClick={() => handleSubmit('confirm')}
            disabled={isSubmitting}
          >
            {isSubmitting ? (
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
            ) : (
              <CheckCircle className="h-4 w-4 mr-2" />
            )}
            Approve & Continue
          </Button>
        </div>
      </div>
    );
  };

  const renderGenericInteraction = () => {
    return (
      <div className="space-y-4">
        <p className="text-sm text-gray-600">{interaction.description}</p>

        <div className="flex justify-end space-x-3 pt-4 border-t border-gray-100">
          {Object.entries(interaction.options).map(([action, label]) => (
            <Button
              key={action}
              variant={action === 'confirm' || action === 'submit' ? 'primary' : 'secondary'}
              onClick={() => handleSubmit(action)}
              disabled={isSubmitting}
            >
              {isSubmitting ? (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              ) : null}
              {label}
            </Button>
          ))}
        </div>
      </div>
    );
  };

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.95 }}
    >
      <Card className="border-2 border-primary-200 bg-primary-50/30">
        <div className="flex items-center space-x-3 mb-4">
          <div className="p-2 bg-primary-100 rounded-lg">
            <MessageCircle className="h-5 w-5 text-primary-600" />
          </div>
          <div>
            <h3 className="font-semibold text-gray-900">{interaction.title}</h3>
            <p className="text-sm text-gray-500">{interaction.description}</p>
          </div>
        </div>

        {renderContent()}
      </Card>
    </motion.div>
  );
}
