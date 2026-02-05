import { motion } from 'framer-motion';
import { Card } from '../components/common';
import { WorkflowCanvas } from '../components/workflow';
import { PAPER_TO_CODE_STEPS, CHAT_PLANNING_STEPS } from '../types/workflow';
import { useState } from 'react';

export default function WorkflowEditorPage() {
  const [selectedWorkflow, setSelectedWorkflow] = useState<'paper' | 'chat'>('paper');
  const [currentStep, setCurrentStep] = useState(2); // Demo: step 2 is active

  const steps = selectedWorkflow === 'paper' ? PAPER_TO_CODE_STEPS : CHAT_PLANNING_STEPS;

  return (
    <div className="space-y-6">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
      >
        <h1 className="text-2xl font-bold text-gray-900">Workflow Editor</h1>
        <p className="text-gray-500 mt-1">
          Visualize and customize your code generation workflows
        </p>
      </motion.div>

      {/* Workflow Selection */}
      <Card>
        <div className="flex items-center space-x-4 mb-6">
          <span className="text-sm font-medium text-gray-700">Workflow:</span>
          <div className="flex space-x-2">
            <button
              onClick={() => setSelectedWorkflow('paper')}
              className={`px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
                selectedWorkflow === 'paper'
                  ? 'bg-primary-50 text-primary-600'
                  : 'text-gray-600 hover:bg-gray-50'
              }`}
            >
              Paper to Code
            </button>
            <button
              onClick={() => setSelectedWorkflow('chat')}
              className={`px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
                selectedWorkflow === 'chat'
                  ? 'bg-primary-50 text-primary-600'
                  : 'text-gray-600 hover:bg-gray-50'
              }`}
            >
              Chat Planning
            </button>
          </div>
        </div>

        {/* Step Selector for Demo */}
        <div className="flex items-center space-x-4 mb-6">
          <span className="text-sm font-medium text-gray-700">Current Step:</span>
          <input
            type="range"
            min="0"
            max={steps.length - 1}
            value={currentStep}
            onChange={(e) => setCurrentStep(parseInt(e.target.value))}
            className="w-48"
          />
          <span className="text-sm text-gray-500">
            {steps[currentStep]?.title || 'N/A'}
          </span>
        </div>

        <WorkflowCanvas
          steps={steps}
          currentStepIndex={currentStep}
          onStepClick={(stepId) => {
            const index = steps.findIndex((s) => s.id === stepId);
            if (index !== -1) setCurrentStep(index);
          }}
        />
      </Card>

      {/* Info */}
      <Card>
        <h3 className="font-semibold text-gray-900 mb-4">About This View</h3>
        <p className="text-sm text-gray-600">
          The workflow editor allows you to visualize the code generation pipeline.
          Each node represents a step in the process, and the connections show the
          data flow between steps. Use this view to understand how DeepCode processes
          your inputs and generates code.
        </p>
        <ul className="mt-4 space-y-2 text-sm text-gray-600">
          <li className="flex items-center space-x-2">
            <span className="w-3 h-3 rounded-full bg-gray-300"></span>
            <span>Pending steps</span>
          </li>
          <li className="flex items-center space-x-2">
            <span className="w-3 h-3 rounded-full bg-primary-500"></span>
            <span>Active step</span>
          </li>
          <li className="flex items-center space-x-2">
            <span className="w-3 h-3 rounded-full bg-green-500"></span>
            <span>Completed steps</span>
          </li>
        </ul>
      </Card>
    </div>
  );
}
