/**
 * NeonCard — HUD panel with corner brackets, optional glow accent.
 * The signature container of the cyberpunk aesthetic.
 */

import type { CSSProperties, ReactNode } from "react"
import { classNames } from "@/lib/format"

type Accent = "cyan" | "mag" | "vlt" | "amber" | "green" | "red" | "none"

const accentColor: Record<Accent, string> = {
  cyan: "var(--color-neon-cyan)",
  mag: "var(--color-neon-mag)",
  vlt: "var(--color-neon-vlt)",
  amber: "var(--color-hud-amber)",
  green: "var(--color-hud-green)",
  red: "var(--color-hud-red)",
  none: "var(--color-line)",
}

export function NeonCard({
  children,
  accent = "cyan",
  scanlines = false,
  className,
  style,
}: {
  children: ReactNode
  accent?: Accent
  scanlines?: boolean
  className?: string
  style?: CSSProperties
}) {
  const c = accentColor[accent]
  return (
    <div
      className={classNames(
        "relative bg-bg-void/80 backdrop-blur-sm border",
        scanlines && "scanlines",
        className,
      )}
      style={{
        borderColor: "var(--color-line)",
        boxShadow: accent === "none"
          ? undefined
          : `inset 0 0 24px ${c}10, 0 0 1px ${c}80`,
        ...style,
      }}
    >
      {/* 4 corner brackets */}
      <span className="absolute top-0 left-0 w-3 h-3" aria-hidden
            style={{ borderTop: `1px solid ${c}`, borderLeft: `1px solid ${c}` }} />
      <span className="absolute top-0 right-0 w-3 h-3" aria-hidden
            style={{ borderTop: `1px solid ${c}`, borderRight: `1px solid ${c}` }} />
      <span className="absolute bottom-0 left-0 w-3 h-3" aria-hidden
            style={{ borderBottom: `1px solid ${c}`, borderLeft: `1px solid ${c}` }} />
      <span className="absolute bottom-0 right-0 w-3 h-3" aria-hidden
            style={{ borderBottom: `1px solid ${c}`, borderRight: `1px solid ${c}` }} />
      {children}
    </div>
  )
}
