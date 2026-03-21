"use client";

import { useState } from "react";
import { Play, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { RunRequest } from "@/types";

interface TaskFormProps {
  onSubmit: (req: RunRequest) => Promise<void>;
  isRunning: boolean;
}

export function TaskForm({ onSubmit, isRunning }: TaskFormProps) {
  const [task, setTask] = useState("");
  const [dryRun, setDryRun] = useState(false);
  const [skipTests, setSkipTests] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!task.trim() || isRunning) return;
    await onSubmit({ task: task.trim(), dry_run: dryRun, skip_tests: skipTests });
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div className="space-y-1.5">
        <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
          Task Description
        </label>
        <textarea
          value={task}
          onChange={(e) => setTask(e.target.value)}
          placeholder="Describe what you want the agents to build or fix…"
          rows={4}
          disabled={isRunning}
          className="w-full rounded-md border border-border bg-muted/50 px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/60 focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/30 disabled:opacity-50 resize-none"
        />
      </div>

      {/* Options */}
      <div className="flex items-center gap-4">
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={dryRun}
            onChange={(e) => setDryRun(e.target.checked)}
            disabled={isRunning}
            className="rounded border-border bg-muted accent-primary"
          />
          <span className="text-xs text-muted-foreground">Dry run</span>
        </label>
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={skipTests}
            onChange={(e) => setSkipTests(e.target.checked)}
            disabled={isRunning}
            className="rounded border-border bg-muted accent-primary"
          />
          <span className="text-xs text-muted-foreground">Skip tests</span>
        </label>
      </div>

      <Button
        type="submit"
        disabled={isRunning || !task.trim()}
        className="w-full gap-2"
      >
        {isRunning ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin-slow" />
            Pipeline running…
          </>
        ) : (
          <>
            <Play className="h-4 w-4" />
            Run Pipeline
          </>
        )}
      </Button>
    </form>
  );
}
