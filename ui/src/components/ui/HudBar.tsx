/**
 * Bar — segmented progress / utilization indicator (LCARS-style).
 */

import { classNames } from "@/lib/format"

export function HudBar({
  value,
  max = 100,
  accent = "cyan",
  segments = 20,
  showLabel = true,
  className,
}: {
  value: number
  max?: number
  accent?: "cyan" | "mag" | "vlt" | "amber" | "green" | "red"
  segments?: number
  showLabel?: boolean
  className?: string
}) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0
  const lit = Math.round((pct / 100) * segments)
  const color =
    accent === "cyan" ? "var(--color-neon-cyan)" :
    accent === "mag" ? "var(--color-neon-mag)" :
    accent === "vlt" ? "var(--color-neon-vlt)" :
    accent === "amber" ? "var(--color-hud-amber)" :
    accent === "green" ? "var(--color-hud-green)" :
    "var(--color-hud-red)"

  return (
    <div className={classNames("flex items-center gap-2", className)}>
      <div className="flex gap-[2px] flex-1">
        {Array.from({ length: segments }).map((_, i) => (
          <span
            key={i}
            className="flex-1 h-2"
            style={{
              background: i < lit ? color : "var(--color-line-dim)",
              boxShadow: i < lit ? `0 0 4px ${color}` : undefined,
              opacity: i < lit ? 1 : 0.5,
            }}
          />
        ))}
      </div>
      {showLabel && (
        <span className="font-mono text-[10px] text-fg-dim w-10 text-right">
          {pct.toFixed(0)}%
        </span>
      )}
    </div>
  )
}
