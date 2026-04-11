"use client";

import { useCallback, useEffect, useState } from "react";
import { BarChart2, RefreshCw, Zap, DollarSign, Hash, Database } from "lucide-react";
import { fetchAnalytics } from "@/lib/api";
import type { AnalyticsData, SessionTokenUsage } from "@/types";

const VND_RATE = 25_000; // 1 USD ≈ 25,000 VND (approximate)

function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

function fmtUsd(v: number): string {
  if (v < 0.001) return `$${(v * 1000).toFixed(4)}m`; // millicents
  return `$${v.toFixed(4)}`;
}

function fmtVnd(vUsd: number): string {
  const vnd = vUsd * VND_RATE;
  if (vnd < 1) return "< 1₫";
  if (vnd >= 1_000_000) return `${(vnd / 1_000_000).toFixed(2)} triệu ₫`;
  if (vnd >= 1_000) return `${(vnd / 1_000).toFixed(0)}K ₫`;
  return `${Math.round(vnd)} ₫`;
}

function TokenBar({ label, value, total, color }: { label: string; value: number; total: number; color: string }) {
  const pct = total > 0 ? Math.min(100, (value / total) * 100) : 0;
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="font-mono text-foreground">{fmt(value)}</span>
      </div>
      <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
    </div>
  );
}

function StatCard({ icon, label, value, sub }: { icon: React.ReactNode; label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-lg border border-border bg-card/60 p-3 space-y-1">
      <div className="flex items-center gap-1.5 text-muted-foreground">
        {icon}
        <span className="text-[11px] uppercase tracking-widest font-semibold">{label}</span>
      </div>
      <div className="text-lg font-bold text-foreground font-mono">{value}</div>
      {sub && <div className="text-[11px] text-muted-foreground">{sub}</div>}
    </div>
  );
}

