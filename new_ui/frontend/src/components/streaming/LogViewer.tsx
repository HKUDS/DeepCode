import { useEffect, useRef, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Terminal, Trash2, Filter } from 'lucide-react';

interface LogEntry {
  id: string;
  level: 'INFO' | 'WARNING' | 'ERROR' | 'DEBUG';
  message: string;
  namespace: string;
  timestamp: string;
}

interface LogViewerProps {
  logs: LogEntry[];
  maxHeight?: number;
  onClear?: () => void;
}

const levelColors = {
  INFO: 'text-blue-600 bg-blue-50',
  WARNING: 'text-yellow-600 bg-yellow-50',
  ERROR: 'text-red-600 bg-red-50',
  DEBUG: 'text-gray-600 bg-gray-50',
};

export default function LogViewer({
  logs,
  maxHeight = 400,
  onClear,
}: LogViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [filter, setFilter] = useState<string | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);

  // Auto-scroll to bottom when new logs arrive
  useEffect(() => {
    if (autoScroll && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [logs, autoScroll]);

  const filteredLogs = filter
    ? logs.filter((log) => log.level === filter)
    : logs;

  const formatTime = (timestamp: string) => {
    try {
      const date = new Date(timestamp);
      return date.toLocaleTimeString('en-US', {
        hour12: false,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      });
    } catch {
      return timestamp.slice(-8);
    }
  };

  return (
    <div className="rounded-lg border border-gray-200 bg-white overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 bg-gray-50 border-b border-gray-200">
        <div className="flex items-center space-x-2">
          <Terminal className="h-4 w-4 text-gray-500" />
          <span className="text-sm font-medium text-gray-700">Logs</span>
          <span className="text-xs text-gray-400">({filteredLogs.length})</span>
        </div>

        <div className="flex items-center space-x-2">
          {/* Filter dropdown */}
          <div className="relative">
            <select
              value={filter || ''}
              onChange={(e) => setFilter(e.target.value || null)}
              className="text-xs pl-6 pr-2 py-1 border border-gray-200 rounded bg-white focus:outline-none focus:ring-1 focus:ring-primary-500"
            >
              <option value="">All levels</option>
              <option value="INFO">INFO</option>
              <option value="WARNING">WARNING</option>
              <option value="ERROR">ERROR</option>
              <option value="DEBUG">DEBUG</option>
            </select>
            <Filter className="absolute left-2 top-1/2 -translate-y-1/2 h-3 w-3 text-gray-400" />
          </div>

          {/* Clear button */}
          {onClear && (
            <button
              onClick={onClear}
              className="p-1 text-gray-400 hover:text-gray-600 transition-colors"
              title="Clear logs"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>

      {/* Log content */}
      <div
        ref={containerRef}
        className="overflow-y-auto font-mono text-xs"
        style={{ maxHeight }}
        onScroll={(e) => {
          const target = e.target as HTMLDivElement;
          const isAtBottom =
            target.scrollHeight - target.scrollTop === target.clientHeight;
          setAutoScroll(isAtBottom);
        }}
      >
        {filteredLogs.length === 0 ? (
          <div className="p-8 text-center text-gray-400">
            No logs to display
          </div>
        ) : (
          <div className="p-2 space-y-1">
            <AnimatePresence initial={false}>
              {filteredLogs.map((log) => (
                <motion.div
                  key={log.id}
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  className="flex items-start space-x-2 py-1 px-2 rounded hover:bg-gray-50"
                >
                  <span className="text-gray-400 flex-shrink-0">
                    {formatTime(log.timestamp)}
                  </span>
                  <span
                    className={`px-1.5 py-0.5 rounded text-xs font-medium flex-shrink-0 ${
                      levelColors[log.level]
                    }`}
                  >
                    {log.level}
                  </span>
                  {log.namespace && (
                    <span className="text-primary-600 flex-shrink-0">
                      [{log.namespace}]
                    </span>
                  )}
                  <span className="text-gray-700 break-all">{log.message}</span>
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
        )}
      </div>
    </div>
  );
}
