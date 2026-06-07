/**
 * Status dot — small circular indicator, optionally pulsing.
 * Used in agent cards, session rows, header.
 */

import type { AgentStatus, RunStatus, SessionStatus } from "@/lib/types"
import { classNames } from "@/lib/format"

type StatusKey =
  | AgentStatus | SessionStatus | RunStatus
  | "active" | "live" | "ok"

const COLOR: Record<string, string> = {
  busy: "var(--color-neon-cyan)",
  active: "var(--color-hud-green)",
  running: "var(--color-neon-cyan)",
  idle: "var(--color-fg-mute)",
  paused: "var(--color-neon-vlt)",
  error: "var(--color-hud-red)",
  failed: "var(--color-hud-red)",
  offline: "var(--color-fg-mute)",
  pending: "var(--color-hud-amber)",
  interrupted: "var(--color-hud-amber)",
  success: "var(--color-hud-green)",
  closed: "var(--color-fg-mute)",
  ok: "var(--color-hud-green)",
  live: "var(--color-neon-cyan)",
}

export function StatusDot({
  status,
  pulse,
  size = 8,
  className,
}: {
  status: StatusKey | string
  pulse?: boolean
  size?: number
  className?: string
}) {
  const color = COLOR[status] ?? "var(--color-fg-mute)"
  const isLive = pulse ?? (status === "busy" || status === "active" || status === "running" || status === "live")
  return (
    <span
      className={classNames("inline-block rounded-full", isLive && "pulse", className)}
      style={{
        width: size,
        height: size,
        background: color,
        color, // for currentColor in pulse keyframe
        boxShadow: `0 0 ${size}px ${color}`,
      }}
      aria-label={`status: ${status}`}
    />
  )
}
