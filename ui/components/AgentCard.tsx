"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { Agent } from "@/types";
import { cn } from "@/lib/utils";

interface AgentCardProps {
  agent: Agent;
  isActive?: boolean;
}

export function AgentCard({ agent, isActive = false }: AgentCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [showPrompt, setShowPrompt] = useState(false);

  return (
    <div
      className={cn(
        "rounded-lg border transition-all duration-200",
        isActive
          ? "border-opacity-60 bg-card shadow-md"
          : "border-border bg-card/50 hover:bg-card"
      )}
      style={isActive ? { borderColor: agent.color } : {}}
    >
      {/* Header row */}
      <button
        className="flex w-full items-center gap-3 p-3 text-left"
        onClick={() => setExpanded((v) => !v)}
      >
        {/* Icon + active indicator */}
        <div
          className="relative flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg text-lg"
          style={{ backgroundColor: `${agent.color}22` }}
        >
          <span>{agent.icon}</span>
          {isActive && (
            <span
              className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full border-2 border-card animate-pulse-dot"
              style={{ backgroundColor: agent.color }}
            />
          )}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-foreground">{agent.name}</span>
            {isActive && (
              <span
                className="rounded-full px-1.5 py-0.5 text-[10px] font-medium"
                style={{ backgroundColor: `${agent.color}33`, color: agent.color }}
              >
                active
              </span>
            )}
          </div>
          <p className="truncate text-xs text-muted-foreground">{agent.role}</p>
        </div>

        {expanded ? (
          <ChevronDown className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
        )}
      </button>

      {/* Expanded body */}
      {expanded && (
        <div className="border-t border-border px-3 pb-3 pt-2 space-y-2">
          <p className="text-xs text-muted-foreground leading-relaxed">{agent.description}</p>

          <button
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
            onClick={() => setShowPrompt((v) => !v)}
          >
            {showPrompt ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
            system prompt
          </button>

          {showPrompt && (
            <pre className="max-h-48 overflow-y-auto rounded-md bg-muted p-2 text-[10px] leading-relaxed text-muted-foreground whitespace-pre-wrap font-mono">
              {agent.system_prompt}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
