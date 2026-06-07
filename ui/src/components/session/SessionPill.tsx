/**
 * SessionPill — compact session indicator row.
 */

import type { Session } from "@/lib/types"
import { StatusDot } from "@/components/ui/StatusDot"
import { relativeTime } from "@/lib/format"

export function SessionPill({ session }: { session: Session }) {
  return (
    <div className="flex items-center gap-2 py-1 px-2 border border-[var(--color-line-dim)] hover:border-[var(--color-line-hot)] transition-colors text-xs">
      <StatusDot status={session.status} size={6} />
      <span className="font-mono text-fg-dim flex-1 truncate">
        {(session.summary || session.external_id || "—").slice(0, 60)}
      </span>
      <span className="font-mono text-fg-mute text-[10px]">
        {relativeTime(session.last_active_at)}
      </span>
    </div>
  )
}
