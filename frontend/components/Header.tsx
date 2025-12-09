import React from "react";
import { Sparkles } from "lucide-react";

export default function Header() {
  return (
    <header className="flex items-center justify-between py-8 mb-12">
      <div className="flex flex-col">
        <div className="flex items-center gap-3 mb-2">
          <div className="p-2 bg-primary/10 rounded-lg border border-primary/20 shadow-sm">
            <Sparkles className="text-primary w-6 h-6" />
          </div>
          <h1 className="font-sans text-4xl font-bold text-text-main tracking-tight">
            DeepCode
          </h1>
        </div>
        <p className="font-sans text-text-muted text-base pl-14">
          Autonomous Research & Engineering Matrix
        </p>
      </div>
      <div className="flex items-center gap-3 px-4 py-2 bg-white border border-border rounded-full shadow-sm">
        <div className="relative flex h-3 w-3">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-success opacity-75"></span>
          <span className="relative inline-flex rounded-full h-3 w-3 bg-success"></span>
        </div>
        <span className="font-mono text-xs font-medium text-success tracking-wider">SYSTEM ONLINE</span>
      </div>
    </header>
  );
}