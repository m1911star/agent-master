/**
 * StatusBar — fixed footer ticker showing live system metrics.
 */

import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { fmtTokens } from "@/lib/format"

export function StatusBar() {
  const m = useQuery({
    queryKey: ["metrics"],
    queryFn: api.metrics,
    refetchInterval: 2000,
    retry: false,
  })

  const data = m.data
  return (
    <footer className="border-t border-[var(--color-line)] bg-bg-void/80 backdrop-blur-md">
      <div className="overflow-hidden">
        <div className="flex items-center px-6 py-1.5 gap-8 font-mono text-[11px] text-fg-dim whitespace-nowrap">
          <Item label="EVT/RX" value={data?.pipeline.events_received ?? 0} accent="cyan" />
          <Item label="EVT/WR" value={data?.pipeline.events_persisted ?? 0} accent="green" />
          <Item label="BCAST" value={data?.pipeline.broadcasts_sent ?? 0} accent="cyan" />
          <Item label="DROP" value={data?.pipeline.broadcasts_dropped ?? 0}
                accent={(data?.pipeline.broadcasts_dropped ?? 0) > 0 ? "red" : "amber"} />
          <span className="flex-1 h-px bg-[var(--color-line-dim)]" />
          <Item label="CH" value={data?.broker.channels.length ?? 0} accent="amber" />
          <Item label="SUBS" value={data?.broker.subscriber_total ?? 0} accent="amber" />
          <Item label="UP" value={fmtTokens(Math.floor(data?.uptime_seconds ?? 0))} accent="cyan" />
        </div>
      </div>
    </footer>
  )
}

function Item({ label, value, accent }: {
  label: string
  value: number | string
  accent: "cyan" | "amber" | "green" | "red"
}) {
  const color =
    accent === "cyan" ? "var(--color-neon-cyan)" :
    accent === "amber" ? "var(--color-hud-amber)" :
    accent === "green" ? "var(--color-hud-green)" :
    "var(--color-hud-red)"
  return (
    <span className="flex items-center gap-1.5">
      <span className="text-fg-mute tracking-[0.2em]">{label}</span>
      <span style={{ color }}>{value}</span>
    </span>
  )
}
