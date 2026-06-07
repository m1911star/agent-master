/**
 * Header — top bar with brand, live status, and time.
 */

import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { StatusDot } from "@/components/ui/StatusDot"

function useNow(intervalMs = 1000) {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), intervalMs)
    return () => clearInterval(id)
  }, [intervalMs])
  return now
}

export function Header() {
  const now = useNow()
  const health = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    refetchInterval: 5000,
    retry: false,
  })

  const online = !!health.data && !health.isError
  const stardate = formatStardate(now)

  return (
    <header className="relative border-b border-[var(--color-line)] bg-bg-deep/90 backdrop-blur-md">
      {/* glow line under header */}
      <div className="absolute bottom-0 left-0 right-0 h-px"
           style={{ background: "linear-gradient(90deg, transparent, var(--color-neon-cyan), transparent)", opacity: 0.4 }} />
      <div className="flex items-center justify-between px-6 py-3">
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-3">
            <BrandMark />
            <div className="flex flex-col leading-tight">
              <span className="font-display font-black text-lg tracking-[0.25em] glow-cyan">
                AGENT//MASTER
              </span>
              <span className="font-mono text-[10px] text-fg-mute tracking-[0.3em] uppercase">
                Local Mission Control · v0.1
              </span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-6">
          {/* link status */}
          <div className="flex items-center gap-2 font-mono text-xs">
            <StatusDot status={online ? "live" : "error"} pulse={online} size={6} />
            <span className="uppercase tracking-[0.2em] text-fg-dim">
              {online ? "LINK · OK" : "LINK · LOST"}
            </span>
          </div>
          {/* uptime */}
          {health.data && (
            <div className="font-mono text-xs text-fg-mute">
              UP {fmtUptime(health.data.uptime_seconds)}
            </div>
          )}
          {/* stardate */}
          <div className="font-mono text-xs text-[var(--color-neon-cyan)] tracking-wider">
            {stardate}
          </div>
        </div>
      </div>
    </header>
  )
}

function BrandMark() {
  return (
    <svg width="32" height="32" viewBox="0 0 32 32" fill="none" aria-hidden>
      <defs>
        <linearGradient id="bm" x1="0" y1="0" x2="32" y2="32">
          <stop offset="0" stopColor="var(--color-neon-cyan)" />
          <stop offset="1" stopColor="var(--color-neon-mag)" />
        </linearGradient>
      </defs>
      {/* hex outline */}
      <polygon
        points="16,2 28,9 28,23 16,30 4,23 4,9"
        stroke="url(#bm)"
        strokeWidth="1.4"
        fill="none"
      />
      {/* inner triangle (orbit-glyph) */}
      <polygon
        points="16,9 22,21 10,21"
        stroke="var(--color-neon-cyan)"
        strokeWidth="1.2"
        fill="none"
        opacity="0.7"
      />
      {/* center dot */}
      <circle cx="16" cy="18" r="1.5" fill="var(--color-neon-cyan)" />
    </svg>
  )
}

function formatStardate(d: Date): string {
  // Star-Trek-ish: YYYY.DDD HH:MM:SS UTC
  const year = d.getUTCFullYear()
  const start = Date.UTC(year, 0, 0)
  const day = Math.floor((d.getTime() - start) / 86_400_000)
  const hh = String(d.getUTCHours()).padStart(2, "0")
  const mm = String(d.getUTCMinutes()).padStart(2, "0")
  const ss = String(d.getUTCSeconds()).padStart(2, "0")
  return `${year}.${String(day).padStart(3, "0")} ${hh}:${mm}:${ss}Z`
}

function fmtUptime(s: number): string {
  if (s < 60) return `${Math.floor(s)}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  return `${h}h${m % 60}m`
}
