/**
 * Activity Log Viewer
 *
 * Displays real-time activity logs from the backend workflow.
 * Shows progress messages, timestamps, and status icons.
 */

import { useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Terminal,
  CheckCircle,
  Clock,
  Loader2,
  AlertCircle,
  Rocket,
  Brain,
  Code,
  FolderOpen,
  FileText,
  Zap
} from 'lucide-react';

interface LogEntry {
  id: string;
  timestamp: Date;
  message: string;
  progress: number;
  type: 'info' | 'success' | 'warning' | 'error' | 'progress';
}

interface ActivityLogViewerProps {
  logs: LogEntry[];
  isRunning: boolean;
  currentMessage?: string;
}

// Map message content to appropriate icon
function getIconForMessage(message: string): React.ReactNode {
  const msg = message.toLowerCase();

  if (msg.includes('complete') || msg.includes('success') || msg.includes('✅')) {
    return <CheckCircle className="h-4 w-4 text-green-500" />;
  }
  if (msg.includes('error') || msg.includes('failed') || msg.includes('❌')) {
    return <AlertCircle className="h-4 w-4 text-red-500" />;
  }
  if (msg.includes('initializ') || msg.includes('🚀') || msg.includes('starting')) {
    return <Rocket className="h-4 w-4 text-blue-500" />;
  }
  if (msg.includes('analyz') || msg.includes('🧠') || msg.includes('brain') || msg.includes('intelligence')) {
    return <Brain className="h-4 w-4 text-purple-500" />;
  }
  if (msg.includes('code') || msg.includes('implement') || msg.includes('🔬') || msg.includes('synthesi')) {
    return <Code className="h-4 w-4 text-orange-500" />;
  }
  if (msg.includes('workspace') || msg.includes('directory') || msg.includes('📁') || msg.includes('🏗️')) {
    return <FolderOpen className="h-4 w-4 text-yellow-600" />;
  }
  if (msg.includes('plan') || msg.includes('📝') || msg.includes('document') || msg.includes('📄')) {
    return <FileText className="h-4 w-4 text-cyan-500" />;
  }
  if (msg.includes('process') || msg.includes('⚡') || msg.includes('running')) {
    return <Zap className="h-4 w-4 text-amber-500" />;
  }

  return <Clock className="h-4 w-4 text-gray-400" />;
}

function formatTime(date: Date): string {
  return date.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  });
}

export default function ActivityLogViewer({
  logs,
  isRunning,
  currentMessage,
}: ActivityLogViewerProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom when new logs arrive
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs]);

  return (
    <div className="rounded-xl border border-gray-200 bg-gray-900 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 bg-gray-800 border-b border-gray-700">
        <div className="flex items-center space-x-2">
          <Terminal className="h-4 w-4 text-green-400" />
          <span className="text-sm font-medium text-gray-200">
            Activity Log
          </span>
          {isRunning && (
            <motion.span
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="flex items-center text-xs text-green-400"
            >
              <Loader2 className="h-3 w-3 mr-1 animate-spin" />
              Live
            </motion.span>
          )}
        </div>

        <div className="text-xs text-gray-500">
          {logs.length} events
        </div>
      </div>

      {/* Log Content */}
      <div
        ref={scrollRef}
        className="h-[350px] overflow-y-auto p-4 font-mono text-sm"
      >
        {logs.length === 0 && !isRunning ? (
          <div className="h-full flex items-center justify-center text-gray-500">
            <div className="text-center">
              <Terminal className="h-12 w-12 mx-auto mb-3 opacity-50" />
              <p className="text-sm">Activity logs will appear here</p>
              <p className="text-xs text-gray-600 mt-1">Start a workflow to see real-time progress</p>
            </div>
          </div>
        ) : (
          <AnimatePresence mode="popLayout">
            {logs.map((log, index) => (
              <motion.div
                key={log.id}
                initial={{ opacity: 0, x: -20 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.2 }}
                className="flex items-start space-x-3 py-2 border-b border-gray-800 last:border-0"
              >
                {/* Timestamp */}
                <span className="text-gray-500 text-xs whitespace-nowrap pt-0.5">
                  {formatTime(log.timestamp)}
                </span>

                {/* Icon */}
                <span className="flex-shrink-0 pt-0.5">
                  {getIconForMessage(log.message)}
                </span>

                {/* Message */}
                <span className="text-gray-300 flex-1 break-words">
                  {log.message}
                </span>

                {/* Progress Badge */}
                <span className="text-xs text-gray-500 whitespace-nowrap pt-0.5">
                  {log.progress}%
                </span>
              </motion.div>
            ))}

            {/* Current Activity Indicator */}
            {isRunning && currentMessage && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="flex items-start space-x-3 py-2 bg-gray-800/50 rounded-lg mt-2 px-2"
              >
                <span className="text-green-400 text-xs whitespace-nowrap pt-0.5">
                  {formatTime(new Date())}
                </span>
                <Loader2 className="h-4 w-4 text-green-400 animate-spin flex-shrink-0" />
                <span className="text-green-400 flex-1">
                  {currentMessage}
                </span>
              </motion.div>
            )}
          </AnimatePresence>
        )}
      </div>

      {/* Footer Status Bar */}
      <div className="flex items-center justify-between px-4 py-2 bg-gray-800 border-t border-gray-700 text-xs text-gray-500">
        <span>
          {isRunning ? (
            <span className="text-green-400">● Connected</span>
          ) : logs.length > 0 ? (
            <span className="text-gray-400">● Completed</span>
          ) : (
            <span className="text-gray-500">○ Idle</span>
          )}
        </span>
        {logs.length > 0 && (
          <span>
            Last update: {formatTime(logs[logs.length - 1]?.timestamp || new Date())}
          </span>
        )}
      </div>
    </div>
  );
}
