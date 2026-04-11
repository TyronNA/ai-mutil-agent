"use client";

import { useCallback, useEffect, useState } from "react";
import { BarChart2, RefreshCw, Zap, DollarSign, Hash, Database, TrendingUp, Bot } from "lucide-react";
import { fetchAnalytics, fetchAgentAnalytics } from "@/lib/api";
import type { AnalyticsData, SessionTokenUsage, AgentUsage, AgentAnalyticsData } from "@/types";

const VND_RATE = 25_000;

const AGENT_COLORS: Record<string, string> = {
  tech_expert: "#a78bfa", dev: "#34d399", qa: "#60a5fa", git: "#f59e0b", notifier: "#fb923c",
  planner: "#a78bfa", coder: "#34d399", reviewer: "#60a5fa", analyzer: "#e879f9", tester: "#f472b6",
};
const AGENT_ICONS: Record<string, string> = {
  tech_expert: "🏛", dev: "⚔", qa: "🧪", git: "🌿", notifier: "🔔",
  planner: "🗺", coder: "💻", reviewer: "🔍", analyzer: "🔬", tester: "📸",
};
function agentColor(n: string) { return AGENT_COLORS[n] ?? "#94a3b8"; }
function agentIcon(n: string)  { return AGENT_ICONS[n]  ?? "🤖"; }
function agentLabel(n: string) { return n.replace(/_/g, " "); }

function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}
function fmtUsd(v: number): string {
  if (v < 0.001) return `$${(v * 1000).toFixed(4)}m`;
  return `$${v.toFixed(4)}`;
}
function fmtVnd(vUsd: number): string {
  const vnd = vUsd * VND_RATE;
  if (vnd < 1) return "< 1₫";
  if (vnd >= 1_000_000) return `${(vnd / 1_000_000).toFixed(2)} triệu ₫`;
  if (vnd >= 1_000) return `${(vnd / 1_000).toFixed(0)}K ₫`;
  return `${Math.round(vnd)} ₫`;
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

function TokenBar({ label, value, total, color }: { label: string; value: number; total: number; color: string }) {
  const pct = total > 0 ? Math.min(100, (value / total) * 100) : 0;
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="font-mono text-foreground">{fmt(value)}</span>
      </div>
      <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
        <div className="h-full rounded-full transition-all duration-500" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
    </div>
  );
}

function AgentBarChart({ agents }: { agents: AgentUsage[] }) {
  const maxCost   = Math.max(...agents.map(a => a.cost_usd), 0.0001);
  const maxTokens = Math.max(...agents.map(a => a.total_tokens), 1);
  if (agents.length === 0) return <p className="text-center text-xs text-muted-foreground py-4">No agent data yet</p>;
  return (
    <div className="space-y-3.5">
      {agents.map((agent) => {
        const color    = agentColor(agent.agent_name);
        const costPct  = Math.max(2, (agent.cost_usd    / maxCost)   * 100);
        const tokenPct = Math.max(1, (agent.total_tokens / maxTokens) * 100);
        return (
          <div key={agent.agent_name}>
            <div className="flex items-center justify-between mb-1">
              <div className="flex items-center gap-1.5 text-[11px]">
                <span>{agentIcon(agent.agent_name)}</span>
                <span className="font-semibold text-foreground capitalize">{agentLabel(agent.agent_name)}</span>
              </div>
              <div className="flex items-center gap-2 text-[10px] font-mono">
                <span className="text-emerald-400 font-bold">{fmtUsd(agent.cost_usd)}</span>
                <span className="text-amber-400">{fmtVnd(agent.cost_usd)}</span>
                <span className="text-muted-foreground">{fmt(agent.total_tokens)} tok</span>
                <span className="text-muted-foreground/50">{agent.calls}×</span>
              </div>
            </div>
            <div className="h-3 w-full rounded bg-muted overflow-hidden mb-0.5">
              <div className="h-full rounded transition-all duration-700" style={{ width: `${costPct}%`, backgroundColor: color }} />
            </div>
            <div className="h-1.5 w-full rounded bg-muted overflow-hidden">
              <div className="h-full rounded transition-all duration-700" style={{ width: `${tokenPct}%`, backgroundColor: color, opacity: 0.3 }} />
            </div>
          </div>
        );
      })}
      <div className="flex items-center gap-4 pt-1 text-[9px] text-muted-foreground">
        <div className="flex items-center gap-1"><div className="h-2.5 w-3.5 rounded" style={{ background: "#34d399" }} />Cost (USD)</div>
        <div className="flex items-center gap-1"><div className="h-1.5 w-3.5 rounded opacity-35" style={{ background: "#34d399" }} />Tokens</div>
      </div>
    </div>
  );
}

