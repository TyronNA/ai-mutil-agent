"use client";

import { ExternalLink, FileCode2 } from "lucide-react";

interface ResultBarProps {
  prUrl?: string;
  filesWritten?: string[];
  sessionId?: string;
  status?: "running" | "done" | "error";
}

export function ResultBar({ prUrl, filesWritten, sessionId, status }: ResultBarProps) {
  if (!prUrl && !filesWritten?.length && !sessionId) return null;

  return (
    <div className="rounded-lg border border-border bg-card p-3 space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
          Pipeline Result
        </span>
        {status && (
          <span
            className={
              status === "done"
                ? "text-xs text-emerald-400"
                : status === "error"
                ? "text-xs text-red-400"
                : "text-xs text-primary"
            }
          >
            {status === "done" ? "✓ Completed" : status === "error" ? "✗ Failed" : "⟳ Running"}
          </span>
        )}
      </div>

      {prUrl && (
        <a
          href={prUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-2 rounded-md border border-border bg-muted/50 px-3 py-2 text-xs text-primary hover:bg-muted transition-colors"
        >
          <ExternalLink className="h-3.5 w-3.5" />
          View Pull Request
        </a>
      )}

      {filesWritten && filesWritten.length > 0 && (
        <div className="space-y-1">
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <FileCode2 className="h-3.5 w-3.5" />
            <span>{filesWritten.length} file{filesWritten.length !== 1 ? "s" : ""} written</span>
          </div>
          <div className="max-h-24 overflow-y-auto space-y-0.5">
            {filesWritten.map((f) => (
              <div key={f} className="text-[11px] font-mono text-muted-foreground/80 truncate px-2">
                {f}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
