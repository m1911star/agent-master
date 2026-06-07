/**
 * AgentCard — the signature element of the home view.
 * Large HUD panel showing one agent's identity + current session + metrics.
 */

import type { Agent, Session } from "@/lib/types"
import { NeonCard } from "@/components/ui/NeonCard"
import { StatusDot } from "@/components/ui/StatusDot"
import { HudStat } from "@/components/ui/HudStat"
import { HudBar } from "@/components/ui/HudBar"
import { fmtCost, fmtTokens, relativeTime, shortId } from "@/lib/format"

const ACCENT_BY_ADAPTER: Record<string, "cyan" | "mag" | "vlt" | "amber"> = {
  opencode: "cyan",
  claude_code: "mag",
  hermes: "vlt",
  codex: "amber",
  omp: "amber",
}

const GLYPH_BY_ADAPTER: Record<string, string> = {
  opencode: "OC",
  claude_code: "CC",
  hermes: "HM",
  codex: "CX",
  omp: "OM",
}

const STATUS_GLOW = {
  busy: "glow-cyan",
  active: "glow-green",
  idle: "",
  paused: "glow-vlt",
  error: "glow-red",
  offline: "",
} as const

export function AgentCard({
  agent,
  sessions,
}: {
  agent: Agent
  sessions: Session[]
}) {
  const ownSessions = sessions.filter((s) => s.agent_id === agent.id)
  const active = ownSessions.filter((s) => s.status === "active")
  const current = active[0] ?? ownSessions[0]
  const accent = ACCENT_BY_ADAPTER[agent.adapter_type] ?? "cyan"
  const glyph = GLYPH_BY_ADAPTER[agent.adapter_type] ?? agent.adapter_type.slice(0, 2).toUpperCase()
  const statusGlow = STATUS_GLOW[agent.status] ?? ""

  // Aggregate cost / tokens across sessions (would be real budget data in V0.2)
  const totalSessions = ownSessions.length
  const activeCount = active.length

  return (
    <NeonCard accent={accent} scanlines className="p-5 flex flex-col gap-4 min-h-[340px]">
      {/* ── header strip ─────────────────────────────────────────── */}
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <AdapterGlyph text={glyph} accent={accent} />
          <div>
            <div className="font-display font-bold text-base tracking-wider">
              {agent.name}
            </div>
            <div className="font-mono text-[10px] text-fg-mute uppercase tracking-[0.2em]">
              {agent.adapter_type} · {shortId(agent.id, 6)}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <StatusDot status={agent.status} pulse={agent.status === "busy"} />
          <span className={`font-mono text-[11px] uppercase tracking-[0.25em] ${statusGlow}`}>
            {agent.status}
          </span>
        </div>
      </div>

      {/* ── current task line ────────────────────────────────────── */}
      <div>
        <div className="text-[10px] uppercase tracking-[0.2em] text-fg-mute font-mono mb-1">
          CURRENT TASK
        </div>
        {current ? (
          <div className="text-sm text-fg leading-snug min-h-[2.5em]">
            {current.summary || (
              <span className="text-fg-mute italic">— no summary —</span>
            )}
          </div>
        ) : (
          <div className="text-fg-mute italic text-sm min-h-[2.5em]">
            standby · no active session
          </div>
        )}
      </div>

      {/* ── HUD stats ─────────────────────────────────────────────── */}
      <div className="grid grid-cols-3 gap-3 pt-2 border-t border-[var(--color-line-dim)]">
        <HudStat label="Sessions" value={totalSessions} accent={accent} />
        <HudStat label="Active" value={activeCount}
                 accent={activeCount > 0 ? "green" : undefined} />
        <HudStat label="Last seen"
                 value={current ? relativeTime(current.last_active_at) : "—"} />
      </div>

      {/* ── capabilities tags ────────────────────────────────────── */}
      {agent.capabilities.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {agent.capabilities.map((c) => (
            <span
              key={c}
              className="font-mono text-[10px] uppercase tracking-wider px-1.5 py-0.5 border border-[var(--color-line)] text-fg-dim"
            >
              {c}
            </span>
          ))}
        </div>
      )}

      {/* ── footer: cost + tokens (placeholder until V0.2 budget) ─ */}
      <div className="mt-auto pt-2 border-t border-[var(--color-line-dim)]">
        <div className="flex items-center justify-between text-[10px] font-mono text-fg-mute uppercase tracking-[0.2em] mb-1">
          <span>Output</span>
          <span>session-aggregated</span>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex-1">
            <HudBar value={Math.min(100, totalSessions * 12)} accent={accent} segments={16} showLabel={false} />
          </div>
          <div className="flex gap-3 text-[11px] font-mono">
            <span className="text-fg-dim">TOK <span className="text-fg">{fmtTokens(estimateTokens(ownSessions))}</span></span>
            <span className="text-fg-dim">COST <span className="text-[var(--color-hud-amber)]">{fmtCost(estimateCost(ownSessions))}</span></span>
          </div>
        </div>
      </div>
    </NeonCard>
  )
}

function AdapterGlyph({ text, accent }: {
  text: string
  accent: "cyan" | "mag" | "vlt" | "amber"
}) {
  const color =
    accent === "cyan" ? "var(--color-neon-cyan)" :
    accent === "mag" ? "var(--color-neon-mag)" :
    accent === "vlt" ? "var(--color-neon-vlt)" :
    "var(--color-hud-amber)"
  return (
    <div
      className="w-10 h-10 flex items-center justify-center font-display font-black text-sm tracking-wider"
      style={{
        background: `${color}10`,
        border: `1px solid ${color}80`,
        color,
        boxShadow: `0 0 12px ${color}40, inset 0 0 12px ${color}10`,
      }}
    >
      {text}
    </div>
  )
}

/** Crude estimation — real numbers land when Run/Budget data wires through. */
function estimateTokens(sessions: Session[]): number {
  return sessions.reduce((sum, s) => {
    const t = (s.meta as Record<string, number | undefined>)
    return sum + (t?.tokens_input ?? 0) + (t?.tokens_output ?? 0) + (t?.message_count ?? 0) * 200
  }, 0)
}

function estimateCost(sessions: Session[]): number {
  return sessions.reduce((sum, s) => {
    const c = (s.meta as { cost?: number }).cost
    return sum + (typeof c === "number" ? c : 0)
  }, 0)
}
