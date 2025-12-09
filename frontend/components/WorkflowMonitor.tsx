import React from "react";
import { Check, Loader2, Circle, AlertTriangle } from "lucide-react";

interface Step {
  title: string;
  subtitle: string;
  state: "pending" | "active" | "completed" | "error";
}

interface WorkflowMonitorProps {
  steps: Step[];
}

export default function WorkflowMonitor({ steps }: WorkflowMonitorProps) {
  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center gap-3 mb-6 px-4">
        <h3 className="text-xs font-semibold text-text-muted uppercase tracking-widest">
          Workflow Status
        </h3>
        <div className="h-px flex-1 bg-gradient-to-r from-border to-transparent" />
      </div>
      
      <div className="relative flex flex-col gap-0 px-4">
        {/* Vertical Connecting Line Background */}
        <div className="absolute top-4 bottom-4 left-[27px] w-0.5 bg-border/40 -z-10" />
        
        {/* Active Progress Line (Vertical) */}
        <div
          className="absolute top-4 left-[27px] w-0.5 bg-primary/30 -z-10 transition-all duration-500"
          style={{
            height: `calc(${(steps.filter(s => s.state === 'completed').length / (steps.length - 1)) * 100}% - 32px)`
          }}
        />

        {steps.map((step, index) => {
          let statusColor = "text-text-dim";
          let bgClass = "bg-white";
          let Icon = Circle;
          let iconSize = 16;

          if (step.state === "active") {
            statusColor = "text-primary";
            bgClass = "bg-white";
            Icon = Loader2;
            iconSize = 18;
          } else if (step.state === "completed") {
            statusColor = "text-white";
            bgClass = "bg-success";
            Icon = Check;
            iconSize = 14;
          } else if (step.state === "error") {
            statusColor = "text-white";
            bgClass = "bg-red-500";
            Icon = AlertTriangle;
          }

          return (
            <div key={index} className="flex items-start gap-4 relative group pb-8 last:pb-0">
              <div
                className={`
                  w-6 h-6 rounded-full flex items-center justify-center shrink-0
                  transition-all duration-300 z-10 mt-0.5
                  ${step.state === 'completed' || step.state === 'error' ? bgClass : 'bg-white border-2'}
                  ${step.state === 'pending' ? 'border-border text-text-dim' : ''}
                  ${step.state === 'active' ? 'border-primary text-primary scale-110 shadow-lg' : ''}
                  ${step.state === 'error' ? 'text-white shadow-lg shadow-red-500/20' : ''}
                `}
              >
                <Icon size={iconSize} className={step.state === "active" ? "animate-spin" : ""} />
              </div>
              
              <div className="flex flex-col pt-0.5">
                <div className={`text-xs font-bold tracking-wide transition-colors duration-300 ${
                  step.state === 'active' ? 'text-primary' :
                  step.state === 'completed' ? 'text-text-main' :
                  step.state === 'error' ? 'text-red-500' : 'text-text-muted'
                }`}>
                  {step.title}
                </div>
                <div className={`text-[10px] font-medium leading-tight mt-0.5 ${
                  step.state === 'error' ? 'text-red-400' : 'text-text-muted'
                }`}>
                  {step.subtitle}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}