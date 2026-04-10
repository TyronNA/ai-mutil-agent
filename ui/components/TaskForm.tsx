"use client";

import { useEffect, useState } from "react";
import { Loader2, Gamepad2, Zap, Brain } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { RunRequest } from "@/types";

interface TaskFormProps {
  onSubmit: (req: RunRequest) => Promise<void>;
  isRunning: boolean;
  prefillTask?: string;
}

export function TaskForm({ onSubmit, isRunning, prefillTask }: TaskFormProps) {
  const [task, setTask] = useState("");

  useEffect(() => {
    if (prefillTask) setTask(prefillTask);
  }, [prefillTask]);
  const [gitEnabled, setGitEnabled] = useState(true);
  const [gameProjectDir, setGameProjectDir] = useState("");
  const [maxWorkers, setMaxWorkers] = useState(1);
  const [techExpertPro, setTechExpertPro] = useState(false);
  const [slowMode, setSlowMode] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!task.trim() || isRunning) return;
    await onSubmit({
      task: task.trim(),
      pipeline_type: "game",
      git_enabled: gitEnabled,
      game_project_dir: gameProjectDir || undefined,
      max_workers: maxWorkers,
      tech_expert_pro: techExpertPro,
      slow_mode: slowMode,
    });
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      {/* Task description */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
          Task
        </label>
        <textarea
          value={task}
          onChange={(e) => setTask(e.target.value)}
          placeholder="Thêm tính năng vào game Mộng Võ Lâm…"
          rows={4}
          disabled={isRunning}
          className="w-full rounded-md border border-border bg-muted/50 px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/60 focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/30 disabled:opacity-50 resize-none"
        />
      </div>

      {/* Options panel */}
      <div className="space-y-2.5 rounded-md border border-border/60 bg-muted/20 p-2.5">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          Options
        </p>

        {/* Project dir */}
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">Project Dir (optional)</label>
          <input
            type="text"
            value={gameProjectDir}
            onChange={(e) => setGameProjectDir(e.target.value)}
            placeholder="~/Projects/game-ai/mong-vo-lam"
            disabled={isRunning}
            className="w-full rounded-md border border-border bg-muted/50 px-2.5 py-1.5 text-xs text-foreground placeholder:text-muted-foreground/50 focus:border-primary/50 focus:outline-none disabled:opacity-50 font-mono"
          />
        </div>

        {/* Workers */}
        <div className="flex items-center gap-2">
          <label className="text-xs text-muted-foreground whitespace-nowrap">Workers</label>
          <input
            type="number"
            min={1}
            max={5}
            value={maxWorkers}
            onChange={(e) => setMaxWorkers(Number(e.target.value))}
            disabled={isRunning}
            className="w-14 rounded-md border border-border bg-muted/50 px-2 py-1 text-xs text-foreground text-center focus:border-primary/50 focus:outline-none disabled:opacity-50"
          />
          <span className="text-[10px] text-muted-foreground">parallel dev/qa</span>
        </div>

        {/* TechExpert model selector */}
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">TechExpert Model</label>
          <div className="grid grid-cols-2 gap-1 rounded-md border border-border/60 bg-muted/30 p-0.5">
            <button
              type="button"
              onClick={() => setTechExpertPro(false)}
              disabled={isRunning}
              className={`flex items-center justify-center gap-1.5 rounded-sm py-1.5 text-xs font-medium transition-all ${
                !techExpertPro
                  ? "bg-card shadow-sm text-foreground border border-border"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <Zap className="h-3 w-3 text-yellow-400" />
              Flash
            </button>
            <button
              type="button"
              onClick={() => setTechExpertPro(true)}
              disabled={isRunning}
              className={`flex items-center justify-center gap-1.5 rounded-sm py-1.5 text-xs font-medium transition-all ${
                techExpertPro
                  ? "bg-card shadow-sm text-foreground border border-border"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <Brain className="h-3 w-3 text-purple-400" />
              Pro
            </button>
          </div>
          <p className="text-[10px] text-muted-foreground/60">
            {techExpertPro
              ? "Gemini Pro — deeper reasoning, higher cost"
              : "Gemini Flash — fast & cheap, good enough for most tasks"}
          </p>
        </div>
      </div>

      {/* Toggles row */}
      <div className="flex flex-col gap-2">
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={!gitEnabled}
            onChange={(e) => setGitEnabled(!e.target.checked)}
            disabled={isRunning}
            className="rounded border-border bg-muted accent-primary"
          />
          <span className="text-xs text-muted-foreground">No commit / PR</span>
        </label>
        <label className="flex items-center gap-2 cursor-pointer" title="Add 5s delay between subtasks — reduces API quota usage">
          <input
            type="checkbox"
            checked={slowMode}
            onChange={(e) => setSlowMode(e.target.checked)}
            disabled={isRunning}
            className="rounded border-border bg-muted accent-primary"
          />
          <span className="text-xs text-muted-foreground">
            Slow mode
            <span className="ml-1 text-[10px] text-muted-foreground/50">(5s delay · saves quota)</span>
          </span>
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
            Running…
          </>
        ) : (
          <>
            <Gamepad2 className="h-4 w-4" />
            Run Game Pipeline
          </>
        )}
      </Button>
    </form>
  );
}
