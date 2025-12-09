"use client";

import React, { useState, useEffect } from "react";
import Header from "../components/Header";
import FeatureCards from "../components/FeatureCards";
import AgentLogViewer from "../components/AgentLogViewer";
import WorkflowMonitor from "../components/WorkflowMonitor";
import { Play, FileText, Link as LinkIcon, Terminal, ArrowRight } from "lucide-react";

interface WorkflowStep {
  title: string;
  subtitle: string;
  state: "pending" | "active" | "completed" | "error";
}

const INITIAL_STEPS: WorkflowStep[] = [
  { title: "RESEARCH", subtitle: "Analysis", state: "pending" },
  { title: "WORKSPACE", subtitle: "Setup", state: "pending" },
  { title: "ARCHITECT", subtitle: "Design", state: "pending" },
  { title: "REFERENCE", subtitle: "Discovery", state: "pending" },
  { title: "REPO", subtitle: "Acquisition", state: "pending" },
  { title: "CODEBASE", subtitle: "Intelligence", state: "pending" },
  { title: "IMPLEMENT", subtitle: "Synthesis", state: "pending" },
];

export default function Home() {
  const [activeTab, setActiveTab] = useState<"pdf" | "url" | "command">("pdf");
  const [inputSource, setInputSource] = useState("");
  const [isProcessing, setIsProcessing] = useState(false);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [steps, setSteps] = useState<WorkflowStep[]>(INITIAL_STEPS);
  const [statusMessage, setStatusMessage] = useState("");

  const startWorkflow = async () => {
    if (!inputSource) return;

    setIsProcessing(true);
    setStatusMessage("Initializing workflow...");
    
    try {
      const response = await fetch("http://localhost:8000/api/workflow/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          input_source: inputSource,
          input_type: activeTab === "pdf" ? "file" : activeTab === "url" ? "url" : "chat",
          enable_indexing: true,
        }),
      });

      if (response.ok) {
        const data = await response.json();
        setTaskId(data.task_id);
      } else {
        setStatusMessage("Failed to start workflow.");
        setIsProcessing(false);
      }
    } catch (error) {
      console.error("Error starting workflow:", error);
      setStatusMessage("Connection error.");
      setIsProcessing(false);
    }
  };

  useEffect(() => {
    if (!taskId) return;

    const interval = setInterval(async () => {
      try {
        const response = await fetch(`http://localhost:8000/api/workflow/${taskId}`);
        if (response.ok) {
          const data = await response.json();
          
          const progress = data.progress || 0;
          const currentStepIndex = Math.floor((progress / 100) * INITIAL_STEPS.length);
          
          const newSteps = INITIAL_STEPS.map((step, index) => {
            if (index < currentStepIndex) return { ...step, state: "completed" as const };
            if (index === currentStepIndex) return { ...step, state: "active" as const };
            return { ...step, state: "pending" as const };
          });
          
          setSteps(newSteps);
          setStatusMessage(data.current_stage || "Processing...");

          if (data.status === "completed" || data.status === "failed") {
            setIsProcessing(false);
            clearInterval(interval);
            setStatusMessage(data.status === "completed" ? "Workflow Completed!" : "Workflow Failed.");
            
            if (data.status === "failed") {
              setSteps(prevSteps => prevSteps.map((step, index) => {
                if (index === currentStepIndex) return { ...step, state: "error" as const };
                return step;
              }));
            }
          }
        }
      } catch (error) {
        console.error("Error polling status:", error);
      }
    }, 1000);

    return () => clearInterval(interval);
  }, [taskId]);

  return (
    <main className="min-h-screen p-8 max-w-7xl mx-auto">
      <Header />
      <FeatureCards />

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
        {/* Left Column: Upload Interface */}
        <div className="lg:col-span-5 space-y-8">
          <div className="bg-white rounded-xl p-8 shadow-soft border border-border transition-all duration-300 h-full">
            <div className="flex flex-col md:flex-row items-center justify-between mb-8 gap-4">
              <h3 className="text-xl font-semibold text-text-main flex items-center gap-3 w-full md:w-auto">
                <div className="p-1.5 bg-primary/10 rounded-md border border-primary/20">
                  <Terminal size={18} className="text-primary" />
                </div>
                Neural Link Interface
              </h3>
              <div className="flex bg-surface-highlight rounded-lg p-1 border border-border w-full md:w-auto justify-between md:justify-start">
                {[
                  { id: "pdf", icon: FileText, label: "PDF" },
                  { id: "url", icon: LinkIcon, label: "URL" },
                  { id: "command", icon: Terminal, label: "CMD" },
                ].map((tab) => (
                  <button
                    key={tab.id}
                    onClick={() => setActiveTab(tab.id as any)}
                    className={`flex-1 md:flex-none flex items-center justify-center gap-2 px-4 py-1.5 rounded-md text-xs font-medium transition-all duration-200 ${
                      activeTab === tab.id
                        ? "bg-white shadow-sm text-primary ring-1 ring-border"
                        : "text-text-muted hover:text-text-main hover:bg-white/50"
                    }`}
                  >
                    <tab.icon size={14} />
                    {tab.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="mb-8">
              {activeTab === "pdf" && (
                <div className="border-2 border-dashed border-border rounded-xl p-12 text-center hover:border-primary/50 hover:bg-primary/5 transition-all cursor-pointer group bg-surface-highlight/30">
                  <div className="w-16 h-16 bg-white rounded-full flex items-center justify-center mx-auto mb-4 group-hover:scale-110 transition-transform shadow-sm border border-border">
                    <FileText size={32} className="text-text-muted group-hover:text-primary transition-colors" />
                  </div>
                  <p className="text-text-main font-medium mb-2">Drop research paper here</p>
                  <p className="text-xs text-text-muted">Supports PDF (max 50MB)</p>
                  <input
                    type="text"
                    placeholder="Or enter file path manually..."
                    className="mt-6 w-full bg-white border border-border rounded-lg px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all text-text-main placeholder:text-text-dim shadow-sm"
                    value={inputSource}
                    onChange={(e) => setInputSource(e.target.value)}
                  />
                </div>
              )}
              {activeTab === "url" && (
                <div className="relative">
                  <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
                    <LinkIcon size={18} className="text-text-muted" />
                  </div>
                  <input
                    type="text"
                    placeholder="https://arxiv.org/abs/..."
                    className="w-full bg-white border border-border rounded-xl pl-11 pr-4 py-4 text-text-main focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all placeholder:text-text-dim shadow-sm"
                    value={inputSource}
                    onChange={(e) => setInputSource(e.target.value)}
                  />
                </div>
              )}
              {activeTab === "command" && (
                <textarea
                  placeholder="Describe the algorithm or system requirements..."
                  className="w-full bg-white border border-border rounded-xl px-4 py-4 text-text-main focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all h-40 resize-none placeholder:text-text-dim shadow-sm"
                  value={inputSource}
                  onChange={(e) => setInputSource(e.target.value)}
                />
              )}
            </div>

            <div className="flex justify-end items-center gap-4">
              {statusMessage && (
                <span className="text-sm text-text-muted animate-pulse">
                  {statusMessage}
                </span>
              )}
              <button
                onClick={startWorkflow}
                disabled={isProcessing || !inputSource}
                className={`group relative flex items-center gap-2 px-8 py-3 rounded-lg font-medium transition-all duration-300 overflow-hidden shadow-md hover:shadow-lg ${
                  isProcessing || !inputSource
                    ? "bg-surface-highlight text-text-muted cursor-not-allowed shadow-none"
                    : "bg-primary text-white hover:bg-primary-hover hover:-translate-y-0.5"
                }`}
              >
                {isProcessing ? (
                  <>Processing...</>
                ) : (
                  <>
                    <Play size={18} className="fill-current" />
                    <span>EXECUTE PROTOCOL</span>
                    <ArrowRight size={16} className="group-hover:translate-x-1 transition-transform" />
                  </>
                )}
              </button>
            </div>
          </div>
        </div>

        {/* Right Column: Workflow Status + Logs */}
        <div className="lg:col-span-7 grid grid-cols-1 md:grid-cols-3 gap-6">
          {/* Workflow Status (Vertical) */}
          <div className="md:col-span-1 bg-white rounded-xl p-6 shadow-soft border border-border h-full">
            <WorkflowMonitor steps={steps} />
          </div>

          {/* Logs */}
          <div className="md:col-span-2 h-full">
            <AgentLogViewer taskId={taskId} />
          </div>
        </div>
      </div>
    </main>
  );
}