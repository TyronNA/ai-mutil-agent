"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  GitBranch,
  Loader2,
  Monitor,
  RefreshCw,
  Terminal,
  Trash2,
  Maximize2,
  Minimize2,
} from "lucide-react";
import { API_BASE, fetchPreviewInfo, checkoutPreviewBranch } from "@/lib/api";
import type { PreviewInfo } from "@/types";

const GAME_PREVIEW_URL = process.env.NEXT_PUBLIC_GAME_PREVIEW_URL ?? `${API_BASE}/preview/game-html`;

// ── Types ─────────────────────────────────────────────────────────────────────

interface ConsoleLog {
  id: number;
  level: "log" | "warn" | "error" | "info";
  message: string;
  timestamp: string;
}

const LEVEL_STYLE: Record<string, string> = {
  log:   "text-foreground",
  info:  "text-blue-400",
  warn:  "text-amber-400",
  error: "text-red-400",
};

const LEVEL_BG: Record<string, string> = {
  log:   "",
  info:  "",
  warn:  "bg-amber-500/5",
  error: "bg-red-500/5",
};

let _logId = 0;
function nowTs() {
  return new Date().toLocaleTimeString("en-US", {
    hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

// ── Component ──────────────────────────────────────────────────────────────────

interface GamePreviewPanelProps {
  initialBranch?: string;
}

export function GamePreviewPanel({ initialBranch }: GamePreviewPanelProps) {
  const [info, setInfo] = useState<PreviewInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedBranch, setSelectedBranch] = useState<string>(initialBranch ?? "");
  const [checkingOut, setCheckingOut] = useState(false);
  const [logs, setLogs] = useState<ConsoleLog[]>([]);
  const [iframeKey, setIframeKey] = useState(0);
  const [iframeSrc, setIframeSrc] = useState(`${GAME_PREVIEW_URL}?v=${Date.now()}`);
  const [expandLogs, setExpandLogs] = useState(false);
  const [scale, setScale] = useState(1);

  const logsEndRef = useRef<HTMLDivElement>(null);
  const logsContainerRef = useRef<HTMLDivElement>(null);

  const addLog = useCallback((level: ConsoleLog["level"], message: string) => {
    setLogs((prev) => [
      ...prev.slice(-499),
      { id: ++_logId, level, message, timestamp: nowTs() },
    ]);
  }, []);

  const loadInfo = useCallback(async () => {
    try {
      const data = await fetchPreviewInfo();
      setInfo(data);
      if (!selectedBranch) {
        setSelectedBranch(initialBranch ?? data.current_branch);
      }
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load preview info");
    } finally {
      setLoading(false);
    }
  }, [initialBranch, selectedBranch]);

  useEffect(() => { loadInfo(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Listen to postMessage from iframe for console logs
  useEffect(() => {
    const handler = (e: MessageEvent) => {
      if (e.data && e.data.type === "console") {
        const level = (["log", "warn", "error", "info"].includes(e.data.level)
          ? e.data.level
          : "log") as ConsoleLog["level"];
        addLog(level, String(e.data.message ?? ""));
      }
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, [addLog]);

  const handleCheckout = async () => {
    if (!selectedBranch || selectedBranch === info?.current_branch) return;
    setCheckingOut(true);
    setLogs([]);
    try {
      await checkoutPreviewBranch(selectedBranch);
      // Reload info to reflect updated current_branch
      const updated = await fetchPreviewInfo();
      setInfo(updated);
      setIframeKey((k) => k + 1);
      setIframeSrc(`${GAME_PREVIEW_URL}?v=${Date.now()}`);
      addLog("info", `✅ Switched to branch: ${selectedBranch}`);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to checkout branch");
    } finally {
      setCheckingOut(false);
    }
  };

  const handleReload = () => {
    setLogs([]);
    setIframeKey((k) => k + 1);
    setIframeSrc(`${GAME_PREVIEW_URL}?v=${Date.now()}`);
    addLog("info", "🔄 Reloading game…");
  };

  useEffect(() => {
    if (logs.length > 0) {
      const container = logsContainerRef.current;
      if (!container) return;
      container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
    }
  }, [logs]);

  // When initialBranch changes from parent (user clicked Preview on a done task)
  useEffect(() => {
    if (initialBranch && initialBranch !== selectedBranch) {
      setSelectedBranch(initialBranch);
    }
  }, [initialBranch]); // eslint-disable-line react-hooks/exhaustive-deps

  // If the selected branch differs from current branch, auto-checkout for accurate preview.
  useEffect(() => {
    if (!info || !selectedBranch || checkingOut) return;
    if (selectedBranch === info.current_branch) return;

    let cancelled = false;
    const run = async () => {
      setCheckingOut(true);
      try {
        await checkoutPreviewBranch(selectedBranch);
        const updated = await fetchPreviewInfo();
        if (cancelled) return;
        setInfo(updated);
        setIframeKey((k) => k + 1);
        setIframeSrc(`${GAME_PREVIEW_URL}?v=${Date.now()}`);
        addLog("info", `✅ Auto-switched preview to branch: ${selectedBranch}`);
        setError(null);
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Failed to checkout branch");
        }
      } finally {
        if (!cancelled) setCheckingOut(false);
      }
    };

    void run();
    return () => {
      cancelled = true;
    };
  }, [info, selectedBranch, checkingOut, addLog]);

  const needsCheckout = selectedBranch && selectedBranch !== info?.current_branch;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* ── Header bar ── */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2 bg-card/40">
        <Monitor className="h-3.5 w-3.5 text-primary flex-shrink-0" />
        <span className="text-xs font-semibold text-foreground">Game Preview</span>

        {/* Branch selector */}
        <div className="flex items-center gap-1.5">
          <GitBranch className="h-3 w-3 text-muted-foreground" />
          {loading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
          ) : (
            <select
              value={selectedBranch}
              onChange={(e) => setSelectedBranch(e.target.value)}
              className="rounded-md border border-border bg-muted/30 px-2 py-0.5 text-xs text-foreground focus:outline-none max-w-[220px]"
            >
              {(info?.branches ?? []).length === 0 && (
                <option value="">— no branches —</option>
              )}
              {(info?.branches ?? []).map((b) => (
                <option key={b} value={b}>
                  {b}
                  {b === info?.current_branch ? " ✓" : ""}
                </option>
              ))}
            </select>
          )}
          {needsCheckout && (
            <button
              onClick={handleCheckout}
              disabled={checkingOut}
              className="flex items-center gap-1 rounded-md border border-primary/40 bg-primary/10 px-2 py-0.5 text-xs text-primary hover:bg-primary/20 disabled:opacity-50 transition-colors"
            >
              {checkingOut ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <GitBranch className="h-3 w-3" />
              )}
              Checkout
            </button>
          )}
        </div>

        {info?.current_branch && (
          <span className="text-[10px] text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 rounded-full px-2 py-0.5 font-mono">
            {info.current_branch}
          </span>
        )}

        <div className="ml-auto flex items-center gap-1.5">
          <button
            onClick={handleReload}
            className="flex items-center gap-1 rounded-md border border-border px-2.5 py-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            <RefreshCw className="h-3 w-3" />
            Reload
          </button>
          <button
            onClick={() => setLogs([])}
            className="rounded-md border border-border p-1 text-muted-foreground hover:text-red-400 transition-colors"
            title="Clear console"
          >
            <Trash2 className="h-3 w-3" />
          </button>
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="flex items-center gap-2 border-b border-red-500/30 bg-red-500/10 px-4 py-2 text-xs text-red-400">
          <AlertCircle className="h-3.5 w-3.5 flex-shrink-0" />
          {error}
          <button
            onClick={() => setError(null)}
            className="ml-auto text-red-400/70 hover:text-red-400"
          >
            ✕
          </button>
        </div>
      )}

      {/* ── Split layout: iframe + console ── */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* iframe area */}
        <div
          className="relative border-b border-border bg-black overflow-auto"
          style={{ flex: expandLogs ? "0 0 30%" : "0 0 90%" }}
        >
          {/* Scale controls */}
          {!loading && info?.game_dir && (
            <div className="absolute top-2 right-2 z-10 flex items-center gap-1 rounded-md border border-border/50 bg-black/80 px-2 py-1 backdrop-blur-sm">
              <span className="text-[10px] text-muted-foreground">Scale:</span>
              <select
                value={scale}
                onChange={(e) => setScale(Number(e.target.value))}
                className="rounded-md border border-border/40 bg-muted/20 px-1.5 py-0.5 text-[11px] text-foreground focus:outline-none"
              >
                {[0.5, 0.75, 1, 1.25, 1.5, 1.75, 2].map((s) => (
                  <option key={s} value={s}>
                    {Math.round(s * 100)}%{s === 1 ? " (default)" : ""}
                  </option>
                ))}
              </select>
            </div>
          )}
          {loading ? (
            <div className="flex h-full items-center justify-center">
              <Loader2 className="h-6 w-6 animate-spin text-primary" />
            </div>
          ) : !info?.game_dir ? (
            <div className="flex h-full flex-col items-center justify-center gap-2 text-center p-4">
              <Monitor className="h-10 w-10 text-muted-foreground/20" />
              <p className="text-xs text-muted-foreground">No game directory configured</p>
              <p className="text-[11px] text-muted-foreground/50">
                Set <code className="font-mono bg-muted/40 px-1 rounded">GAME_PROJECT_DIR</code> in <code className="font-mono bg-muted/40 px-1 rounded">.env</code>
              </p>
            </div>
          ) : (
            <div
              style={{
                transform: `scale(${scale})`,
                transformOrigin: "left top",
                width: `${100 / scale}%`,
                height: `${100 / scale}%`,
              }}
              className="will-change-transform"
            >
              <iframe
                key={iframeKey}
                src={iframeSrc}
                className="h-full w-full border-0"
                title="Mộng Võ Lâm Preview"
                allow="autoplay"
              />
            </div>
          )}
        </div>

        {/* ── Console log panel ── */}
        <div className="flex flex-col min-h-0" style={{ flex: expandLogs ? "1 1 70%" : "1 1 10%" }}>
          <div className="flex items-center gap-2 border-b border-border px-3 py-1.5 bg-card/30 flex-shrink-0">
            <Terminal className="h-3 w-3 text-muted-foreground" />
            <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
              Console
            </span>
            {logs.filter((l) => l.level === "error").length > 0 && (
              <span className="rounded-full bg-red-500/20 border border-red-500/30 px-1.5 py-0.5 text-[10px] text-red-400">
                {logs.filter((l) => l.level === "error").length} error
                {logs.filter((l) => l.level === "error").length !== 1 ? "s" : ""}
              </span>
            )}
            {logs.filter((l) => l.level === "warn").length > 0 && (
              <span className="rounded-full bg-amber-500/20 border border-amber-500/30 px-1.5 py-0.5 text-[10px] text-amber-400">
                {logs.filter((l) => l.level === "warn").length} warn
              </span>
            )}
            <span className="ml-auto text-[10px] text-muted-foreground">
              {logs.length} line{logs.length !== 1 ? "s" : ""}
            </span>
            <button
              onClick={() => setExpandLogs((v) => !v)}
              className="rounded p-0.5 text-muted-foreground hover:text-foreground transition-colors"
              title={expandLogs ? "Shrink console" : "Expand console"}
            >
              {expandLogs ? (
                <Minimize2 className="h-3 w-3" />
              ) : (
                <Maximize2 className="h-3 w-3" />
              )}
            </button>
          </div>

          <div
            ref={logsContainerRef}
            className="flex-1 overflow-y-auto bg-neutral-950 px-3 py-2 font-mono text-[11px]"
          >
            {logs.length === 0 ? (
              <p className="text-muted-foreground/30 italic">
                Load the game to capture console output…
              </p>
            ) : (
              logs.map((log) => (
                <div
                  key={log.id}
                  className={`flex gap-2 leading-[1.6] ${LEVEL_STYLE[log.level]} ${LEVEL_BG[log.level]}`}
                >
                  <span className="text-muted-foreground/30 flex-shrink-0 select-none">
                    {log.timestamp}
                  </span>
                  <span
                    className={`flex-shrink-0 w-10 select-none ${
                      log.level === "error"
                        ? "text-red-500"
                        : log.level === "warn"
                        ? "text-amber-500"
                        : log.level === "info"
                        ? "text-blue-500"
                        : "text-muted-foreground/40"
                    }`}
                  >
                    [{log.level.toUpperCase().slice(0, 3)}]
                  </span>
                  <span className="break-all whitespace-pre-wrap">{log.message}</span>
                </div>
              ))
            )}
            <div ref={logsEndRef} />
          </div>
        </div>
      </div>
    </div>
  );
}
