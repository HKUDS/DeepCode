import { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import { CheckCircle, Circle, Loader2 } from 'lucide-react';
import { motion } from 'framer-motion';

interface WorkflowNodeData {
  id: string;
  title: string;
  subtitle: string;
  isActive: boolean;
  isCompleted: boolean;
  onClick?: () => void;
}

function WorkflowNode({ data }: NodeProps<WorkflowNodeData>) {
  const { title, subtitle, isActive, isCompleted, onClick } = data;

  return (
    <>
      <Handle
        type="target"
        position={Position.Left}
        className="!bg-gray-300 !border-2 !border-white !w-3 !h-3"
      />

      <motion.div
        initial={{ scale: 0.9, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        onClick={onClick}
        className={`px-4 py-3 rounded-xl border-2 cursor-pointer transition-all min-w-[140px] ${
          isCompleted
            ? 'bg-green-50 border-green-300 shadow-green-100'
            : isActive
            ? 'bg-primary-50 border-primary-400 shadow-primary-100 shadow-lg'
            : 'bg-white border-gray-200 hover:border-gray-300'
        }`}
      >
        <div className="flex items-center space-x-2 mb-1">
          {isCompleted ? (
            <CheckCircle className="h-4 w-4 text-green-500" />
          ) : isActive ? (
            <Loader2 className="h-4 w-4 text-primary-500 animate-spin" />
          ) : (
            <Circle className="h-4 w-4 text-gray-300" />
          )}
          <span
            className={`text-sm font-semibold ${
              isCompleted
                ? 'text-green-700'
                : isActive
                ? 'text-primary-700'
                : 'text-gray-700'
            }`}
          >
            {title}
          </span>
        </div>
        <p
          className={`text-xs ${
            isCompleted
              ? 'text-green-600'
              : isActive
              ? 'text-primary-600'
              : 'text-gray-400'
          }`}
        >
          {subtitle}
        </p>

        {isActive && (
          <motion.div
            layoutId="activeIndicator"
            className="absolute -bottom-1 left-1/2 transform -translate-x-1/2 w-2 h-2 bg-primary-500 rounded-full"
            animate={{ scale: [1, 1.2, 1] }}
            transition={{ repeat: Infinity, duration: 1.5 }}
          />
        )}
      </motion.div>

      <Handle
        type="source"
        position={Position.Right}
        className="!bg-gray-300 !border-2 !border-white !w-3 !h-3"
      />
    </>
  );
}

export default memo(WorkflowNode);
