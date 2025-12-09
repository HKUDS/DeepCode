import React from "react";
import { Brain, Zap, Database, Shield } from "lucide-react";

const features = [
  {
    icon: Brain,
    title: "Neural Synthesis",
    desc: "Transform research papers directly into executable repositories via multi-agent LLM pipelines.",
    color: "text-pink-600",
    bg: "bg-pink-50",
    border: "border-pink-100",
  },
  {
    icon: Zap,
    title: "Hyper-Speed Mode",
    desc: "Acceleration layer that parallelizes retrieval, planning, and implementation for fastest delivery.",
    color: "text-amber-600",
    bg: "bg-amber-50",
    border: "border-amber-100",
  },
  {
    icon: Database,
    title: "Cognitive Context",
    desc: "Semantic memory graphs retain methodology, datasets, and evaluation strategy during reasoning.",
    color: "text-blue-600",
    bg: "bg-blue-50",
    border: "border-blue-100",
  },
  {
    icon: Shield,
    title: "Secure Sandbox",
    desc: "Isolated execution & validation environment keeps experiments safe and reproducible.",
    color: "text-emerald-600",
    bg: "bg-emerald-50",
    border: "border-emerald-100",
  },
];

export default function FeatureCards() {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-12">
      {features.map((feature, index) => (
        <div
          key={index}
          className="group relative bg-white border border-border p-6 rounded-xl transition-all duration-300 hover:border-transparent hover:-translate-y-1 hover:shadow-lg overflow-hidden"
        >
          <div className={`absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity duration-500 bg-gradient-to-br from-transparent via-transparent to-${feature.color.split('-')[1]}-500/5`} />
          
          <div className={`w-12 h-12 rounded-lg ${feature.bg} ${feature.border} border flex items-center justify-center mb-4 group-hover:scale-110 transition-transform duration-300`}>
            <feature.icon size={24} className={feature.color} />
          </div>
          
          <h3 className="font-sans font-semibold text-lg mb-2 text-text-main group-hover:text-primary transition-colors">
            {feature.title}
          </h3>
          <p className="font-sans text-sm text-text-muted leading-relaxed">
            {feature.desc}
          </p>
        </div>
      ))}
    </div>
  );
}