function CostTrendChart({ sessions }: { sessions: SessionTokenUsage[] }) {
  const sorted = [...sessions].filter(s => (s.cost_usd ?? 0) > 0).sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? ""));
  if (sorted.length < 2) return null;
  const W = 400, H = 72, PAD = 6;
  const maxCost = Math.max(...sorted.map(s => s.cost_usd));
  const pts = sorted.map((s, i) => ({
    x: PAD + (i / (sorted.length - 1)) * (W - PAD * 2),
    y: PAD + (1 - s.cost_usd / maxCost) * (H - PAD * 2),
  }));
  const polyline = pts.map(p => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  const area = `${PAD},${H} ${polyline} ${W - PAD},${H}`;
  return (
    <div>
      <p className="text-[10px] text-muted-foreground mb-1.5 flex items-center gap-1">
        <TrendingUp className="h-3 w-3" /> Cost trend ({sorted.length} sessions)
      </p>
      <div className="relative rounded-lg border border-border/50 bg-muted/10 overflow-hidden">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-16" preserveAspectRatio="none">
          <defs>
            <linearGradient id="cg" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#34d399" stopOpacity="0.25" />
              <stop offset="100%" stopColor="#34d399" stopOpacity="0" />
            </linearGradient>
          </defs>
          <polygon points={area} fill="url(#cg)" />
          <polyline points={polyline} fill="none" stroke="#34d399" strokeWidth="1.5" strokeLinejoin="round" />
          {pts.map((p, i) => <circle key={i} cx={p.x} cy={p.y} r="2.5" fill="#34d399" />)}
        </svg>
        <div className="absolute top-1.5 right-2 text-[9px] font-mono text-emerald-400">max {fmtUsd(maxCost)}</div>
      </div>
    </div>
  );
}

