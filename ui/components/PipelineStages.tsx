"use client";

import { cn } from "@/lib/utils";
import type { PipelineStage } from "@/types";

const STAGES: Omit<PipelineStage, "status">[] = [
  { id: "init", label: "Init", icon: "🚀" },
  { id: "plan", label: "Plan", icon: "🧠" },
  { id: "checkout", label: "Checkout", icon: "🌿" },
  { id: "code", label: "Code", icon: "💻" },
  { id: "review", label: "Review", icon: "🔍" },
  { id: "test", label: "Test", icon: "🧪" },
  { id: "commit", label: "Commit", icon: "📦" },
  { id: "notify", label: "Notify", icon: "📬" },
];

interface PipelineStagesProps {
  activeStage?: string;
  completedStages?: string[];
  errorStage?: string;
}

export function PipelineStages({
  activeStage,
  completedStages = [],
  errorStage,
}: PipelineStagesProps) {
  return (
    <div className="space-y-1">
      {STAGES.map((stage, i) => {
        const isActive = stage.id === activeStage;
        const isDone = completedStages.includes(stage.id);
        const isError = stage.id === errorStage;

        return (
          <div key={stage.id} className="flex items-center gap-2">
            {/* Connector line */}
            <div className="flex flex-col items-center">
              <div
                className={cn(
                  "h-5 w-5 rounded-full flex items-center justify-center text-xs flex-shrink-0 transition-all duration-300",
                  isError
                    ? "bg-red-500/20 text-red-400 ring-1 ring-red-500/50"
                    : isActive
                    ? "bg-primary/20 text-primary ring-1 ring-primary/50 animate-pulse-dot"
                    : isDone
                    ? "bg-emerald-500/20 text-emerald-400"
                    : "bg-muted text-muted-foreground"
                )}
              >
                {isError ? "✗" : isDone ? "✓" : isActive ? "●" : stage.icon}
              </div>
              {i < STAGES.length - 1 && (
                <div
                  className={cn(
                    "w-px h-2 mt-0.5",
                    isDone ? "bg-emerald-500/40" : "bg-border"
                  )}
                />
              )}
            </div>

            <span
              className={cn(
                "text-xs transition-colors",
                isError
                  ? "text-red-400 font-medium"
                  : isActive
                  ? "text-foreground font-medium"
                  : isDone
                  ? "text-emerald-400"
                  : "text-muted-foreground"
              )}
            >
              {stage.label}
            </span>
          </div>
        );
      })}
    </div>
  );
}
