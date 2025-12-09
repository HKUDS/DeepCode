"use client";

import React, { useEffect, useState, useRef } from "react";
import { Terminal, Clock, Cpu, Activity } from "lucide-react";

interface LogEntry {
  timestamp: string;
  agent: string;
  message: string;
  level: string;
}

interface AgentLogViewerProps {
  taskId?: string | null;
}

export default function AgentLogViewer({ taskId }: AgentLogViewerProps) {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!taskId) {
      setLogs([]);
      setLoading(false);
      return;
    }

    setLoading(true);
    const fetchLogs = async () => {
      try {
        const response = await fetch(`http://localhost:8000/api/logs/agents?task_id=${taskId}`);
        if (response.ok) {
          const data = await response.json();
          setLogs(data.logs);
        }
      } catch (error) {
        console.error("Failed to fetch logs:", error);
      } finally {
        setLoading(false);
      }
    };

    fetchLogs();
    const interval = setInterval(fetchLogs, 2000); // Poll every 2 seconds

    return () => clearInterval(interval);
  }, [taskId]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs]);

  return (
    <div className="bg-white rounded-xl overflow-hidden flex flex-col h-full min-h-[600px] shadow-soft border border-border">
      <div className="px-4 py-3 bg-surface-highlight border-b border-border flex justify-between items-center">
        <div className="flex items-center gap-2 font-medium text-sm text-text-main">
          <Terminal size={16} className="text-primary" />
          <span className="tracking-wide">Agent Telemetry Stream</span>
        </div>
        <div className="flex items-center gap-2 text-xs text-text-muted bg-white px-2 py-1 rounded-full border border-border shadow-sm">
          <Activity size={12} className="text-success animate-pulse" />
          <span className="font-mono">LIVE UPLINK</span>
        </div>
      </div>

      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto p-0 font-mono text-xs bg-[#0f172a] text-slate-300"
      >
        {!taskId ? (
          <div className="flex flex-col items-center justify-center h-full text-slate-500 gap-3">
            <div className="p-3 bg-slate-800/50 rounded-full">
              <Terminal size={24} className="text-slate-600" />
            </div>
            <span>Waiting for task initialization...</span>
          </div>
        ) : loading && logs.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-slate-500 gap-3">
            <Loader2 size={24} className="animate-spin text-primary" />
            <span>Establishing secure connection...</span>
          </div>
        ) : (
          logs.map((log, index) => (
            <div
              key={index}
              className="flex gap-4 px-4 py-2.5 border-b border-slate-800 last:border-0 hover:bg-slate-800/50 transition-colors group"
            >
              <div className="text-slate-500 min-w-[100px] flex items-center gap-1.5 opacity-60 group-hover:opacity-100 transition-opacity">
                <Clock size={10} />
                {log.timestamp}
              </div>
              <div className="text-violet-400 min-w-[120px] font-medium flex items-center gap-1.5">
                <Cpu size={10} />
                {log.agent}
              </div>
              <div className="text-slate-300 whitespace-pre-wrap flex-1 leading-relaxed">
                {log.message}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function Loader2({ size, className }: { size: number; className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
    </svg>
  );
}