function SessionRow({ s, isLast }: { s: SessionTokenUsage; isLast: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`border-b border-border/50 ${isLast ? "border-b-0" : ""}`}>
      <button
        className="w-full flex items-center justify-between px-3 py-2 text-left hover:bg-muted/30 transition-colors"
        onClick={() => setOpen((o) => !o)}
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-mono text-[10px] text-muted-foreground shrink-0">{s.session_id.slice(0, 8)}</span>
          <span className="text-xs text-foreground truncate">{s.task || "—"}</span>
        </div>
        <div className="flex items-center gap-3 shrink-0 ml-2">
          <span className="text-[11px] font-mono text-emerald-400">{fmtUsd(s.cost_usd)}</span>
          <span className="text-[11px] font-mono text-muted-foreground">{fmt(s.total_tokens)} tok</span>
          <span
            className={`text-[10px] rounded-full px-1.5 py-0.5 border ${
              s.status === "done"
                ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-400"
                : s.status === "error"
                ? "bg-red-500/10 border-red-500/30 text-red-400"
                : "bg-primary/10 border-primary/30 text-primary"
            }`}
          >
            {s.status ?? "?"}
          </span>
          <span className="text-muted-foreground text-xs">{open ? "▲" : "▼"}</span>
        </div>
      </button>

      {open && (
        <div className="px-3 pb-3 space-y-2 bg-muted/10">
          <div className="pt-1 space-y-1.5">
            <TokenBar label="Prompt" value={s.prompt_tokens} total={s.total_tokens} color="#60a5fa" />
            <TokenBar label="Cached" value={s.cached_tokens} total={s.total_tokens} color="#34d399" />
            <TokenBar label="Output" value={s.output_tokens} total={s.total_tokens} color="#a78bfa" />
          </div>
          <div className="grid grid-cols-3 gap-1.5 pt-1">
            <div className="rounded bg-muted/40 px-2 py-1 text-center">
              <div className="text-[10px] text-muted-foreground">Calls</div>
              <div className="text-xs font-mono font-bold text-foreground">{s.calls}</div>
            </div>
            <div className="rounded bg-muted/40 px-2 py-1 text-center">
              <div className="text-[10px] text-muted-foreground">USD</div>
              <div className="text-xs font-mono font-bold text-emerald-400">{fmtUsd(s.cost_usd)}</div>
            </div>
            <div className="rounded bg-muted/40 px-2 py-1 text-center">
              <div className="text-[10px] text-muted-foreground">VND</div>
              <div className="text-xs font-mono font-bold text-amber-400">{fmtVnd(s.cost_usd)}</div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export function AnalyticsPanel({ currentSessionId }: { currentSessionId?: string }) {
  const [data, setData] = useState<AnalyticsData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchAnalytics();
      setData(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load analytics");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // Auto-refresh while a session is running
  useEffect(() => {
    if (!currentSessionId) return;
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, [currentSessionId, load]);

  const agg = data?.aggregate;
  const sessions = data?.sessions ?? [];

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5 shrink-0">
        <div className="flex items-center gap-2">
          <BarChart2 className="h-4 w-4 text-primary" />
          <span className="text-sm font-semibold text-foreground">Token Analytics</span>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-xs text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-40"
        >
          <RefreshCw className={`h-3 w-3 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-5">
        {error && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-400">
            {error}
          </div>
        )}

        {/* Aggregate stats */}
        {agg && (
          <>
            <div>
              <p className="mb-2 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
                All Sessions — Aggregate
              </p>
              <div className="grid grid-cols-2 gap-2">
                <StatCard
                  icon={<Hash className="h-3 w-3" />}
                  label="Total Tokens"
                  value={fmt(agg.total_tokens)}
                  sub={`${fmt(agg.prompt_tokens)} in · ${fmt(agg.output_tokens)} out`}
                />
                <StatCard
                  icon={<Zap className="h-3 w-3" />}
                  label="API Calls"
                  value={String(agg.calls)}
                  sub={agg.flash_calls !== undefined ? `${agg.flash_calls} flash · ${agg.pro_calls} pro` : undefined}
                />
                <StatCard
                  icon={<DollarSign className="h-3 w-3" />}
                  label="Cost (USD)"
                  value={fmtUsd(agg.cost_usd)}
                />
                <StatCard
                  icon={<span className="text-amber-400 text-xs font-bold">₫</span>}
                  label="Cost (VND)"
                  value={fmtVnd(agg.cost_usd)}
                  sub={`~${VND_RATE.toLocaleString()} ₫/USD`}
                />
              </div>
            </div>

            {/* Token breakdown bar */}
            <div className="rounded-lg border border-border bg-card/40 p-3 space-y-2.5">
              <p className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
                Token Breakdown
              </p>
              <TokenBar label="Prompt (billed)" value={Math.max(0, agg.prompt_tokens - agg.cached_tokens)} total={agg.total_tokens} color="#60a5fa" />
              <TokenBar label="Cached (discount)" value={agg.cached_tokens} total={agg.total_tokens} color="#34d399" />
              <TokenBar label="Output" value={agg.output_tokens} total={agg.total_tokens} color="#a78bfa" />
            </div>

            {/* Pricing reference */}
            <div className="rounded-lg border border-border/50 bg-muted/20 p-3">
              <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
                Pricing Reference (Gemini Flash)
              </p>
              <div className="space-y-0.5 text-[11px] text-muted-foreground font-mono">
                <div className="flex justify-between">
                  <span>Input</span>
                  <span>${agg.pricing.flash_input_per_1m.toFixed(3)} / 1M tokens</span>
                </div>
                <div className="flex justify-between">
                  <span>Output</span>
                  <span>${agg.pricing.flash_output_per_1m.toFixed(3)} / 1M tokens</span>
                </div>
                <div className="flex justify-between text-emerald-500/80">
                  <span>Cached</span>
                  <span>${agg.pricing.flash_cached_per_1m.toFixed(4)} / 1M tokens</span>
                </div>
              </div>
            </div>
          </>
        )}

        {/* Per-session breakdown */}
        {sessions.length > 0 && (
          <div>
            <p className="mb-2 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
              Per-Session Breakdown ({sessions.length})
            </p>
            <div className="rounded-lg border border-border overflow-hidden">
              {sessions.map((s, i) => (
                <SessionRow key={s.session_id} s={s} isLast={i === sessions.length - 1} />
              ))}
            </div>
          </div>
        )}

        {!loading && sessions.length === 0 && !error && (
          <div className="flex flex-col items-center justify-center py-12 text-center space-y-2">
            <Database className="h-8 w-8 text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">No token data yet.</p>
            <p className="text-xs text-muted-foreground/60">Run a pipeline to start tracking usage.</p>
          </div>
        )}
      </div>
    </div>
  );
}