function SessionRow({ s, isLast }: { s: SessionTokenUsage; isLast: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`border-b border-border/50 ${isLast ? "border-b-0" : ""}`}>
      <button className="w-full flex items-center justify-between px-3 py-2 text-left hover:bg-muted/30 transition-colors" onClick={() => setOpen(o => !o)}>
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-mono text-[10px] text-muted-foreground shrink-0">{String(s.session_id).slice(0, 8)}</span>
          <span className="text-xs text-foreground truncate">{s.task || "—"}</span>
        </div>
        <div className="flex items-center gap-3 shrink-0 ml-2">
          <span className="text-[11px] font-mono text-emerald-400">{fmtUsd(s.cost_usd)}</span>
          <span className="text-[11px] font-mono text-muted-foreground">{fmt(s.total_tokens)} tok</span>
          <span className={`text-[10px] rounded-full px-1.5 py-0.5 border ${s.status === "done" ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-400" : s.status === "error" ? "bg-red-500/10 border-red-500/30 text-red-400" : "bg-primary/10 border-primary/30 text-primary"}`}>{s.status ?? "?"}</span>
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
            {[["Calls", String(s.calls), ""], ["USD", fmtUsd(s.cost_usd), "text-emerald-400"], ["VND", fmtVnd(s.cost_usd), "text-amber-400"]].map(([l, v, c]) => (
              <div key={l} className="rounded bg-muted/40 px-2 py-1 text-center">
                <div className="text-[10px] text-muted-foreground">{l}</div>
                <div className={`text-xs font-mono font-bold ${c}`}>{v}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

type Tab = "overview" | "agents" | "sessions";

export function AnalyticsPanel({ currentSessionId }: { currentSessionId?: string }) {
  const [data, setData]           = useState<AnalyticsData | null>(null);
  const [agentData, setAgentData] = useState<AgentAnalyticsData | null>(null);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState<string | null>(null);
  const [tab, setTab]             = useState<Tab>("overview");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [analytics, agents] = await Promise.all([fetchAnalytics(), fetchAgentAnalytics()]);
      setData(analytics);
      setAgentData(agents);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load analytics");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!currentSessionId) return;
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [currentSessionId, load]);

  const agg      = data?.aggregate;
  const sessions = data?.sessions ?? [];
  const agents   = agentData?.agents ?? [];

  const TABS: { id: Tab; label: string; icon: React.ReactNode }[] = [
    { id: "overview", label: "Overview", icon: <BarChart2 className="h-3 w-3" /> },
    { id: "agents",   label: "By Agent", icon: <Bot className="h-3 w-3" /> },
    { id: "sessions", label: "Sessions", icon: <Database className="h-3 w-3" /> },
  ];

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5 shrink-0">
        <div className="flex items-center gap-2">
          <BarChart2 className="h-4 w-4 text-primary" />
          <span className="text-sm font-semibold text-foreground">Analytics</span>
          {agentData && (
            <span className="text-[10px] text-muted-foreground font-mono">
              {fmtUsd(agentData.total_cost_usd)} · {fmtVnd(agentData.total_cost_usd)} total
            </span>
          )}
        </div>
        <button onClick={load} disabled={loading} className="flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-xs text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-40">
          <RefreshCw className={`h-3 w-3 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-0.5 border-b border-border px-3 py-1.5 shrink-0">
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)} className={`flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors ${tab === t.id ? "bg-primary/20 text-primary" : "text-muted-foreground hover:text-foreground hover:bg-muted"}`}>
            {t.icon}{t.label}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-5">
        {error && <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-400">{error}</div>}

        {/* OVERVIEW */}
        {tab === "overview" && (
          <>
            {agg && (
              <>
                <div>
                  <p className="mb-2 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">All Sessions — Aggregate</p>
                  <div className="grid grid-cols-2 gap-2">
                    <StatCard icon={<Hash className="h-3 w-3" />} label="Total Tokens" value={fmt(agg.total_tokens)} sub={`${fmt(agg.prompt_tokens)} in · ${fmt(agg.output_tokens)} out`} />
                    <StatCard icon={<Zap className="h-3 w-3" />} label="API Calls" value={String(agg.calls)} sub={agg.flash_calls !== undefined ? `${agg.flash_calls} flash · ${agg.pro_calls} pro` : undefined} />
                    <StatCard icon={<DollarSign className="h-3 w-3" />} label="Cost (USD)" value={fmtUsd(agg.cost_usd)} />
                    <StatCard icon={<span className="text-amber-400 text-xs font-bold">₫</span>} label="Cost (VND)" value={fmtVnd(agg.cost_usd)} />
                  </div>
                </div>
                <div>
                  <p className="mb-2 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Token Distribution</p>
                  <div className="space-y-2 rounded-lg border border-border/50 bg-card/40 p-3">
                    <TokenBar label="Prompt (net)" value={Math.max(0, agg.prompt_tokens - agg.cached_tokens)} total={agg.total_tokens} color="#60a5fa" />
                    <TokenBar label="Cached" value={agg.cached_tokens} total={agg.total_tokens} color="#34d399" />
                    <TokenBar label="Output" value={agg.output_tokens} total={agg.total_tokens} color="#a78bfa" />
                  </div>
                </div>
              </>
            )}
            {sessions.length >= 2 && <CostTrendChart sessions={sessions} />}
            {agents.length > 0 && (
              <div>
                <p className="mb-2 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Top Agents by Cost</p>
                <div className="rounded-lg border border-border/50 bg-card/40 p-3 space-y-1.5">
                  {agents.slice(0, 4).map(a => (
                    <div key={a.agent_name} className="flex items-center justify-between text-xs">
                      <div className="flex items-center gap-1.5">
                        <div className="w-2 h-2 rounded-full" style={{ backgroundColor: agentColor(a.agent_name) }} />
                        <span className="capitalize text-foreground">{agentLabel(a.agent_name)}</span>
                      </div>
                      <div className="flex items-center gap-2 font-mono text-[10px]">
                        <span className="text-emerald-400">{fmtUsd(a.cost_usd)}</span>
                        <span className="text-muted-foreground">{fmt(a.total_tokens)} tok</span>
                      </div>
                    </div>
                  ))}
                  {agents.length > 4 && (
                    <button onClick={() => setTab("agents")} className="text-[10px] text-primary hover:underline mt-1 block">
                      +{agents.length - 4} more → view all agents
                    </button>
                  )}
                </div>
              </div>
            )}
          </>
        )}

        {/* AGENTS */}
        {tab === "agents" && (
          <>
            <div>
              <div className="flex items-center justify-between mb-2">
                <p className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Per-Agent Usage</p>
                {agentData && <span className="text-[10px] font-mono text-muted-foreground">Σ {fmtUsd(agentData.total_cost_usd)}</span>}
              </div>
              <div className="rounded-lg border border-border/50 bg-card/40 p-3">
                <AgentBarChart agents={agents} />
              </div>
            </div>
            {agents.length > 0 && (
              <div>
                <p className="mb-2 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Detail Table</p>
                <div className="rounded-lg border border-border/50 overflow-hidden">
                  <table className="w-full text-[11px]">
                    <thead>
                      <tr className="border-b border-border/50 bg-muted/30">
                        {["Agent", "Calls", "Tokens", "Cost USD", "Cost VND"].map((h, i) => (
                          <th key={h} className={`py-1.5 text-muted-foreground font-medium ${i === 0 ? "text-left px-3" : "text-right px-2"}`}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {agents.map((a, i) => (
                        <tr key={a.agent_name} className={`border-b border-border/30 hover:bg-muted/20 ${i % 2 === 0 ? "bg-card/20" : ""}`}>
                          <td className="px-3 py-1.5">
                            <div className="flex items-center gap-1.5">
                              <div className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: agentColor(a.agent_name) }} />
                              <span className="capitalize text-foreground font-medium">{agentLabel(a.agent_name)}</span>
                            </div>
                          </td>
                          <td className="text-right px-2 py-1.5 font-mono text-muted-foreground">{a.calls}</td>
                          <td className="text-right px-2 py-1.5 font-mono text-muted-foreground">{fmt(a.total_tokens)}</td>
                          <td className="text-right px-2 py-1.5 font-mono text-emerald-400">{fmtUsd(a.cost_usd)}</td>
                          <td className="text-right px-3 py-1.5 font-mono text-amber-400">{fmtVnd(a.cost_usd)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        )}

        {/* SESSIONS */}
        {tab === "sessions" && (
          <div>
            <p className="mb-2 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">{sessions.length} session{sessions.length !== 1 ? "s" : ""}</p>
            {sessions.length === 0
              ? <p className="text-xs text-muted-foreground italic">No session data yet.</p>
              : <div className="rounded-lg border border-border/50 overflow-hidden">
                  {sessions.map((s, i) => <SessionRow key={s.session_id} s={s} isLast={i === sessions.length - 1} />)}
                </div>
            }
          </div>
        )}
      </div>
    </div>
  );
}
