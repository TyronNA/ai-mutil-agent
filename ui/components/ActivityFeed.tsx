"use client";

import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";
import type { FeedLine } from "@/types";

interface ActivityFeedProps {
  lines: FeedLine[];
  filter?: "all" | "progress" | "result" | "error";
}

export function ActivityFeed({ lines, filter = "all" }: ActivityFeedProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [lines.length]);

  const filtered =
    filter === "all" ? lines : lines.filter((l) => l.type === filter);

  if (filtered.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        {lines.length === 0 ? "Waiting for pipeline to start…" : "No events in this category"}
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto space-y-0.5 px-1 font-mono text-[11px]">
      {filtered.map((line) => (
        <div
          key={line.id}
          className={cn(
            "flex items-start gap-2 rounded px-2 py-1 transition-colors",
            line.type === "error"
              ? "bg-red-500/10 text-red-400"
              : line.type === "result"
              ? "bg-emerald-500/10 text-emerald-400"
              : line.type === "done"
              ? "bg-primary/10 text-primary"
              : "hover:bg-muted/50"
          )}
        >
          {/* Timestamp */}
          <span className="flex-shrink-0 text-muted-foreground/50 mt-0.5">{line.timestamp}</span>

          {/* Agent tag */}
          <span
            className="flex-shrink-0 rounded px-1 py-0.5 text-[10px] font-semibold uppercase tracking-wider"
            style={{ backgroundColor: `${line.agentColor}22`, color: line.agentColor }}
          >
            {line.agentIcon} {line.agent}
          </span>

          {/* Message */}
          <span
            className={cn(
              "flex-1 break-all leading-relaxed",
              line.type === "error"
                ? "text-red-400"
                : line.type === "result" || line.type === "done"
                ? ""
                : "text-foreground/90"
            )}
          >
            {line.message}
          </span>
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
