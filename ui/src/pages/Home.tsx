/**
 * Home page — agent card grid + topology mini + session list.
 *
 * Layout (per design decision: option A — Agent card grid):
 *   ┌─────────────────────────────────────────────────────────┐
 *   │ FLEET STATUS · 3 active · 12 sessions · $2.47 today    │
 *   ├──────────────────┬──────────────────┬──────────────────┤
 *   │ Agent Card       │ Agent Card       │ Agent Card       │
 *   │ (OpenCode)       │ (Claude Code)    │ (Hermes)         │
 *   ├──────────────────┴──────────────────┼──────────────────┤
 *   │ TOPOLOGY                            │ RECENT SESSIONS  │
 *   │ root → child → grandchild           │ ...              │
 *   └─────────────────────────────────────┴──────────────────┘
 */

import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { AgentCard } from "@/components/agent/AgentCard"
import { TopologyMini } from "@/components/session/TopologyMini"
import { SessionPill } from "@/components/session/SessionPill"
import { NeonCard } from "@/components/ui/NeonCard"
import { HudStat } from "@/components/ui/HudStat"
import { fmtCost } from "@/lib/format"

export function Home() {
  const agentsQ = useQuery({ queryKey: ["agents"], queryFn: api.listAgents })
  const sessionsQ = useQuery({
    queryKey: ["sessions"],
    queryFn: () => api.listSessions({ limit: 50 }),
    refetchInterval: 5000,
  })

  if (agentsQ.isLoading || sessionsQ.isLoading) return <LoadingScreen />
  if (agentsQ.isError || sessionsQ.isError) return <ErrorScreen err={agentsQ.error ?? sessionsQ.error} />

  const agents = agentsQ.data ?? []
  const sessions = sessionsQ.data ?? []
  const active = sessions.filter((s) => s.status === "active")

  // Aggregate cost from session meta
  const totalCost = sessions.reduce((sum, s) => {
    const c = (s.meta as { cost?: number }).cost
    return sum + (typeof c === "number" ? c : 0)
  }, 0)

  return (
    <div className="flex flex-col gap-5 max-w-[1600px] mx-auto">
      {/* ── fleet status strip ────────────────────────────────── */}
      <NeonCard accent="cyan" className="px-5 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-8">
            <div className="font-display font-black text-xl tracking-[0.3em] glow-cyan">
              FLEET STATUS
            </div>
            <div className="font-mono text-xs text-fg-mute uppercase tracking-widest">
              {agents.length} agents online · {active.length} active session{active.length === 1 ? "" : "s"}
            </div>
          </div>
          <div className="flex items-center gap-8">
            <HudStat label="Total Sessions" value={sessions.length} align="right" />
            <HudStat label="Active" value={active.length} accent={active.length > 0 ? "green" : undefined} align="right" />
            <HudStat label="Cost (today)" value={fmtCost(totalCost)} accent="amber" align="right" />
          </div>
        </div>
      </NeonCard>

      {/* ── agent card grid ───────────────────────────────────── */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
        {agents.map((a) => (
          <AgentCard key={a.id} agent={a} sessions={sessions} />
        ))}
      </div>

      {/* ── lower row: topology + recent sessions ──────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-[2fr_1fr] gap-5">
        <TopologyMini sessions={sessions} />
        <NeonCard accent="amber" className="p-5">
          <div className="text-[10px] uppercase tracking-[0.25em] text-fg-mute font-mono mb-3">
            <span className="text-[var(--color-hud-amber)] glow-amber">▸</span> RECENT SESSIONS
          </div>
          <div className="flex flex-col gap-1.5 max-h-[400px] overflow-y-auto">
            {sessions.slice(0, 12).map((s) => (
              <SessionPill key={s.id} session={s} />
            ))}
          </div>
        </NeonCard>
      </div>
    </div>
  )
}

function LoadingScreen() {
  return (
    <div className="flex items-center justify-center min-h-[60vh] font-mono text-sm text-fg-dim">
      <span className="glow-cyan">▸ ESTABLISHING UPLINK ...</span>
    </div>
  )
}

function ErrorScreen({ err }: { err: unknown }) {
  const msg = err instanceof Error ? err.message : String(err)
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-3 font-mono text-sm">
      <div className="glow-red text-[var(--color-hud-red)] text-base">⚠ LINK FAILURE</div>
      <div className="text-fg-mute">{msg}</div>
      <div className="text-fg-mute text-xs">
        is the daemon running? try: <span className="text-fg">agent-master start</span>
      </div>
    </div>
  )
}
