/**
 * Tiny label/value pair, like HUD readouts.
 * Label is mono small caps; value is large and prominent.
 */

import type { ReactNode } from "react"
import { classNames } from "@/lib/format"

export function HudStat({
  label,
  value,
  accent,
  align = "left",
  className,
}: {
  label: ReactNode
  value: ReactNode
  accent?: "cyan" | "mag" | "vlt" | "amber" | "green" | "red"
  align?: "left" | "right" | "center"
  className?: string
}) {
  const glowCls = accent ? `glow-${accent}` : ""
  const valColor =
    accent === "cyan" ? "text-[var(--color-neon-cyan)]"
    : accent === "mag" ? "text-[var(--color-neon-mag)]"
    : accent === "vlt" ? "text-[var(--color-neon-vlt)]"
    : accent === "amber" ? "text-[var(--color-hud-amber)]"
    : accent === "green" ? "text-[var(--color-hud-green)]"
    : accent === "red" ? "text-[var(--color-hud-red)]"
    : "text-fg"
  const alignCls =
    align === "right" ? "text-right" :
    align === "center" ? "text-center" : "text-left"
  return (
    <div className={classNames("flex flex-col gap-0.5", alignCls, className)}>
      <span className="text-[10px] uppercase tracking-[0.2em] text-fg-mute font-mono">
        {label}
      </span>
      <span className={classNames("text-base font-mono font-medium", valColor, glowCls)}>
        {value}
      </span>
    </div>
  )
}
