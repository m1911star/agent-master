/**
 * Mini topology panel — shows session parent/child edges as a list of arrows.
 * V0.3 will upgrade this to a React Flow canvas; for V0.1 the list view is
 * enough to verify the relationships exist.
 */

import type { Session } from "@/lib/types"
import { NeonCard } from "@/components/ui/NeonCard"
import { StatusDot } from "@/components/ui/StatusDot"
import { shortId } from "@/lib/format"

export function TopologyMini({ sessions }: { sessions: Session[] }) {
  // Build adjacency: parent -> children
  const byId = new Map(sessions.map((s) => [s.id, s]))
  const children = new Map<string, Session[]>()
  for (const s of sessions) {
    if (s.parent_session_id) {
      children.set(
        s.parent_session_id,
        [...(children.get(s.parent_session_id) ?? []), s],
      )
    }
  }
  const roots = sessions.filter((s) => !s.parent_session_id || !byId.has(s.parent_session_id))
  const linkedRoots = roots.filter((r) => children.has(r.id))

  if (linkedRoots.length === 0) {
    return (
      <NeonCard accent="vlt" className="p-5 min-h-[200px]">
        <SectionTitle>TOPOLOGY</SectionTitle>
        <div className="text-fg-mute text-sm font-mono mt-4">
          // no spawn relationships in current data slice
        </div>
      </NeonCard>
    )
  }

  return (
    <NeonCard accent="vlt" className="p-5">
      <SectionTitle>TOPOLOGY · SPAWN GRAPH</SectionTitle>
      <div className="mt-4 flex flex-col gap-4 font-mono text-sm">
        {linkedRoots.map((root) => (
          <div key={root.id} className="flex flex-col gap-1">
            <SessionLine session={root} />
            {(children.get(root.id) ?? []).map((c) => (
              <div key={c.id} className="ml-6 flex items-center gap-2 text-fg-dim">
                <span className="text-[var(--color-neon-vlt)]">└─►</span>
                <SessionLine session={c} dim />
              </div>
            ))}
          </div>
        ))}
      </div>
    </NeonCard>
  )
}

function SessionLine({ session, dim }: { session: Session; dim?: boolean }) {
  return (
    <div className={`flex items-center gap-2 ${dim ? "text-fg-dim" : "text-fg"}`}>
      <StatusDot status={session.status} size={6} />
      <span className="font-mono text-[11px] text-fg-mute">[{shortId(session.id, 6)}]</span>
      <span className="truncate flex-1">
        {session.summary || session.external_id || "—"}
      </span>
    </div>
  )
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[10px] uppercase tracking-[0.25em] text-fg-mute font-mono">
      <span className="text-[var(--color-neon-vlt)] glow-vlt">▸</span> {children}
    </div>
  )
}
