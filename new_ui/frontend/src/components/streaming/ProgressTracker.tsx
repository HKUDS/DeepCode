import { motion } from 'framer-motion';
import { CheckCircle, Circle, Loader2, XCircle } from 'lucide-react';
import type { WorkflowStep } from '../../types/workflow';

interface ProgressTrackerProps {
  steps: WorkflowStep[];
  currentProgress: number;
}

export default function ProgressTracker({
  steps,
  currentProgress,
}: ProgressTrackerProps) {
  const getStepIcon = (status: WorkflowStep['status']) => {
    switch (status) {
      case 'completed':
        return <CheckCircle className="h-5 w-5 text-green-500" />;
      case 'active':
        return <Loader2 className="h-5 w-5 text-primary-500 animate-spin" />;
      case 'error':
        return <XCircle className="h-5 w-5 text-red-500" />;
      default:
        return <Circle className="h-5 w-5 text-gray-300" />;
    }
  };

  return (
    <div className="w-full">
      {/* Progress bar */}
      <div className="mb-6">
        <div className="flex justify-between text-sm mb-2">
          <span className="font-medium text-gray-700">Progress</span>
          <span className="text-gray-500">{currentProgress}%</span>
        </div>
        <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
          <motion.div
            className="h-full bg-primary-500 rounded-full"
            initial={{ width: 0 }}
            animate={{ width: `${currentProgress}%` }}
            transition={{ duration: 0.5, ease: 'easeOut' }}
          />
        </div>
      </div>

      {/* Steps */}
      <div className="space-y-3">
        {steps.map((step, index) => (
          <motion.div
            key={step.id}
            initial={{ opacity: 0, x: -10 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: index * 0.1 }}
            className={`flex items-center space-x-3 p-3 rounded-lg transition-colors ${
              step.status === 'active'
                ? 'bg-primary-50 border border-primary-200'
                : step.status === 'completed'
                ? 'bg-green-50 border border-green-100'
                : step.status === 'error'
                ? 'bg-red-50 border border-red-100'
                : 'bg-gray-50'
            }`}
          >
            {getStepIcon(step.status)}
            <div className="flex-1 min-w-0">
              <p
                className={`text-sm font-medium ${
                  step.status === 'active'
                    ? 'text-primary-700'
                    : step.status === 'completed'
                    ? 'text-green-700'
                    : step.status === 'error'
                    ? 'text-red-700'
                    : 'text-gray-500'
                }`}
              >
                {step.title}
              </p>
              <p className="text-xs text-gray-400">{step.subtitle}</p>
            </div>
            {step.status === 'completed' && (
              <span className="text-xs text-green-600 font-medium">Done</span>
            )}
          </motion.div>
        ))}
      </div>
    </div>
  );
}
